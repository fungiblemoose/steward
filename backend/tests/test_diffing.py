from __future__ import annotations

from steward.diffing import snapshot_diff
from steward.models import ClusterSnapshot, NodeMetric, NodeStatus, VMMetric, VMStatus


def _node(name, cpu, mem_used, *, status=NodeStatus.online):
    return NodeMetric(node=name, status=status, cpu_pct=cpu,
                      mem_used_mb=mem_used, mem_total_mb=10000)


def _vm(vmid, node, *, cpu=20.0, status=VMStatus.running, name=None):
    return VMMetric(vmid=vmid, name=name or f"vm{vmid}", node=node, status=status,
                    cpu_pct=cpu, cores=2)


def test_no_changes_when_identical():
    snap = ClusterSnapshot(nodes=[_node("a", 40, 4000)], vms=[_vm(1, "a")])
    d = snapshot_diff(snap, snap)
    assert d == {"nodes": [], "vms": []}


def test_node_load_swing_surfaced_with_sign():
    before = ClusterSnapshot(nodes=[_node("a", 90, 8000)])
    after = ClusterSnapshot(nodes=[_node("a", 50, 5000)])
    d = snapshot_diff(before, after)
    assert len(d["nodes"]) == 1
    n = d["nodes"][0]
    assert n["node"] == "a" and n["cpu_delta"] == -40.0 and n["mem_delta"] == -30.0


def test_tiny_node_wiggle_is_ignored():
    before = ClusterSnapshot(nodes=[_node("a", 40.0, 4000)])
    after = ClusterSnapshot(nodes=[_node("a", 40.3, 4010)])  # < 1pct
    assert snapshot_diff(before, after)["nodes"] == []


def test_node_status_change_surfaced():
    before = ClusterSnapshot(nodes=[_node("a", 40, 4000)])
    after = ClusterSnapshot(nodes=[_node("a", 40, 4000, status=NodeStatus.offline)])
    n = snapshot_diff(before, after)["nodes"][0]
    assert n["status"] == {"from": "online", "to": "offline"}


def test_vm_migration_surfaced():
    before = ClusterSnapshot(vms=[_vm(1, "a")])
    after = ClusterSnapshot(vms=[_vm(1, "b")])
    v = snapshot_diff(before, after)["vms"][0]
    assert v["vmid"] == 1 and v["moved"] == {"from": "a", "to": "b"}


def test_vm_power_change_surfaced():
    before = ClusterSnapshot(vms=[_vm(1, "a", status=VMStatus.running)])
    after = ClusterSnapshot(vms=[_vm(1, "a", status=VMStatus.stopped)])
    v = snapshot_diff(before, after)["vms"][0]
    assert v["status"] == {"from": "running", "to": "stopped"}


def test_vm_appeared_and_disappeared():
    before = ClusterSnapshot(vms=[_vm(1, "a")])
    after = ClusterSnapshot(vms=[_vm(2, "b")])
    changes = {c.get("change"): c for c in snapshot_diff(before, after)["vms"]}
    assert changes["appeared"]["vmid"] == 2
    assert changes["disappeared"]["vmid"] == 1


def test_small_vm_cpu_drift_ignored_but_big_surfaced():
    before = ClusterSnapshot(vms=[_vm(1, "a", cpu=20.0)])
    assert snapshot_diff(before, ClusterSnapshot(vms=[_vm(1, "a", cpu=22.0)]))["vms"] == []
    big = snapshot_diff(before, ClusterSnapshot(vms=[_vm(1, "a", cpu=80.0)]))["vms"]
    assert big and big[0]["cpu_delta"] == 60.0
