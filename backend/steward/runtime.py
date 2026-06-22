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
from steward.checks.probes import run_probe
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
        self.ring: collections.deque[ClusterSnapshot] = collections.deque(
            maxlen=settings.ring_buffer_size
        )
        self.latest: Optional[ClusterSnapshot] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
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

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
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

        for ev in events:
            ev.id = await asyncio.to_thread(self.store.insert_event, ev)
            if ev.severity in (Severity.warning, Severity.critical):
                await self.notifier.send(f"[{ev.severity.value}] {ev.check_name}", ev.message,
                                         ev.severity)
            await self._maybe_suggest(ev, checks)

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
