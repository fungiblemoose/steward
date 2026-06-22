"""The check/probe schema. A check is *data*, not code.

A check describes a condition to evaluate against collected metrics (or, for
non-Proxmox probes, against an external target). The rule engine interprets
these deterministically; the LLM may *propose* them but they are validated
against this schema and routed through human approval before they ever run.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from steward.models import ActionType, Severity, now_ts


class ProbeType(str, Enum):
    proxmox_metric = "proxmox_metric"   # evaluate against the cluster snapshot
    http_get = "http_get"               # GET a URL, assert status/latency
    tcp_port = "tcp_port"               # assert a host:port is reachable
    process_cpu = "process_cpu"         # assert a VM/process CPU level
    shell_command = "shell_command"     # run a command, assert exit code


class ComparisonOp(str, Enum):
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    eq = "eq"
    ne = "ne"

    def apply(self, value: float, threshold: float) -> bool:
        return {
            ComparisonOp.gt: value > threshold,
            ComparisonOp.gte: value >= threshold,
            ComparisonOp.lt: value < threshold,
            ComparisonOp.lte: value <= threshold,
            ComparisonOp.eq: value == threshold,
            ComparisonOp.ne: value != threshold,
        }[self]


class Condition(BaseModel):
    """A comparison of one metric field against a threshold.

    ``metric`` names a field on the evaluated entity, e.g. ``cpu_pct``,
    ``mem_pct``, ``disk_pct``, ``used_pct``, or the special string-valued
    ``status`` / boolean ``quorate``.
    """

    metric: str
    op: ComparisonOp
    threshold: float = 0.0
    # For string/bool metrics (status == "stopped", quorate == false).
    threshold_str: Optional[str] = None


class SuggestedAction(BaseModel):
    """An action a check may *suggest* when it fires. Never auto-fires unless
    the check's ``auto_execute`` is set AND the target is allow-listed."""

    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class Check(BaseModel):
    id: str
    name: str
    probe_type: ProbeType = ProbeType.proxmox_metric
    # Scope selector, e.g. "node:*", "node:pve-1", "vm:101", "storage:*",
    # "cluster". For non-proxmox probes this holds the URL / host:port / etc.
    target: str = "node:*"
    condition: Condition
    severity: Severity = Severity.warning
    cooldown_s: float = 300.0
    suggested_action: Optional[SuggestedAction] = None
    auto_execute: bool = False
    enabled: bool = True
    source: str = "manual"  # builtin | llm | manual
    description: str = ""
    created_ts: float = Field(default_factory=now_ts)

    @field_validator("source")
    @classmethod
    def _valid_source(cls, v: str) -> str:
        if v not in {"builtin", "llm", "manual"}:
            raise ValueError("source must be one of builtin|llm|manual")
        return v


def parse_target(target: str) -> tuple[str, str]:
    """Split a proxmox target like ``node:pve-1`` into ``("node", "pve-1")``.

    A bare ``cluster`` (no colon) returns ``("cluster", "*")``. A trailing
    ``*`` selector means "match every entity in scope".
    """
    if ":" not in target:
        return target.strip(), "*"
    scope, _, selector = target.partition(":")
    return scope.strip(), (selector.strip() or "*")
