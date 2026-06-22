"""The action executor — the single chokepoint for anything that mutates.

Every action, whether proposed by a rule, suggested by the LLM, or triggered
by a human, flows through here. The gates are enforced in this exact execution
order in ``_run`` (a row is audited no matter where it stops):

  0. Approval gating               -> handled *before* this method by
                                      propose/approve. auto_execute checks and
                                      human-approved actions reach _run;
                                      everything else waits in the queue.
  1. Resolve dynamic params        -> e.g. pick a migration target; pure
                                      computation, mutates nothing.
  2. Global kill switch (paused)   -> nothing executes; still audited.
  3. Allow-list                    -> only allow-listed guests may *auto*-act;
                                      a human approval is the stronger gate and
                                      may override it.
  4. Cooldown + hourly rate limit  -> refuse if exceeded.
  5. Dry-run flag (default ON)     -> simulate, never call mutating client APIs.
  6. Audit everything              -> every proposed/executed/rejected/blocked
                                      action gets a row with before/after and a
                                      reversibility note.

Migration safety (documented; dry-run tonight): live migration needs a valid,
online target and ideally shared storage; with local disks Proxmox does a
storage migration (slower, copies disk). We refuse when no eligible target is
found. HA-managed guests should be excluded from auto-action via the allow-list
so Steward and the HA manager don't fight.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from steward.config import Settings
from steward.models import (
    ActionRecord,
    ActionRequest,
    ActionStatus,
    ActionType,
    ClusterSnapshot,
    Severity,
    now_ts,
)
from steward.actions.planner import busiest_vm_on_node, plan_migration_target
from steward.notify import Notifier
from steward.proxmox.base import ProxmoxClient, ProxmoxError
from steward.store.db import Store

log = logging.getLogger("steward.actions")


class GuardrailError(RuntimeError):
    """Raised internally when a guardrail blocks an action."""


@dataclass
class RuntimeFlags:
    """Live view of the safety flags. Supplied by the runtime so the executor
    always sees the current (possibly operator-toggled) values."""

    paused: Callable[[], bool]
    dry_run: Callable[[], bool]
    allowlist: Callable[[], list[int]]
    cooldown_s: Callable[[], float]
    max_per_hour: Callable[[], int]
    snapshot: Callable[[], Optional[ClusterSnapshot]]


class ActionExecutor:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        client: ProxmoxClient,
        notifier: Notifier,
        flags: RuntimeFlags,
    ) -> None:
        self.settings = settings
        self.store = store
        self.client = client
        self.notifier = notifier
        self.flags = flags

    # ------------------------------------------------------------------ #
    # Approval queue
    # ------------------------------------------------------------------ #
    def propose(self, req: ActionRequest) -> ActionRecord:
        """Record an action awaiting human approval (the approval queue)."""
        rec = ActionRecord(
            type=req.type, params=dict(req.params), reason=req.reason, source=req.source,
            check_id=req.check_id, status=ActionStatus.proposed, dry_run=self.flags.dry_run(),
            reversibility=_reversibility(req.type),
        )
        rec.id = self.store.insert_action(rec)
        log.info("proposed action #%s: %s %s", rec.id, rec.type.value, rec.params)
        return rec

    async def approve(self, action_id: int) -> ActionRecord:
        rec = self.store.get_action(action_id)
        if rec is None:
            raise GuardrailError(f"no such action: {action_id}")
        if rec.status != ActionStatus.proposed:
            raise GuardrailError(f"action #{action_id} is {rec.status.value}, not proposed")
        req = ActionRequest(
            type=rec.type, params=rec.params, reason=rec.reason, source=rec.source,
            check_id=rec.check_id, auto_execute=True,
        )
        return await self._run(req, existing=rec, approved_by_human=True)

    def reject(self, action_id: int) -> ActionRecord:
        rec = self.store.get_action(action_id)
        if rec is None:
            raise GuardrailError(f"no such action: {action_id}")
        rec.status = ActionStatus.rejected
        rec.resolved_at = now_ts()
        rec.outcome = "Rejected by operator"
        self.store.update_action(rec)
        return rec

    # ------------------------------------------------------------------ #
    # Direct execution (auto path or manual immediate)
    # ------------------------------------------------------------------ #
    async def run(self, req: ActionRequest, *, approved_by_human: bool = False) -> ActionRecord:
        return await self._run(req, existing=None, approved_by_human=approved_by_human)

    async def _run(
        self,
        req: ActionRequest,
        *,
        existing: Optional[ActionRecord],
        approved_by_human: bool,
    ) -> ActionRecord:
        rec = existing or ActionRecord(
            type=req.type, params=dict(req.params), reason=req.reason, source=req.source,
            check_id=req.check_id, reversibility=_reversibility(req.type),
        )
        if rec.id is None:
            rec.id = self.store.insert_action(rec)

        # --- resolve dynamic params (e.g. migration target) before guardrails
        try:
            self._resolve_params(rec)
        except GuardrailError as exc:
            return self._finish(rec, ActionStatus.blocked, str(exc))

        # --- Guardrail 1: kill switch
        if self.flags.paused():
            return self._finish(rec, ActionStatus.blocked, "Kill switch engaged (paused)")

        # --- Guardrail 3: allow-list (auto path only; humans may override)
        if not approved_by_human:
            vmid = rec.params.get("vmid")
            if vmid is not None and int(vmid) not in self.flags.allowlist():
                return self._finish(
                    rec, ActionStatus.blocked,
                    f"VM {vmid} not in auto-action allow-list (suggest-only)",
                )

        # --- Guardrail 4: cooldown + rate limit
        ok, why = self._rate_ok(rec.type)
        if not ok:
            return self._finish(rec, ActionStatus.blocked, why)

        # --- Guardrail 2: dry-run vs real
        dry = self.flags.dry_run()
        rec.dry_run = dry
        rec.before = self._capture(rec)
        try:
            if dry:
                rec.after = self._simulate(rec)
                outcome = "DRY-RUN: simulated, no client call made"
            else:
                await self._perform(rec)
                rec.after = await self._capture_live(rec)
                outcome = "Executed against client"
        except (ProxmoxError, GuardrailError) as exc:
            return self._finish(rec, ActionStatus.failed, f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - audit unexpected failures too
            log.exception("action #%s failed", rec.id)
            return self._finish(rec, ActionStatus.failed, f"unexpected: {exc}")

        return self._finish(rec, ActionStatus.executed, outcome)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _resolve_params(self, rec: ActionRecord) -> None:
        """Fill in dynamic parameters (notably migration target selection)."""
        if rec.type != ActionType.migrate:
            return
        snap = self.flags.snapshot()
        params = rec.params
        # strategy: find busiest VM on a pressured node and a good target
        if "vmid" not in params and params.get("strategy") and snap is not None:
            node = params.get("node")
            if node:
                vmid = busiest_vm_on_node(snap, node)
                if vmid is None:
                    raise GuardrailError(f"no movable guest on node {node}")
                params["vmid"] = vmid
        if "target" not in params and snap is not None and "vmid" in params:
            dimension = "mem" if "mem" in str(params.get("strategy", "")) else "cpu"
            target = plan_migration_target(snap, int(params["vmid"]), dimension=dimension)
            if target is None:
                raise GuardrailError("no eligible target node for migration")
            params["target"] = target

    def _rate_ok(self, atype: ActionType) -> tuple[bool, str]:
        cooldown = self.flags.cooldown_s()
        max_per_hour = self.flags.max_per_hour()
        now = now_ts()
        recent = self.store.recent_actions_of_type(atype.value, now - 3600)
        if len(recent) >= max_per_hour:
            return False, f"rate limit: {len(recent)} {atype.value} actions in last hour"
        if recent:
            last = recent[0].resolved_at or recent[0].ts
            if (now - last) < cooldown:
                wait = cooldown - (now - last)
                return False, f"cooldown: {atype.value} again in {wait:.0f}s"
        return True, ""

    def _capture(self, rec: ActionRecord) -> dict:
        snap = self.flags.snapshot()
        vmid = rec.params.get("vmid")
        if snap is None or vmid is None:
            return {}
        vm = next((v for v in snap.vms if v.vmid == int(vmid)), None)
        if vm is None:
            return {}
        return {"vmid": vm.vmid, "name": vm.name, "node": vm.node,
                "status": vm.status.value, "mem_max_mb": vm.mem_max_mb}

    async def _capture_live(self, rec: ActionRecord) -> dict:
        """Re-read fresh state from the client after a real mutation, so the
        audit 'after' reflects what actually happened (not the cached poll)."""
        vmid = rec.params.get("vmid")
        if vmid is None:
            return {"notified": True} if rec.type == ActionType.notify else {}
        try:
            snap = await self.client.get_cluster_resources()
        except Exception:
            return self._capture(rec)
        vm = next((v for v in snap.vms if v.vmid == int(vmid)), None)
        if vm is None:
            return {}
        return {"vmid": vm.vmid, "name": vm.name, "node": vm.node,
                "status": vm.status.value, "mem_max_mb": vm.mem_max_mb}

    def _simulate(self, rec: ActionRecord) -> dict:
        after = dict(self._capture(rec))
        if rec.type == ActionType.migrate:
            after["node"] = rec.params.get("target", after.get("node"))
        elif rec.type == ActionType.power:
            state = rec.params.get("state")
            after["status"] = {"start": "running", "reboot": "running",
                               "stop": "stopped", "shutdown": "stopped"}.get(state, "unknown")
        elif rec.type == ActionType.balloon:
            after["mem_max_mb"] = rec.params.get("mb", after.get("mem_max_mb"))
        elif rec.type == ActionType.notify:
            after = {"notified": True}
        return after

    async def _perform(self, rec: ActionRecord) -> None:
        p = rec.params
        if rec.type == ActionType.migrate:
            await self.client.migrate_vm(int(p["vmid"]), p["target"], bool(p.get("online", True)))
        elif rec.type == ActionType.power:
            await self.client.set_vm_power(int(p["vmid"]), p["state"])
        elif rec.type == ActionType.balloon:
            await self.client.set_balloon(int(p["vmid"]), int(p["mb"]))
        elif rec.type == ActionType.notify:
            await self.notifier.send(
                p.get("title", "Steward"), p.get("message", rec.reason),
                Severity(p.get("severity", "info")),
            )

    def _finish(self, rec: ActionRecord, status: ActionStatus, outcome: str) -> ActionRecord:
        rec.status = status
        rec.outcome = outcome
        rec.resolved_at = now_ts()
        self.store.update_action(rec)
        log.info("action #%s -> %s: %s", rec.id, status.value, outcome)
        return rec


def _reversibility(atype: ActionType) -> str:
    return {
        ActionType.migrate: "Reversible: migrate the guest back to the source node.",
        ActionType.power: "Partially reversible: power state can be toggled again, "
                          "but an in-guest stop may lose unsaved work.",
        ActionType.balloon: "Reversible: set the balloon target back to the prior value.",
        ActionType.notify: "Reversible: a notification has no infrastructure effect.",
    }.get(atype, "Unknown reversibility.")
