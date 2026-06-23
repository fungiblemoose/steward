"""'What changed?' — a pure diff between two cluster snapshots.

Answers "what moved in the last N minutes": node load swings, guests that
migrated, changed power state, or appeared/disappeared. Pure and deterministic
so it's trivially testable; the runtime picks the two snapshots from its ring
buffer and calls this.
"""
from __future__ import annotations

from steward.models import ClusterSnapshot

# Only surface deltas above these magnitudes, so noise doesn't bury real moves.
_NODE_PCT_EPS = 1.0
_VM_CPU_EPS = 5.0


def snapshot_diff(before: ClusterSnapshot, after: ClusterSnapshot) -> dict:
    """Return the meaningful changes from ``before`` to ``after``."""
    return {"nodes": _node_changes(before, after), "vms": _vm_changes(before, after)}


def _node_changes(before: ClusterSnapshot, after: ClusterSnapshot) -> list[dict]:
    prev = {n.node: n for n in before.nodes}
    out = []
    for n in after.nodes:
        b = prev.get(n.node)
        if b is None:
            continue
        cpu_d = round(n.cpu_pct - b.cpu_pct, 1)
        mem_d = round(n.mem_pct - b.mem_pct, 1)
        status_changed = n.status != b.status
        if abs(cpu_d) < _NODE_PCT_EPS and abs(mem_d) < _NODE_PCT_EPS and not status_changed:
            continue
        entry = {"node": n.node, "cpu_delta": cpu_d, "mem_delta": mem_d}
        if status_changed:
            entry["status"] = {"from": b.status.value, "to": n.status.value}
        out.append(entry)
    return out


def _vm_changes(before: ClusterSnapshot, after: ClusterSnapshot) -> list[dict]:
    prev = {v.vmid: v for v in before.vms}
    now = {v.vmid: v for v in after.vms}
    out = []
    for vmid, v in now.items():
        b = prev.get(vmid)
        if b is None:
            out.append({"vmid": vmid, "name": v.name, "change": "appeared", "node": v.node})
            continue
        moved = b.node != v.node
        status_changed = b.status != v.status
        cpu_d = round(v.cpu_pct - b.cpu_pct, 1)
        if not moved and not status_changed and abs(cpu_d) < _VM_CPU_EPS:
            continue
        entry: dict = {"vmid": vmid, "name": v.name, "node": v.node, "cpu_delta": cpu_d}
        if moved:
            entry["moved"] = {"from": b.node, "to": v.node}
        if status_changed:
            entry["status"] = {"from": b.status.value, "to": v.status.value}
        out.append(entry)
    for vmid, b in prev.items():
        if vmid not in now:
            out.append({"vmid": vmid, "name": b.name, "change": "disappeared", "node": b.node})
    return out
