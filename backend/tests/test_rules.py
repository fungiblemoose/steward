from __future__ import annotations

from steward.checks.schema import Check, ComparisonOp, Condition, ProbeType
from steward.models import Severity
from steward.rules.builtins import builtin_checks
from steward.rules.engine import RuleEngine, evaluate_check


def _cpu_check(threshold=85.0):
    return Check(
        id="t.cpu", name="cpu", probe_type=ProbeType.proxmox_metric, target="node:*",
        condition=Condition(metric="cpu_pct", op=ComparisonOp.gt, threshold=threshold),
        severity=Severity.warning, cooldown_s=300,
    )


def _drive_pressure(mock_client, node="pve-1"):
    for vmid in (101, 102, 103):
        mock_client.inject_load(vmid=vmid, cpu_pct=100)
    for _ in range(5):
        mock_client.step()


async def test_cpu_pressure_fires_when_over_threshold(mock_client):
    _drive_pressure(mock_client)
    snap = await mock_client.get_cluster_resources()
    events = evaluate_check(_cpu_check(80), snap)
    assert any(e.target == "pve-1" for e in events)


async def test_cpu_pressure_silent_when_calm(mock_client):
    snap = await mock_client.get_cluster_resources()
    assert evaluate_check(_cpu_check(95), snap) == []


async def test_vm_stopped_check(mock_client):
    await mock_client.set_vm_power(201, "stop")
    snap = await mock_client.get_cluster_resources()
    chk = Check(
        id="t.down", name="down", target="vm:*",
        condition=Condition(metric="status", op=ComparisonOp.eq, threshold_str="stopped"),
        severity=Severity.critical,
    )
    events = evaluate_check(chk, snap)
    assert len(events) == 1 and events[0].target == "201"


async def test_quorum_lost_check(mock_client):
    mock_client.set_quorate(False)
    snap = await mock_client.get_cluster_resources()
    chk = Check(
        id="t.quorum", name="q", target="cluster",
        condition=Condition(metric="quorate", op=ComparisonOp.eq, threshold_str="false"),
        severity=Severity.critical,
    )
    assert len(evaluate_check(chk, snap)) == 1


async def test_storage_near_full(mock_client):
    mock_client.set_storage_used("local", "pve-1", 470)  # of 480
    snap = await mock_client.get_cluster_resources()
    chk = Check(
        id="t.stor", name="s", target="storage:*",
        condition=Condition(metric="used_pct", op=ComparisonOp.gt, threshold=85),
    )
    events = evaluate_check(chk, snap)
    assert any(e.target == "local@pve-1" for e in events)


async def test_cooldown_suppresses_repeat(mock_client):
    _drive_pressure(mock_client)
    snap = await mock_client.get_cluster_resources()
    engine = RuleEngine()
    chk = _cpu_check(50)
    first = engine.evaluate([chk], snap)
    second = engine.evaluate([chk], snap)  # same ts -> within cooldown
    assert first and not second


async def test_disabled_check_never_fires(mock_client):
    snap = await mock_client.get_cluster_resources()
    chk = _cpu_check(0)
    chk.enabled = False
    assert evaluate_check(chk, snap) == []


def test_builtins_are_valid():
    checks = builtin_checks()
    assert {c.id for c in checks} == {
        "builtin.node_cpu_pressure", "builtin.node_mem_pressure",
        "builtin.vm_unexpected_stop", "builtin.storage_near_full",
        "builtin.cluster_quorum_lost",
    }
    for c in checks:
        assert c.source == "builtin"
