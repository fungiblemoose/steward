"""Guard the worked examples so they don't silently rot.

The receiver lives outside the package (examples/), so we load it by path and
exercise its pure decision function — the part real wiring depends on.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RECEIVER = Path(__file__).resolve().parents[2] / "examples" / "escalation_receiver.py"


@pytest.fixture(scope="module")
def receiver():
    spec = importlib.util.spec_from_file_location("escalation_receiver", _RECEIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stopped_vm_incident_proposes_restart(receiver):
    payload = {"incident": {"check_id": "builtin.vm_unexpected_stop", "target": "101",
                            "count": 3, "severity": "critical"}}
    proposal = receiver.propose_from_incident(payload)
    assert proposal["type"] == "power"
    assert proposal["params"] == {"vmid": 101, "state": "start"}
    assert proposal["mode"] == "propose"          # never executes directly


def test_other_incident_proposes_a_visible_note(receiver):
    payload = {"incident": {"check_id": "builtin.node_cpu_pressure", "check_name": "Node CPU",
                            "target": "pve-1", "count": 5, "severity": "warning"}}
    proposal = receiver.propose_from_incident(payload)
    assert proposal["type"] == "notify"
    assert proposal["mode"] == "propose"
    assert "pve-1" in proposal["params"]["message"]


def test_non_numeric_vm_target_is_skipped(receiver):
    payload = {"incident": {"check_id": "builtin.vm_unexpected_stop", "target": "not-an-id"}}
    # can't restart a non-numeric vmid -> propose nothing rather than crash
    assert receiver.propose_from_incident(payload) is None
