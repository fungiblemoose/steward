"""OpenAI-compatible chat client (works with Ollama, llama.cpp, vLLM, OpenAI).

We talk raw HTTP via httpx against ``POST {base_url}/chat/completions`` rather
than pulling in the openai SDK — fewer deps and identical behaviour against
local servers. A :class:`FakeLLMClient` lets tests exercise the LLM features
deterministically with no model running.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Protocol

import httpx

from steward.config import Settings

log = logging.getLogger("steward.llm")


class LLMClient(Protocol):
    async def chat(
        self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.2
    ) -> str: ...

    @property
    def enabled(self) -> bool: ...


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str, api_key: str, timeout_s: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def chat(
        self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.2
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            # Supported by OpenAI and recent Ollama; harmless if ignored.
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]


class FakeLLMClient:
    """Deterministic stub for tests. Returns canned responses by intent.

    ``scripted`` maps a substring found in the latest user message to a reply.
    Falls back to ``default``.
    """

    def __init__(self, scripted: Optional[dict[str, str]] = None, default: str = "ok") -> None:
        self.scripted = scripted or {}
        self.default = default
        self.calls: list[list[dict]] = []

    @property
    def enabled(self) -> bool:
        return True

    async def chat(
        self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.2
    ) -> str:
        self.calls.append(messages)
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        for needle, reply in self.scripted.items():
            if needle.lower() in user.lower():
                return reply
        return self.default


class _DisabledClient:
    @property
    def enabled(self) -> bool:
        return False

    async def chat(self, *a, **k) -> str:  # pragma: no cover - guarded by callers
        raise RuntimeError("LLM is not configured")


def build_llm_client(settings: Settings) -> LLMClient:
    if not settings.llm_enabled:
        return _DisabledClient()
    return OpenAICompatClient(
        settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout_s
    )


def extract_json(text: str) -> dict:
    """Best-effort JSON extraction from a model reply (handles code fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
