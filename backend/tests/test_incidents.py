from __future__ import annotations

from steward.incidents import IncidentTracker
from steward.models import Severity


def _rec(t: IncidentTracker, ts: float, *, severity: Severity = Severity.warning,
         check_id: str = "c", target: str = "x") -> None:
    t.record(check_id=check_id, check_name="C", target=target, severity=severity, ts=ts)


def test_below_min_severity_is_ignored():
    t = IncidentTracker(min_severity=Severity.warning, min_occurrences=1, min_age_s=0)
    _rec(t, 0, severity=Severity.info)
    assert t.due(1000) == []


def test_requires_both_occurrences_and_age():
    t = IncidentTracker(min_occurrences=3, min_age_s=600)
    _rec(t, 0)
    _rec(t, 400)
    assert t.due(10_000) == []           # only 2 occurrences
    _rec(t, 800)                         # 3rd, age 800 >= 600
    due = t.due(10_000)
    assert len(due) == 1 and due[0].count == 3


def test_age_gate_blocks_rapid_bursts():
    t = IncidentTracker(min_occurrences=2, min_age_s=600)
    _rec(t, 0)
    _rec(t, 100)                         # count 2 but age 100 < 600
    assert t.due(10_000) == []


def test_cooldown_blocks_then_allows_reescalation():
    t = IncidentTracker(min_occurrences=2, min_age_s=0, cooldown_s=3600)
    _rec(t, 0)
    _rec(t, 10)
    assert len(t.due(100)) == 1
    t.mark_escalated(("c", "x"), 100)
    assert t.due(200) == []                       # within cooldown
    assert len(t.due(100 + 3600 + 1)) == 1        # cooldown elapsed


def test_severity_rises_to_worst_seen():
    t = IncidentTracker(min_occurrences=1, min_age_s=0)
    _rec(t, 0, severity=Severity.warning)
    _rec(t, 1, severity=Severity.critical)
    assert t.due(10)[0].severity == Severity.critical


def test_prune_drops_quiet_incidents():
    t = IncidentTracker(ttl_s=1800)
    _rec(t, 0)
    t.prune(5000)                        # silent for 5000s > ttl
    assert t.due(5000) == []


def test_distinct_targets_are_distinct_incidents():
    t = IncidentTracker(min_occurrences=2, min_age_s=0)
    _rec(t, 0, target="a")
    _rec(t, 1, target="a")
    _rec(t, 1, target="b")
    due = {i.target for i in t.due(10)}
    assert due == {"a"}                  # b only fired once
