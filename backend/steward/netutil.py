"""Tiny shared HTTP helper for fire-and-forget outbound POSTs.

Notifications and escalation must never break the collector loop, so this POSTs
JSON and swallows+logs any failure. Used by both the webhook notifier and the
escalator so the "open client, post, swallow" body lives in one place.
"""
from __future__ import annotations

import logging

import httpx


async def post_json(url: str, payload: dict, *, timeout: float = 10.0,
                    log: logging.Logger | None = None) -> None:
    log = log or logging.getLogger("steward.net")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(url, json=payload)
    except Exception as exc:  # never let an outbound POST break the loop
        log.warning("POST %s failed: %s", url, exc)
