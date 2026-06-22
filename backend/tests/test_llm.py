from __future__ import annotations

import json

import pytest

from steward.llm.client import FakeLLMClient, extract_json
from steward.llm.service import LLMService
from steward.models import ClusterSnapshot, Event, NodeMetric, Severity


def test_extract_json_handles_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('here you go: {"a": 2} thanks') == {"a": 2}
    assert extract_json('{"a": 3}') == {"a": 3}


async def test_nl_to_check_produces_disabled_llm_check():
    canned = json.dumps({
        "name": "node memory over 85%",
        "probe_type": "proxmox_metric",
        "target": "node:*",
        "condition": {"metric": "mem_pct", "op": "gt", "threshold": 85},
        "severity": "warning",
        "cooldown_s": 300,
    })
    svc = LLMService(FakeLLMClient(default=canned))
    check = await svc.nl_to_check("alert me if any node memory goes over 85%")
    assert check.source == "llm"
    assert check.enabled is False          # must be reviewed before it fires
    assert check.id.startswith("llm.")
    assert check.condition.metric == "mem_pct"
    assert check.condition.threshold == 85


async def test_nl_to_check_rejects_garbage():
    svc = LLMService(FakeLLMClient(default="not json at all"))
    with pytest.raises(Exception):
        await svc.nl_to_check("nonsense")


async def test_answer_is_grounded_in_state():
    snap = ClusterSnapshot(nodes=[
        NodeMetric(node="pve-1", cpu_pct=92, mem_used_mb=1500, mem_total_mb=2000),
        NodeMetric(node="pve-2", cpu_pct=10, mem_used_mb=500, mem_total_mb=2000),
    ])
    fake = FakeLLMClient(default="pve-1 is under CPU pressure at 92%.")
    svc = LLMService(fake)
    answer = await svc.answer("what's under pressure?", snap, [])
    assert "pve-1" in answer
    # the state was actually passed to the model
    user_msg = fake.calls[-1][-1]["content"]
    assert "pve-1" in user_msg and "92" in user_msg


async def test_explain_passes_event():
    fake = FakeLLMClient(default="CPU is high; consider migrating a VM.")
    svc = LLMService(fake)
    ev = Event(check_id="x", check_name="CPU", severity=Severity.warning,
               message="Node pve-1 cpu_pct=95", value=95)
    out = await svc.explain(ev, None)
    assert "migrat" in out.lower()
    assert "pve-1" in fake.calls[-1][-1]["content"]
