from __future__ import annotations

import pytest

from steward.config import Settings
from steward.models import Severity
from steward.runtime import Steward


class FakeEscalator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def escalate(self, payload: dict) -> None:
        self.calls.append(payload)


def _settings(tmp_path, **over) -> Settings:
    base = dict(
        db_path=str(tmp_path / "esc.db"),
        proxmox_mode="mock",
        poll_interval_s=0.05,
        llm_base_url="",
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def escalating_steward(tmp_path):
    s = _settings(
        tmp_path,
        escalation_webhook_url="http://example.invalid/hook",
        escalation_min_occurrences=3,
        escalation_min_age_s=600.0,
    )
    sw = Steward(s)
    sw.escalator = FakeEscalator()
    yield sw
    sw.store.close()


async def test_repeated_incident_escalates_once(escalating_steward):
    sw = escalating_steward
    assert sw.settings.escalation_enabled is True
    snap = await sw.poll_once()  # establishes sw.latest (balanced cluster, no events)

    base = snap.ts - 700
    for i in range(3):  # three fires spanning > min_age
        sw.incidents.record(check_id="builtin.node_cpu_pressure", check_name="Node CPU pressure",
                            target="pve-1", severity=Severity.warning, ts=base + i * 300)

    await sw._run_escalation(sw.latest)
    assert len(sw.escalator.calls) == 1
    payload = sw.escalator.calls[0]
    assert payload["kind"] == "steward.incident"
    assert payload["incident"]["target"] == "pve-1"
    assert payload["incident"]["count"] == 3
    assert "snapshot" in payload

    # cooldown: a second pass in the same window must not re-page
    await sw._run_escalation(sw.latest)
    assert len(sw.escalator.calls) == 1


async def test_no_escalation_when_disabled(steward):
    """Default settings have no webhook -> escalation is off entirely."""
    steward.escalator = FakeEscalator()
    assert steward.settings.escalation_enabled is False
    snap = await steward.poll_once()
    for i in range(5):  # well past any threshold
        steward.incidents.record(check_id="c", check_name="C", target="x",
                                severity=Severity.critical, ts=snap.ts - 1000 + i * 100)
    await steward._run_escalation(snap)
    assert steward.escalator.calls == []
