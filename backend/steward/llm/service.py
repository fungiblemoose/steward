"""LLM-backed features, all async and strictly off the collector hot path.

The LLM only: (a) turns natural language into a *reviewable, disabled* check,
(b) answers questions grounded in the current snapshot, and (c) explains an
alert. It never executes actions and never gates monitoring. Generated checks
are created disabled with ``source="llm"`` so a human must review and enable
them (the "approval queue" for checks).
"""
from __future__ import annotations

import logging
import uuid

from steward.checks.schema import Check
from steward.llm.client import LLMClient, extract_json
from steward.llm.prompts import (
    EXPLAIN_SYSTEM,
    EXPLAIN_USER,
    NL_TO_CHECK_SYSTEM,
    NL_TO_CHECK_USER,
    QA_SYSTEM,
    QA_USER,
)
from steward.models import ClusterSnapshot, Event, now_ts

log = logging.getLogger("steward.llm")


class LLMService:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    async def nl_to_check(self, request: str) -> Check:
        """Translate a request into a validated, DISABLED, llm-sourced check."""
        messages = [
            {"role": "system", "content": NL_TO_CHECK_SYSTEM},
            {"role": "user", "content": NL_TO_CHECK_USER.format(request=request)},
        ]
        raw = await self.client.chat(messages, json_mode=True, temperature=0.0)
        data = extract_json(raw)
        # Server-owned fields: id/source/enabled are never trusted from the model.
        data["id"] = f"llm.{uuid.uuid4().hex[:10]}"
        data["source"] = "llm"
        data["enabled"] = False
        data.setdefault("description", f"Generated from: {request!r}")
        data["created_ts"] = now_ts()
        return Check.model_validate(data)  # raises if the model produced garbage

    async def answer(
        self, question: str, snap: ClusterSnapshot | None, events: list[Event]
    ) -> str:
        state = _summarize_state(snap)
        ev_txt = (
            "\n".join(f"- [{e.severity.value}] {e.message}" for e in events[:15]) or "(none)"
        )
        messages = [
            {"role": "system", "content": QA_SYSTEM},
            {"role": "user", "content": QA_USER.format(
                state=state, events=ev_txt, question=question)},
        ]
        return await self.client.chat(messages, temperature=0.2)

    async def explain(self, event: Event, snap: ClusterSnapshot | None) -> str:
        messages = [
            {"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": EXPLAIN_USER.format(
                event=event.model_dump_json(), state=_summarize_state(snap))},
        ]
        return await self.client.chat(messages, temperature=0.2)


def _summarize_state(snap: ClusterSnapshot | None) -> str:
    """Compact, token-friendly view of the snapshot for grounding."""
    if snap is None:
        return "(no data collected yet)"
    lines = [f"quorate={snap.quorate}"]
    for n in snap.nodes:
        lines.append(
            f"node {n.node}: cpu={n.cpu_pct:.0f}% mem={n.mem_pct:.0f}% "
            f"disk={n.disk_pct:.0f}% status={n.status.value}"
        )
    for v in snap.vms:
        lines.append(
            f"vm {v.vmid} {v.name} on {v.node}: cpu={v.cpu_pct:.0f}% "
            f"mem={v.mem_pct:.0f}% status={v.status.value}"
        )
    for s in snap.storage:
        lines.append(f"storage {s.storage}@{s.node}: used={s.used_pct:.0f}%")
    return "\n".join(lines)
