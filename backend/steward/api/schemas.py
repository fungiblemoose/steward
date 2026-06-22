"""Request/response bodies for the API that aren't already domain models."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from steward.models import ActionType


class FlagsUpdate(BaseModel):
    paused: Optional[bool] = None
    dry_run: Optional[bool] = None
    allowlist: Optional[list[int]] = None


class ActionCreate(BaseModel):
    type: ActionType
    params: dict[str, Any] = {}
    reason: str = ""
    # "propose" -> approval queue; "run" -> execute now (human-approved path).
    mode: str = "propose"


class AskRequest(BaseModel):
    question: str


class NLCheckRequest(BaseModel):
    request: str


class ChatResponse(BaseModel):
    answer: str
