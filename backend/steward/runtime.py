"""The Steward runtime: the service container and the collector loop.

Owns the singletons (store, Proxmox client, rule engine, executor, notifier),
holds the live in-memory ring buffer + latest snapshot, manages the operator
safety flags (paused / dry-run / allow-list) with the store as source of truth,
and runs the deterministic polling loop. No LLM is touched here.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
from typing import Optional

from steward.actions.executor import ActionExecutor, RuntimeFlags
from steward.balancer import blended_imbalance, suggest_balancing_migrations, trending_up
from steward.checks.probes import run_probe
from steward.escalate import build_escalator
from steward.incidents import IncidentTracker
from steward.checks.schema import Check, ProbeType
from steward.config import Settings
from steward.models import (
    ActionRequest,
    ActionType,
    ClusterSnapshot,
    Event,
    Severity,
    now_ts,
)
from steward.notify import build_notifier
from steward.proxmox.factory import build_client
from steward.rules.builtins import builtin_checks
from steward.rules.engine import RuleEngine
from steward.store.db import Store

log = logging.getLogger("steward.runtime")


class Steward:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.client = build_client(settings)
        self.engine = RuleEngine()
        self.notifier = build_notifier(settings)
        self.escalator = build_escalator(settings)
        self.incidents = IncidentTracker(
            min_occurrences=settings.escalation_min_occurrences,
            min_age_s=settings.escalation_min_age_s,
            cooldown_s=settings.escalation_cooldown_s,
            ttl_s=settings.escalation_ttl_s,
        )
        self.ring: collections.deque[ClusterSnapshot] = collections.deque(
            maxlen=settings.ring_buffer_size
        )
        self.latest: Optional[ClusterSnapshot] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._demo_task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self._cycle = 0

        self._seed_state()
        self._seed_builtins()

        self.executor = ActionExecutor(
            settings, self.store, self.client, self.notifier,
            RuntimeFlags(
                paused=self.is_paused,
                dry_run=self.is_dry_run,
                allowlist=self.allowlist,
                cooldown_s=lambda: settings.action_cooldown_s,
                max_per_hour=lambda: settings.action_max_per_hour,
                snapshot=lambda: self.latest,
            ),
        )

    # ------------------------------------------------------------------ #
    # State seeding & flags (store is source of truth, seeded from settings)
    # ------------------------------------------------------------------ #
    def _seed_state(self) -> None:
        if self.store.get_state("paused") is None:
            self.store.set_state("paused", json.dumps(self.settings.paused))
        if self.store.get_state("dry_run") is None:
            self.store.set_state("dry_run", json.dumps(self.settings.dry_run))
        if self.store.get_state("allowlist") is None:
            self.store.set_state("allowlist", json.dumps(self.settings.action_allowlist))

    def _seed_builtins(self) -> None:
        existing = {c.id for c in self.store.list_checks()}
        for chk in builtin_checks():
            if chk.id not in existing:
                self.store.upsert_check(chk)

    def is_paused(self) -> bool:
        return json.loads(self.store.get_state("paused") or "false")

    def is_dry_run(self) -> bool:
        return json.loads(self.store.get_state("dry_run") or "true")

    def allowlist(self) -> list[int]:
        return json.loads(self.store.get_state("allowlist") or "[]")

    def set_paused(self, value: bool) -> None:
        self.store.set_state("paused", json.dumps(bool(value)))
        log.warning("kill switch %s", "ENGAGED (paused)" if value else "released (running)")

    def set_dry_run(self, value: bool) -> None:
        self.store.set_state("dry_run", json.dumps(bool(value)))
        log.warning("dry-run %s", "ON" if value else "OFF — actions will hit the client!")

    def set_allowlist(self, vmids: list[int]) -> None:
        self.store.set_state("allowlist", json.dumps([int(v) for v in vmids]))

    # ------------------------------------------------------------------ #
    # Pub/sub for the websocket
    # ------------------------------------------------------------------ #
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, payload: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # slow consumer; drop the frame rather than block the loop
                pass

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(), name="steward-collector")
            log.info("collector started (interval=%ss)", self.settings.poll_interval_s)
        if self.settings.demo_mode and self._demo_task is None:
            from steward.demo import run_demo

            self._demo_task = asyncio.create_task(
                run_demo(self.client, self._stopping), name="steward-demo"
            )

    async def stop(self) -> None:
        self._stopping.set()
        for attr in ("_task", "_demo_task"):
            task = getattr(self, attr)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, attr, None)
        await self.client.close()
        self.store.close()

    async def poll_once(self) -> ClusterSnapshot:
        """Run a single collection cycle. Returns the snapshot. Used by the
        loop and directly by tests."""
        snap = await self.client.get_cluster_resources()
        self.latest = snap
        self.ring.append(snap)
        await asyncio.to_thread(self.store.insert_snapshot, snap)

        checks = [c for c in self.store.list_checks() if c.enabled]
        events = self._run_checks(checks, snap)
        events += await self._run_active_probes(checks, snap)

        if self.settings.predictive_enabled:
            events += self._run_predictions(snap)

        for ev in events:
            ev.id = await asyncio.to_thread(self.store.insert_event, ev)
            if ev.severity in (Severity.warning, Severity.critical):
                await self.notifier.send(f"[{ev.severity.value}] {ev.check_name}", ev.message,
                                         ev.severity)
            self.incidents.record(check_id=ev.check_id, check_name=ev.check_name,
                                  target=ev.target, severity=ev.severity, ts=ev.ts)
            await self._maybe_suggest(ev, checks)

        # Tier-0 autonomous balancer runs its own deterministic step: it executes
        # moves directly (never via _maybe_suggest, which would double-fire).
        balancer_events = await self._run_balancer(checks, snap)
        events += balancer_events

        # Tier-2: page a human (Claude Code) for repeated, unresolved incidents.
        await self._run_escalation(snap)

        self._broadcast({
            "type": "tick",
            "snapshot": snap.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events],
            "flags": self.flags_dict(),
        })

        self._cycle += 1
        if self._cycle % 30 == 0:
            await asyncio.to_thread(self._prune)
        return snap

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - never let one bad cycle kill the loop
                log.exception("collector cycle failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.settings.poll_interval_s)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ #
    # Check evaluation
    # ------------------------------------------------------------------ #
    def _run_checks(self, checks: list[Check], snap: ClusterSnapshot) -> list[Event]:
        proxmox_checks = [c for c in checks if c.probe_type == ProbeType.proxmox_metric]
        return self.engine.evaluate(proxmox_checks, snap)

    async def _run_active_probes(self, checks: list[Check], snap: ClusterSnapshot) -> list[Event]:
        active = [c for c in checks if c.probe_type != ProbeType.proxmox_metric]
        events: list[Event] = []
        for chk in active:
            try:
                ev = await asyncio.wait_for(run_probe(chk, snap), timeout=20.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("probe %s failed: %s", chk.id, exc)
                continue
            if ev:
                key = (ev.check_id, ev.target)
                last = self.engine._last_fire.get(key)
                if last is not None and (now_ts() - last) < chk.cooldown_s:
                    continue
                self.engine._last_fire[key] = now_ts()
                events.append(ev)
        return events

    def _run_predictions(self, snap: ClusterSnapshot) -> list[Event]:
        """Emit info-level 'projected to exceed' events using the ring buffer."""
        from steward.rules.predict import forecast_threshold

        if len(self.ring) < 4:
            return []
        threshold = self.settings.predictive_threshold
        events: list[Event] = []
        for metric in ("cpu_pct", "mem_pct"):
            for node in snap.nodes:
                series = [
                    next((n.__getattribute__(metric) for n in s.nodes if n.node == node.node), None)
                    for s in self.ring
                ]
                series = [v for v in series if v is not None]
                fc = forecast_threshold(
                    series, threshold=threshold, dt_s=self.settings.poll_interval_s,
                    lookahead_s=self.settings.predictive_lookahead_s,
                )
                if fc is None or not fc.will_cross or fc.current >= threshold:
                    continue  # already-over is the reactive check's job
                check_id = f"predictive.node_{metric}"
                key = (check_id, node.node)
                last = self.engine._last_fire.get(key)
                if last is not None and (snap.ts - last) < self.settings.predictive_cooldown_s:
                    continue
                self.engine._last_fire[key] = snap.ts
                mins = (fc.seconds_to_threshold or 0) / 60.0
                events.append(Event(
                    ts=snap.ts, check_id=check_id, check_name=f"Predicted {metric} pressure",
                    severity=Severity.info, target=node.node, value=fc.current,
                    message=(f"Node {node.node} {metric} trending up "
                             f"({fc.current:.0f}% now); projected to exceed {threshold:.0f}% "
                             f"in ~{mins:.0f} min"),
                    context={"metric": metric, "current": fc.current,
                             "slope_per_s": fc.slope_per_s, "threshold": threshold},
                ))
        return events

    async def _run_balancer(self, checks: list[Check], snap: ClusterSnapshot) -> list[Event]:
        """Tier-0 deterministic load balancer.

        Driven by the ``builtin.autonomous_balancer`` check (disabled by default,
        so this is dormant unless an operator enables it). When the blended
        CPU+mem imbalance exceeds the check's threshold *and* is trending up, it
        proposes migrations and runs them through the guarded executor — which
        still enforces kill-switch, allow-list, cooldown, and dry-run. No LLM.
        """
        bal = next((c for c in checks if c.id == "builtin.autonomous_balancer"), None)
        if bal is None:  # check disabled or removed -> balancer off
            return []

        s = self.settings
        blended = blended_imbalance(snap, s.balancer_weight_cpu, s.balancer_weight_mem)
        if blended <= bal.condition.threshold:
            return []
        if s.balancer_require_trend and not trending_up(
            list(self.ring), s.balancer_weight_cpu, s.balancer_weight_mem
        ):
            return []

        # Per-balancer cooldown (reuse the engine's fire bookkeeping).
        key = (bal.id, "cluster")
        last = self.engine._last_fire.get(key)
        if last is not None and (snap.ts - last) < bal.cooldown_s:
            return []
        # Let a recent migration settle before initiating another.
        if self.store.recent_actions_of_type(
            ActionType.migrate.value, snap.ts - s.balancer_migration_settle_s
        ):
            return []

        moves = suggest_balancing_migrations(
            snap,
            w_cpu=s.balancer_weight_cpu, w_mem=s.balancer_weight_mem,
            max_target_pct=s.balancer_max_target_pct,
            min_improvement=s.balancer_min_improvement,
            max_moves=s.balancer_max_moves_per_cycle,
        )
        if not moves:
            return []

        self.engine._last_fire[key] = snap.ts
        summary = ", ".join(f"{m.name}({m.vmid}) {m.source}->{m.target}" for m in moves)
        ev = Event(
            ts=snap.ts, check_id=bal.id, check_name=bal.name, severity=bal.severity,
            target="cluster", value=blended,
            message=f"Cluster imbalance {blended:.1f} > {bal.condition.threshold:.0f}; "
                    f"rebalancing: {summary}",
            context={"blended_imbalance": blended,
                     "moves": [{"vmid": m.vmid, "source": m.source, "target": m.target,
                                "improvement": round(m.improvement, 2)} for m in moves]},
        )
        ev.id = await asyncio.to_thread(self.store.insert_event, ev)
        if ev.severity in (Severity.warning, Severity.critical):
            await self.notifier.send(f"[{ev.severity.value}] {ev.check_name}", ev.message, ev.severity)

        for m in moves:
            req = ActionRequest(
                type=ActionType.migrate,
                params={"vmid": m.vmid, "target": m.target, "node": m.source,
                        "strategy": "autonomous_balance"},
                reason=f"{bal.name}: imbalance {blended:.1f}, "
                       f"move {m.name}({m.vmid}) {m.source}->{m.target} "
                       f"(-{m.improvement:.1f})",
                source="rule", check_id=bal.id, auto_execute=True,
            )
            await self.executor.run(req)
        return [ev]

    async def _run_escalation(self, snap: ClusterSnapshot) -> None:
        """Tier-2 escalation: hand repeated, unresolved incidents to Claude Code.

        Off unless an escalation webhook is configured. The incident tracker only
        surfaces a (check, target) that has fired enough times over enough time
        and isn't in cooldown, so a transient blip never pages anyone.
        """
        if not self.settings.escalation_enabled:
            return
        now = snap.ts
        for inc in self.incidents.due(now):
            try:
                await self.escalator.escalate(self._escalation_payload(inc, snap))
            except Exception:  # noqa: BLE001 - escalation must never break the loop
                log.exception("escalation failed for %s/%s", inc.check_id, inc.target)
                continue
            self.incidents.mark_escalated(inc.key(), now)
            log.warning("escalated incident %s/%s (count=%s, age=%.0fs) to external agent",
                        inc.check_id, inc.target, inc.count, inc.age_s)
        self.incidents.prune(now)

    def _escalation_payload(self, inc, snap: ClusterSnapshot) -> dict:
        recent = self.store.list_events(check_id=inc.check_id, limit=20)
        return {
            "kind": "steward.incident",
            "incident": {
                "check_id": inc.check_id, "check_name": inc.check_name,
                "target": inc.target, "severity": inc.severity.value,
                "count": inc.count, "first_ts": inc.first_ts, "last_ts": inc.last_ts,
                "age_s": round(inc.age_s, 1),
            },
            "snapshot": snap.model_dump(mode="json"),
            "recent_events": [e.model_dump(mode="json") for e in recent
                              if e.target == inc.target][:10],  # fetch 20, keep ≤10 for this target
            "note": ("Unresolved incident escalated by Steward. Investigate via the read API "
                     "and propose remediation into the approval queue — do not bypass the "
                     "executor guardrails."),
        }

    def diff(self, since_s: float = 300.0) -> dict:
        """What changed over roughly the last ``since_s`` seconds.

        Compares the latest snapshot against the ring-buffer snapshot closest to
        (and at least as old as) the cutoff — falling back to the oldest we hold.
        """
        from steward.diffing import snapshot_diff

        after = self.latest
        empty = {"from_ts": None, "to_ts": None, "span_s": 0.0, "nodes": [], "vms": []}
        if after is None or len(self.ring) < 2:
            return empty
        cutoff = after.ts - since_s
        older = [s for s in self.ring if s.ts <= cutoff and s is not after]
        before = older[-1] if older else self.ring[0]
        if before is after:
            return empty
        result = snapshot_diff(before, after)
        result.update(from_ts=before.ts, to_ts=after.ts, span_s=round(after.ts - before.ts, 1))
        return result

    def simulate_balancer(self) -> dict:
        """Dry preview of the Tier-0 balancer: the imbalance now and the moves it
        would make — without executing anything or touching cooldown state.

        Drives the same pure policy as ``_run_balancer`` so the UI shows exactly
        what an enabled balancer would do. ``would_act`` reflects the real
        trigger (enabled + over threshold + a move exists); ``moves`` are shown
        regardless so the operator can preview them before enabling.
        """
        from steward.balancer import blended_imbalance, imbalance, suggest_balancing_migrations

        s = self.settings
        bal = self.store.get_check("builtin.autonomous_balancer")
        enabled = bool(bal and bal.enabled)
        threshold = float(bal.condition.threshold) if bal else 0.0
        snap = self.latest
        result = {
            "enabled": enabled,
            "blended_imbalance": 0.0,
            "threshold": threshold,
            "imbalance_cpu": 0.0,
            "imbalance_mem": 0.0,
            "weights": {"cpu": s.balancer_weight_cpu, "mem": s.balancer_weight_mem},
            "would_act": False,
            "moves": [],
        }
        if snap is None:
            return result

        blended = blended_imbalance(snap, s.balancer_weight_cpu, s.balancer_weight_mem)
        moves = suggest_balancing_migrations(
            snap,
            w_cpu=s.balancer_weight_cpu, w_mem=s.balancer_weight_mem,
            max_target_pct=s.balancer_max_target_pct,
            min_improvement=s.balancer_min_improvement,
            max_moves=s.balancer_max_moves_per_cycle,
        )
        result.update(
            blended_imbalance=round(blended, 2),
            imbalance_cpu=round(imbalance(snap, "cpu"), 2),
            imbalance_mem=round(imbalance(snap, "mem"), 2),
            would_act=enabled and blended > threshold and bool(moves),
            moves=[{"vmid": m.vmid, "name": m.name, "source": m.source,
                    "target": m.target, "improvement": round(m.improvement, 2)} for m in moves],
        )
        return result

    async def _maybe_suggest(self, ev: Event, checks: list[Check]) -> None:
        check = next((c for c in checks if c.id == ev.check_id), None)
        if check is None or check.suggested_action is None:
            return
        req = _suggestion_from_event(check, ev)
        if req is None:
            return
        if check.auto_execute:
            await self.executor.run(req)  # guardrails still apply (allow-list, etc.)
        else:
            await asyncio.to_thread(self.executor.propose, req)

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #
    def flags_dict(self) -> dict:
        return {
            "paused": self.is_paused(),
            "dry_run": self.is_dry_run(),
            "allowlist": self.allowlist(),
            "llm_enabled": self.settings.llm_enabled,
            "proxmox_mode": self.settings.proxmox_mode,
        }

    def _prune(self) -> None:
        cutoff = now_ts() - self.settings.metrics_retention_hours * 3600
        n = self.store.prune_metrics(cutoff)
        e = self.store.prune_events(cutoff)
        if n or e:
            log.info("retention: pruned %s metric rows, %s events", n, e)


def _suggestion_from_event(check: Check, ev: Event) -> Optional[ActionRequest]:
    sa = check.suggested_action
    if sa is None:
        return None
    params = dict(sa.params)
    if sa.type == ActionType.migrate:
        # node pressure -> migrate a guest off the firing node
        params.setdefault("node", ev.target)
    elif sa.type in (ActionType.power, ActionType.balloon):
        try:
            params.setdefault("vmid", int(ev.target))
        except (TypeError, ValueError):
            return None
    return ActionRequest(
        type=sa.type, params=params, reason=f"{check.name}: {ev.message}",
        source="rule", check_id=check.id, auto_execute=check.auto_execute,
    )
