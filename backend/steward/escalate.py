"""Tier-2 escalation: hand an unresolved incident to an external agent.

When the deterministic tiers can't quiet an incident, Steward POSTs a rich
incident payload to a webhook that kicks off a **Claude Code run** (on whatever
remote-agent / cron / CI machinery the operator wires up). Claude then
investigates through Steward's read API and *proposes* remediation into the
approval queue — it never bypasses the executor guardrails.

This is intentionally a thin, fire-and-forget POST: escalation must never block
or break the collector loop, so failures are logged and swallowed. It is off
unless ``STEWARD_ESCALATION_WEBHOOK_URL`` is set.
"""
from __future__ import annotations

import logging
from typing import Protocol

from steward.config import Settings
from steward.netutil import post_json

log = logging.getLogger("steward.escalate")


class Escalator(Protocol):
    async def escalate(self, payload: dict) -> None: ...


class NoopEscalator:
    async def escalate(self, payload: dict) -> None:
        log.debug("escalate (noop): %s", payload.get("incident", {}).get("check_id"))


class WebhookEscalator:
    def __init__(self, url: str, timeout_s: float = 15.0) -> None:
        self.url = url
        self.timeout_s = timeout_s

    async def escalate(self, payload: dict) -> None:
        await post_json(self.url, payload, timeout=self.timeout_s, log=log)


def build_escalator(settings: Settings) -> Escalator:
    if settings.escalation_enabled:
        return WebhookEscalator(settings.escalation_webhook_url, settings.escalation_timeout_s)
    return NoopEscalator()
