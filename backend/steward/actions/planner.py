"""Deterministic migration planner.

Given a snapshot and a VM to move, pick the best target node. The LLM may
*narrate* this choice, but the decision is made here, in plain code, so it is
testable and predictable.

Strategy: choose the online node (other than the current one) with the most
free headroom on the dimension under pressure, after accounting for the guest's
own footprint. Refuse (return ``None``) if no eligible target exists — e.g.
single-node clusters or every other node already loaded.
"""
from __future__ import annotations

from typing import Optional

from steward.models import ClusterSnapshot, NodeMetric, VMMetric


def _headroom(node: NodeMetric, dimension: str) -> float:
    if dimension == "mem":
        return node.mem_total_mb - node.mem_used_mb
    # default: CPU headroom as percentage points below 100
    return max(0.0, 100.0 - node.cpu_pct)


def plan_migration_target(
    snap: ClusterSnapshot,
    vmid: int,
    *,
    dimension: str = "cpu",
    max_target_pct: float = 80.0,
) -> Optional[str]:
    """Return the name of the best target node for ``vmid``, or ``None``.

    ``max_target_pct`` guards against migrating into a node that is itself
    nearly saturated on the chosen dimension.
    """
    vm: Optional[VMMetric] = next((v for v in snap.vms if v.vmid == vmid), None)
    if vm is None:
        return None

    candidates: list[tuple[float, str]] = []
    for node in snap.nodes:
        if node.node == vm.node or node.status.value != "online":
            continue
        load_pct = node.mem_pct if dimension == "mem" else node.cpu_pct
        if load_pct >= max_target_pct:
            continue
        candidates.append((_headroom(node, dimension), node.node))

    if not candidates:
        return None
    candidates.sort(reverse=True)  # most headroom first
    return candidates[0][1]


def busiest_vm_on_node(snap: ClusterSnapshot, node: str, *, dimension: str = "cpu") -> Optional[int]:
    """Pick the heaviest running guest on a node to relieve pressure."""
    vms = [v for v in snap.vms if v.node == node and v.status.value == "running"]
    if not vms:
        return None
    key = (lambda v: v.mem_used_mb) if dimension == "mem" else (lambda v: v.cpu_pct * v.cores)
    return max(vms, key=key).vmid
