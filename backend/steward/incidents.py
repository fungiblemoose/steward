"""Incident tracking for Tier-2 escalation.

A single firing event is noise; an *incident* is the same check firing on the
same target repeatedly and staying unresolved. Only incidents escalate to a
human (a Claude Code run), so a transient blip never pages anyone.

This is deliberately small and pure (no I/O, clock passed in) so the escalation
policy is unit-testable. The runtime owns one :class:`IncidentTracker`, feeds it
each warning/critical event, and escalates whatever it reports as ``due``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from steward.models import Severity

# Severity ranking so "at least warning" style thresholds are comparable.
_RANK = {Severity.info: 0, Severity.warning: 1, Severity.critical: 2}


@dataclass
class Incident:
    check_id: str
    check_name: str
    target: str
    severity: Severity
    first_ts: float
    last_ts: float
    count: int = 1
    last_escalated_ts: Optional[float] = None

    @property
    def age_s(self) -> float:
        return self.last_ts - self.first_ts

    def key(self) -> tuple[str, str]:
        return (self.check_id, self.target)


@dataclass
class IncidentTracker:
    """Aggregates repeated events into incidents and decides which are due.

    ``min_occurrences`` and ``min_age_s`` define when a cluster of events becomes
    an escalatable incident; ``cooldown_s`` stops the same incident re-paging;
    ``ttl_s`` ages out incidents that have gone quiet (treated as resolved).
    """

    min_occurrences: int = 3
    min_age_s: float = 600.0
    cooldown_s: float = 3600.0
    ttl_s: float = 1800.0
    min_severity: Severity = Severity.warning
    _incidents: dict[tuple[str, str], Incident] = field(default_factory=dict)

    def record(self, *, check_id: str, check_name: str, target: str,
               severity: Severity, ts: float) -> None:
        """Fold one event into its incident. Sub-threshold severities are ignored."""
        if _RANK[severity] < _RANK[self.min_severity]:
            return
        key = (check_id, target)
        inc = self._incidents.get(key)
        if inc is None:
            self._incidents[key] = Incident(
                check_id=check_id, check_name=check_name, target=target,
                severity=severity, first_ts=ts, last_ts=ts,
            )
            return
        inc.count += 1
        inc.last_ts = ts
        # remember the most severe level seen in this incident
        if _RANK[severity] > _RANK[inc.severity]:
            inc.severity = severity

    def due(self, now: float) -> list[Incident]:
        """Incidents that have crossed the threshold and aren't in cooldown."""
        out = []
        for inc in self._incidents.values():
            if inc.count < self.min_occurrences or inc.age_s < self.min_age_s:
                continue
            if inc.last_escalated_ts is not None and (now - inc.last_escalated_ts) < self.cooldown_s:
                continue
            out.append(inc)
        return out

    def mark_escalated(self, key: tuple[str, str], now: float) -> None:
        inc = self._incidents.get(key)
        if inc is not None:
            inc.last_escalated_ts = now

    def prune(self, now: float) -> None:
        """Drop incidents that have gone quiet past ``ttl_s`` (treat as resolved)."""
        stale = [k for k, inc in self._incidents.items() if (now - inc.last_ts) > self.ttl_s]
        for k in stale:
            del self._incidents[k]
