"""Deterministic load-balancing policy.

All pure functions: given a :class:`ClusterSnapshot` (and tuning constants),
score how imbalanced the cluster is and propose concrete migrations that reduce
that imbalance. No I/O, no clock, no LLM — the same inputs always yield the same
moves, which is exactly what you want for something allowed to live-migrate real
workloads.

Imbalance is the **standard deviation of per-node load** on a dimension (CPU% or
memory%); 0.0 means every online node carries the same load. The *blended* score
is a weighted sum of the CPU and memory imbalances so a single threshold can
react to pressure on either axis, while memory also acts as a hard headroom
constraint on candidate targets (never pack a guest onto a node short on RAM).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from steward.models import ClusterSnapshot, NodeMetric, VMMetric


@dataclass(frozen=True)
class BalanceMove:
    """A single proposed migration and the imbalance improvement it buys."""

    vmid: int
    name: str
    source: str
    target: str
    improvement: float     # blended-imbalance drop this move buys (positive = better)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _online_nodes(snap: ClusterSnapshot) -> list[NodeMetric]:
    return [n for n in snap.nodes if n.status.value == "online"]


def _node_load(node: NodeMetric, dimension: str) -> float:
    return node.mem_pct if dimension == "mem" else node.cpu_pct


def _stddev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / n) ** 0.5


def imbalance(snap: ClusterSnapshot, dimension: str = "cpu") -> float:
    """Stddev of online-node load on ``dimension`` ("cpu" | "mem"). 0 = balanced."""
    return _stddev([_node_load(n, dimension) for n in _online_nodes(snap)])


def blended_imbalance(snap: ClusterSnapshot, w_cpu: float = 0.5, w_mem: float = 0.5) -> float:
    """Weighted blend of CPU and memory imbalance."""
    return w_cpu * imbalance(snap, "cpu") + w_mem * imbalance(snap, "mem")


def trending_up(
    snaps: list[ClusterSnapshot], w_cpu: float = 0.5, w_mem: float = 0.5, *, window: int = 6
) -> bool:
    """True if blended imbalance is rising or sustained over the recent window.

    Guards against acting on a momentary spike that is already receding. With
    fewer than three samples there isn't enough history to judge, so we allow it
    (the threshold gate still applies).
    """
    vals = [blended_imbalance(s, w_cpu, w_mem) for s in snaps[-window:]]
    if len(vals) < 3:
        return True
    prior = vals[:-1]
    return vals[-1] >= sum(prior) / len(prior)


# --------------------------------------------------------------------------- #
# Move planning
# --------------------------------------------------------------------------- #
def _cpu_contrib(vm: VMMetric, node: NodeMetric) -> float:
    """Percentage points ``vm`` adds to ``node``'s CPU (core-weighted).

    Falls back to the raw guest CPU% if the node's core count is unknown, which
    keeps the math defined for hand-built snapshots in tests.
    """
    if node.cpu_cores:
        return vm.cpu_pct * vm.cores / node.cpu_cores
    return vm.cpu_pct


def _simulate_move(snap: ClusterSnapshot, vmid: int, target: str) -> ClusterSnapshot:
    """Return a copy of ``snap`` with ``vmid`` moved to ``target``.

    Memory moves with the guest exactly (node mem is the sum of its guests);
    CPU is adjusted by the guest's core-weighted contribution on each node.
    """
    after = snap.model_copy(deep=True)
    vm = next((v for v in after.vms if v.vmid == vmid), None)
    if vm is None or vm.node == target:
        return after
    src = next((n for n in after.nodes if n.node == vm.node), None)
    dst = next((n for n in after.nodes if n.node == target), None)
    if src is None or dst is None:
        return after

    src.cpu_pct = max(0.0, src.cpu_pct - _cpu_contrib(vm, src))
    src.mem_used_mb = max(0.0, src.mem_used_mb - vm.mem_used_mb)
    dst.cpu_pct = min(100.0, dst.cpu_pct + _cpu_contrib(vm, dst))
    dst.mem_used_mb = dst.mem_used_mb + vm.mem_used_mb
    vm.node = target
    return after


def _target_has_headroom(dst: NodeMetric, vm: VMMetric, max_target_pct: float) -> bool:
    """True if ``vm`` fits on ``dst`` without pushing CPU *or* memory past the cap.

    Memory is a hard constraint: we never migrate onto a node that would end up
    above ``max_target_pct`` memory, however good the CPU math looks.
    """
    new_cpu = dst.cpu_pct + _cpu_contrib(vm, dst)
    new_mem_used = dst.mem_used_mb + vm.mem_used_mb
    new_mem_pct = 100.0 * new_mem_used / dst.mem_total_mb if dst.mem_total_mb else 100.0
    return new_cpu <= max_target_pct and new_mem_pct <= max_target_pct


def _blended_after_move(
    online: list[NodeMetric], vm: VMMetric, src: str, dst: str, w_cpu: float, w_mem: float
) -> float:
    """Blended imbalance if ``vm`` moved ``src`` -> ``dst``, computed arithmetically.

    Only ``vm``'s contribution to the source and destination nodes changes, so we
    derive the post-move per-node loads directly instead of cloning the whole
    snapshot to read one scalar back.
    """
    cpu_loads, mem_loads = [], []
    for n in online:
        cpu, mem_used = n.cpu_pct, n.mem_used_mb
        if n.node == src:
            cpu -= _cpu_contrib(vm, n)
            mem_used -= vm.mem_used_mb
        elif n.node == dst:
            cpu += _cpu_contrib(vm, n)
            mem_used += vm.mem_used_mb
        cpu_loads.append(max(0.0, cpu))
        mem_loads.append(100.0 * max(0.0, mem_used) / n.mem_total_mb if n.mem_total_mb else 0.0)
    return w_cpu * _stddev(cpu_loads) + w_mem * _stddev(mem_loads)


def suggest_balancing_migrations(
    snap: ClusterSnapshot,
    *,
    w_cpu: float = 0.5,
    w_mem: float = 0.5,
    max_target_pct: float = 80.0,
    min_improvement: float = 2.0,
    max_moves: int = 1,
) -> list[BalanceMove]:
    """Greedily propose up to ``max_moves`` migrations that reduce blended imbalance.

    Each round, every running guest is trial-migrated onto every eligible target
    (online, not its current node, with CPU+mem headroom); the move that most
    reduces blended imbalance wins, provided it clears ``min_improvement``. The
    chosen move is applied to a working copy and the search repeats, so a second
    move accounts for the first. Returns ``[]`` when nothing helps enough.
    """
    moves: list[BalanceMove] = []
    working = snap.model_copy(deep=True)

    for _ in range(max(0, max_moves)):
        online = _online_nodes(working)
        if len(online) < 2:
            break
        current = blended_imbalance(working, w_cpu, w_mem)
        if current <= 0.0:
            break

        best: Optional[BalanceMove] = None
        for vm in working.vms:
            if vm.status.value != "running":
                continue
            for dst in online:
                if dst.node == vm.node or not _target_has_headroom(dst, vm, max_target_pct):
                    continue
                after = _blended_after_move(online, vm, vm.node, dst.node, w_cpu, w_mem)
                improvement = current - after
                if improvement >= min_improvement and (best is None or improvement > best.improvement):
                    best = BalanceMove(
                        vmid=vm.vmid, name=vm.name, source=vm.node, target=dst.node,
                        improvement=improvement,
                    )

        if best is None:
            break
        moves.append(best)
        working = _simulate_move(working, best.vmid, best.target)  # apply only the winner

    return moves
