"""Pluggable notifications. Defaults to a no-op so nothing leaves the box."""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from steward.config import Settings
from steward.models import Severity

log = logging.getLogger("steward.notify")


class Notifier(Protocol):
    async def send(self, title: str, message: str, severity: Severity) -> None: ...


class NoopNotifier:
    async def send(self, title: str, message: str, severity: Severity) -> None:
        log.debug("notify (noop): %s — %s", title, message)


class NtfyNotifier:
    def __init__(self, url: str) -> None:
        self.url = url

    async def send(self, title: str, message: str, severity: Severity) -> None:
        priority = {"info": "default", "warning": "high", "critical": "urgent"}.get(
            severity.value, "default"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    self.url,
                    content=message.encode("utf-8"),
                    headers={"Title": title, "Priority": priority, "Tags": severity.value},
                )
        except Exception as exc:  # never let notification failure break the loop
            log.warning("ntfy send failed: %s", exc)


class WebhookNotifier:
    def __init__(self, url: str) -> None:
        self.url = url

    async def send(self, title: str, message: str, severity: Severity) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    self.url,
                    json={"title": title, "message": message, "severity": severity.value},
                )
        except Exception as exc:
            log.warning("webhook send failed: %s", exc)


def build_notifier(settings: Settings) -> Notifier:
    if settings.notify_kind == "ntfy" and settings.notify_ntfy_url:
        return NtfyNotifier(settings.notify_ntfy_url)
    if settings.notify_kind == "webhook" and settings.notify_webhook_url:
        return WebhookNotifier(settings.notify_webhook_url)
    return NoopNotifier()
