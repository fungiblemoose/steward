from __future__ import annotations

from steward.models import (
    ActionStatus,
    ClusterSnapshot,
    NodeMetric,
    VMMetric,
    VMStatus,
    now_ts,
)


async def test_poll_once_persists_and_seeds(steward):
    snap = await steward.poll_once()
    assert snap is not None
    assert steward.latest is not None
    # builtins seeded
    ids = {c.id for c in steward.store.list_checks()}
    assert "builtin.node_cpu_pressure" in ids
    # metrics persisted
    series = steward.store.metric_series("node", "pve-1")
    assert len(series) >= 1


async def test_cpu_pressure_event_and_suggestion(steward):
    # drive pve-1 hot
    for vmid in (101, 102, 103):
        steward.client.inject_load(vmid=vmid, cpu_pct=100)
    fired = False
    for _ in range(8):
        await steward.poll_once()
        events = steward.store.list_events(check_id="builtin.node_cpu_pressure")
        if events:
            fired = True
            break
    assert fired, "CPU pressure event never fired"
    # a migration was suggested into the approval queue (auto_execute=False builtin)
    proposed = steward.store.list_actions(status=ActionStatus.proposed.value)
    assert any(a.type.value == "migrate" for a in proposed)


async def test_flags_toggle_persist(steward):
    steward.set_paused(True)
    assert steward.is_paused() is True
    steward.set_dry_run(False)
    assert steward.is_dry_run() is False
    steward.set_allowlist([1, 2, 3])
    assert steward.allowlist() == [1, 2, 3]


def test_predictive_event_fires_on_rising_trend(steward):
    # synthesize a steadily rising CPU trend on pve-1 in the ring buffer
    base = now_ts() - 200
    for i in range(15):
        cpu = 50 + i * 2.5  # rising toward 90
        snap = ClusterSnapshot(
            ts=base + i * steward.settings.poll_interval_s,
            nodes=[NodeMetric(node="pve-1", cpu_pct=cpu, mem_used_mb=1000, mem_total_mb=4000)],
        )
        steward.ring.append(snap)
    latest = ClusterSnapshot(
        ts=now_ts(),
        nodes=[NodeMetric(node="pve-1", cpu_pct=85, mem_used_mb=1000, mem_total_mb=4000)],
    )
    events = steward._run_predictions(latest)
    assert any(e.check_id == "predictive.node_cpu_pct" and e.target == "pve-1" for e in events)


async def test_no_auto_execute_for_builtin_suggestions(steward):
    """Builtin suggestions must land in the queue, never auto-fire.

    This also guards that the autonomous balancer stays dormant while disabled
    (its builtin check ships ``enabled=False``)."""
    for vmid in (101, 102, 103):
        steward.client.inject_load(vmid=vmid, cpu_pct=100)
    for _ in range(8):
        await steward.poll_once()
    executed = steward.store.list_actions(status=ActionStatus.executed.value)
    assert executed == []  # nothing executed automatically


def test_diff_picks_baseline_and_reports_changes(steward):
    base = now_ts()
    old = ClusterSnapshot(
        ts=base - 600,
        nodes=[NodeMetric(node="pve-1", cpu_pct=90, mem_used_mb=8000, mem_total_mb=10000)],
        vms=[VMMetric(vmid=1, name="x", node="pve-1", status=VMStatus.running, cpu_pct=80, cores=2)],
    )
    new = ClusterSnapshot(
        ts=base,
        nodes=[NodeMetric(node="pve-1", cpu_pct=40, mem_used_mb=4000, mem_total_mb=10000)],
        vms=[VMMetric(vmid=1, name="x", node="pve-2", status=VMStatus.running, cpu_pct=30, cores=2)],
    )
    steward.ring.clear()
    steward.ring.append(old)
    steward.ring.append(new)
    steward.latest = new

    d = steward.diff(since_s=300)
    assert d["span_s"] == 600.0
    assert d["nodes"][0]["cpu_delta"] == -50.0
    assert any(v.get("moved") == {"from": "pve-1", "to": "pve-2"} for v in d["vms"])


def test_diff_empty_without_history(steward):
    steward.ring.clear()
    steward.latest = None
    d = steward.diff()
    assert d["nodes"] == [] and d["vms"] == [] and d["span_s"] == 0.0


async def test_autonomous_balancer_executes_when_enabled(steward):
    """Enabling the balancer check makes it auto-migrate (dry-run) to rebalance."""
    bal = next(c for c in steward.store.list_checks() if c.id == "builtin.autonomous_balancer")
    assert bal.enabled is False  # off by default
    bal.enabled = True
    steward.store.upsert_check(bal)

    for vmid in (101, 102, 103):
        steward.client.inject_load(vmid=vmid, cpu_pct=100)

    def _balancer_migration():
        return next(
            (a for a in steward.store.list_actions(status=ActionStatus.executed.value)
             if a.type.value == "migrate" and a.check_id == "builtin.autonomous_balancer"),
            None,
        )

    mig = None
    for _ in range(12):
        await steward.poll_once()
        mig = _balancer_migration()
        if mig:
            break
    assert mig is not None, "balancer never executed a migration"
    assert mig.dry_run is True            # dry-run by default: simulated, audited
    assert mig.params.get("target")       # a concrete target was chosen
    assert mig.source == "rule"
