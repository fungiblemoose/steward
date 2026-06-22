from __future__ import annotations

from steward.balancer import (
    blended_imbalance,
    imbalance,
    suggest_balancing_migrations,
    trending_up,
)
from steward.balancer.policy import _simulate_move
from steward.models import ClusterSnapshot, NodeMetric, NodeStatus, VMMetric, VMStatus


def _node(name: str, cpu: float, mem_used: float, *, cores: int = 8, mem_total: float = 32000) -> NodeMetric:
    return NodeMetric(
        node=name, status=NodeStatus.online, cpu_pct=cpu, cpu_cores=cores,
        mem_used_mb=mem_used, mem_total_mb=mem_total,
    )


def _vm(vmid: int, node: str, cpu: float, *, cores: int = 4, mem_used: float = 3000) -> VMMetric:
    return VMMetric(
        vmid=vmid, name=f"vm{vmid}", node=node, status=VMStatus.running,
        cpu_pct=cpu, cores=cores, mem_used_mb=mem_used, mem_max_mb=8192,
    )


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def test_imbalance_zero_when_balanced():
    snap = ClusterSnapshot(nodes=[_node("a", 40, 8000), _node("b", 40, 8000)])
    assert imbalance(snap, "cpu") == 0.0
    assert imbalance(snap, "mem") == 0.0


def test_imbalance_positive_when_skewed():
    snap = ClusterSnapshot(nodes=[_node("a", 80, 8000), _node("b", 20, 8000)])
    assert imbalance(snap, "cpu") > 0.0


def test_offline_nodes_excluded():
    a, b = _node("a", 80, 8000), _node("b", 20, 8000)
    b.status = NodeStatus.offline
    snap = ClusterSnapshot(nodes=[a, b])
    # only one online node -> no spread -> zero
    assert imbalance(snap, "cpu") == 0.0


def test_blended_is_weighted_sum():
    snap = ClusterSnapshot(nodes=[_node("a", 80, 16000), _node("b", 20, 8000)])
    cpu, mem = imbalance(snap, "cpu"), imbalance(snap, "mem")
    blended = blended_imbalance(snap, 0.7, 0.3)
    assert abs(blended - (0.7 * cpu + 0.3 * mem)) < 1e-9


def test_trending_up_true_when_rising_false_when_falling():
    rising = [
        ClusterSnapshot(nodes=[_node("a", 50, 8000), _node("b", 50, 8000)]),
        ClusterSnapshot(nodes=[_node("a", 55, 8000), _node("b", 45, 8000)]),
        ClusterSnapshot(nodes=[_node("a", 75, 8000), _node("b", 25, 8000)]),
    ]
    assert trending_up(rising) is True
    assert trending_up(list(reversed(rising))) is False


def test_trending_up_allows_when_insufficient_history():
    one = [ClusterSnapshot(nodes=[_node("a", 90, 8000), _node("b", 10, 8000)])]
    assert trending_up(one) is True


# --------------------------------------------------------------------------- #
# Move planning
# --------------------------------------------------------------------------- #
def test_simulate_move_shifts_load():
    snap = ClusterSnapshot(
        nodes=[_node("a", 60, 8000), _node("b", 10, 4000)],
        vms=[_vm(1, "a", 80, cores=4, mem_used=3000)],
    )
    after = _simulate_move(snap, 1, "b")
    a = next(n for n in after.nodes if n.node == "a")
    b = next(n for n in after.nodes if n.node == "b")
    vm = next(v for v in after.vms if v.vmid == 1)
    assert vm.node == "b"
    assert a.cpu_pct < 60 and b.cpu_pct > 10      # cpu moved with the guest
    assert a.mem_used_mb == 5000 and b.mem_used_mb == 7000  # mem moved exactly


def test_suggest_moves_busy_guest_to_idle_node():
    snap = ClusterSnapshot(
        nodes=[_node("a", 60, 8000), _node("b", 10, 4000)],
        vms=[_vm(1, "a", 80, cores=4, mem_used=3000)],
    )
    moves = suggest_balancing_migrations(snap, min_improvement=1.0)
    assert len(moves) == 1
    assert moves[0].vmid == 1 and moves[0].source == "a" and moves[0].target == "b"
    assert moves[0].improvement > 0


def test_suggest_returns_nothing_when_balanced():
    snap = ClusterSnapshot(
        nodes=[_node("a", 40, 8000), _node("b", 40, 8000)],
        vms=[_vm(1, "a", 20), _vm(2, "b", 20)],
    )
    assert suggest_balancing_migrations(snap) == []


def test_memory_is_a_hard_target_constraint():
    # b is nearly full on memory; a big mem guest must not be packed onto it
    snap = ClusterSnapshot(
        nodes=[_node("a", 70, 6000), _node("b", 5, 25000)],  # b at ~78% mem
        vms=[_vm(1, "a", 80, cores=4, mem_used=5000)],       # +5000 -> b ~94% mem
    )
    moves = suggest_balancing_migrations(snap, max_target_pct=80.0, min_improvement=0.1)
    assert moves == []  # CPU would love the move, memory forbids it


def test_cpu_cap_blocks_overpacking():
    snap = ClusterSnapshot(
        nodes=[_node("a", 70, 6000, cores=8), _node("b", 70, 6000, cores=8)],
        vms=[_vm(1, "a", 80, cores=4, mem_used=2000)],  # +40pts -> b would hit 110%
    )
    moves = suggest_balancing_migrations(snap, max_target_pct=80.0, min_improvement=0.1)
    assert moves == []
