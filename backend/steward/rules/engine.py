"""The deterministic rule engine.

``evaluate_check`` is a pure function: given a check and a cluster snapshot it
returns the events the check would fire, with no I/O and no clock dependence
beyond the snapshot's own timestamp. The :class:`RuleEngine` wraps it with
per-check cooldown bookkeeping so a noisy condition doesn't spam events.

Only ``proxmox_metric`` checks are evaluated here against the snapshot. Active
probes (http_get, tcp_port, ...) live in :mod:`steward.checks.probes` and are
driven by the collector, since they perform I/O.
"""
from __future__ import annotations

from typing import Optional

from steward.checks.schema import Check, Condition, ProbeType, parse_target
from steward.models import (
    ClusterSnapshot,
    Event,
    NodeMetric,
    StorageMetric,
    VMMetric,
)


def _numeric_metric(entity: object, metric: str) -> Optional[float]:
    """Pull a numeric metric from a node/vm/storage model, incl. derived props."""
    if hasattr(entity, metric):
        val = getattr(entity, metric)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _condition_holds(value: Optional[float], cond: Condition) -> bool:
    if value is None:
        return False
    return cond.op.apply(value, cond.threshold)


def _string_condition_holds(value: str, cond: Condition) -> bool:
    target = cond.threshold_str if cond.threshold_str is not None else ""
    if cond.op.name == "eq":
        return value == target
    if cond.op.name == "ne":
        return value != target
    return False


def evaluate_check(check: Check, snap: ClusterSnapshot) -> list[Event]:
    """Return events this check fires against the snapshot. Pure."""
    if not check.enabled or check.probe_type != ProbeType.proxmox_metric:
        return []

    scope, selector = parse_target(check.target)
    events: list[Event] = []

    if scope == "cluster":
        events.extend(_eval_cluster(check, snap))
    elif scope == "node":
        for n in snap.nodes:
            if selector in ("*", n.node):
                ev = _eval_entity(check, n, n.node, snap.ts)
                if ev:
                    events.append(ev)
    elif scope == "vm":
        for v in snap.vms:
            if selector in ("*", str(v.vmid), v.name):
                ev = _eval_vm(check, v, snap.ts)
                if ev:
                    events.append(ev)
    elif scope == "storage":
        for s in snap.storage:
            if selector in ("*", s.storage, f"{s.storage}:{s.node}"):
                ev = _eval_storage(check, s, snap.ts)
                if ev:
                    events.append(ev)
    return events


def _eval_cluster(check: Check, snap: ClusterSnapshot) -> list[Event]:
    cond = check.condition
    if cond.metric != "quorate":
        return []
    want = (cond.threshold_str or "").lower()
    holds = (want == "false" and not snap.quorate) or (want == "true" and snap.quorate)
    if not holds and cond.threshold_str is None:
        # numeric form: quorate as 0/1
        holds = cond.op.apply(1.0 if snap.quorate else 0.0, cond.threshold)
    if holds:
        return [Event(
            ts=snap.ts, check_id=check.id, check_name=check.name, severity=check.severity,
            target="cluster", value=1.0 if snap.quorate else 0.0,
            message=f"Cluster quorum is {'present' if snap.quorate else 'LOST'}",
            context={"quorate": snap.quorate},
        )]
    return []


def _eval_entity(check: Check, node: NodeMetric, name: str, ts: float) -> Optional[Event]:
    cond = check.condition
    if cond.metric == "status":
        if _string_condition_holds(node.status.value, cond):
            return _mk_event(check, name, node.status.value, ts,
                             f"Node {name} status is {node.status.value}",
                             {"status": node.status.value})
        return None
    val = _numeric_metric(node, cond.metric)
    if _condition_holds(val, cond):
        return _mk_event(check, name, val, ts,
                         f"Node {name} {cond.metric}={val:.1f} {cond.op.value} {cond.threshold}",
                         {"metric": cond.metric, "value": val, "threshold": cond.threshold})
    return None


def _eval_vm(check: Check, vm: VMMetric, ts: float) -> Optional[Event]:
    cond = check.condition
    label = f"{vm.name} ({vm.vmid})"
    if cond.metric == "status":
        if _string_condition_holds(vm.status.value, cond):
            return _mk_event(check, str(vm.vmid), vm.status.value, ts,
                             f"VM {label} status is {vm.status.value}",
                             {"status": vm.status.value, "name": vm.name, "node": vm.node})
        return None
    val = _numeric_metric(vm, cond.metric)
    if _condition_holds(val, cond):
        return _mk_event(check, str(vm.vmid), val, ts,
                         f"VM {label} {cond.metric}={val:.1f} {cond.op.value} {cond.threshold}",
                         {"metric": cond.metric, "value": val, "name": vm.name, "node": vm.node})
    return None


def _eval_storage(check: Check, st: StorageMetric, ts: float) -> Optional[Event]:
    cond = check.condition
    val = _numeric_metric(st, cond.metric)
    label = f"{st.storage}@{st.node}"
    if _condition_holds(val, cond):
        return _mk_event(check, label, val, ts,
                         f"Storage {label} {cond.metric}={val:.1f} {cond.op.value} {cond.threshold}",
                         {"metric": cond.metric, "value": val, "storage": st.storage,
                          "node": st.node})
    return None


def _mk_event(check: Check, target: str, value, ts: float, message: str, context: dict) -> Event:
    numeric = float(value) if isinstance(value, (int, float)) else None
    return Event(
        ts=ts, check_id=check.id, check_name=check.name, severity=check.severity,
        target=target, value=numeric, message=message, context=context,
    )


class RuleEngine:
    """Evaluates checks with per-(check, target) cooldown gating."""

    def __init__(self) -> None:
        # (check_id, target) -> last fire ts
        self._last_fire: dict[tuple[str, str], float] = {}

    def evaluate(self, checks: list[Check], snap: ClusterSnapshot) -> list[Event]:
        fired: list[Event] = []
        for check in checks:
            for ev in evaluate_check(check, snap):
                key = (ev.check_id, ev.target)
                last = self._last_fire.get(key)
                if last is not None and (snap.ts - last) < check.cooldown_s:
                    continue
                self._last_fire[key] = snap.ts
                fired.append(ev)
        return fired

    def reset(self) -> None:
        self._last_fire.clear()
