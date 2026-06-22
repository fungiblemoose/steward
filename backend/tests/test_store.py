from __future__ import annotations

from steward.checks.schema import Check, ComparisonOp, Condition
from steward.models import (
    ActionRecord,
    ActionStatus,
    ActionType,
    ClusterSnapshot,
    Event,
    NodeMetric,
    Severity,
    now_ts,
)


def _snap():
    return ClusterSnapshot(nodes=[NodeMetric(node="pve-1", cpu_pct=50, mem_used_mb=1000,
                                             mem_total_mb=2000)])


def test_snapshot_roundtrip_and_series(store):
    snap = _snap()
    store.insert_snapshot(snap)
    series = store.metric_series("node", "pve-1")
    assert len(series) == 1
    assert series[0]["cpu_pct"] == 50.0


def test_metrics_retention(store):
    old = ClusterSnapshot(ts=now_ts() - 100000,
                          nodes=[NodeMetric(node="pve-1", mem_total_mb=1)])
    store.insert_snapshot(old)
    store.insert_snapshot(_snap())
    removed = store.prune_metrics(now_ts() - 3600)
    assert removed == 1


def test_check_crud(store):
    chk = Check(id="c1", name="c", condition=Condition(metric="cpu_pct", op=ComparisonOp.gt,
                                                       threshold=80))
    store.upsert_check(chk)
    assert store.get_check("c1").name == "c"
    chk.name = "renamed"
    store.upsert_check(chk)
    assert store.get_check("c1").name == "renamed"
    assert len(store.list_checks()) == 1
    assert store.delete_check("c1") is True
    assert store.get_check("c1") is None


def test_event_filtering(store):
    store.insert_event(Event(check_id="a", check_name="A", severity=Severity.warning,
                             message="w"))
    store.insert_event(Event(check_id="b", check_name="B", severity=Severity.critical,
                             message="c"))
    assert len(store.list_events()) == 2
    assert len(store.list_events(severity="critical")) == 1
    assert len(store.list_events(check_id="a")) == 1


def test_action_audit_lifecycle(store):
    rec = ActionRecord(type=ActionType.migrate, params={"vmid": 1, "target": "pve-2"})
    rec.id = store.insert_action(rec)
    rec.status = ActionStatus.executed
    rec.resolved_at = now_ts()
    store.update_action(rec)
    got = store.get_action(rec.id)
    assert got.status == ActionStatus.executed
    assert len(store.list_actions(status="executed")) == 1


def test_app_state_kv(store):
    assert store.get_state("missing") is None
    store.set_state("paused", "true")
    assert store.get_state("paused") == "true"
    store.set_state("paused", "false")
    assert store.get_state("paused") == "false"
