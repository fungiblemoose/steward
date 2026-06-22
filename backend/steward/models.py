"""Shared domain models used across collector, rules, actions, API, and store.

These are Pydantic models so they validate at the boundaries (API, LLM output)
and serialize cleanly to JSON for the UI and the SQLite audit trail.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def now_ts() -> float:
    """Single source of truth for timestamps (epoch seconds, float)."""
    return time.time()


# --------------------------------------------------------------------------- #
# Cluster state
# --------------------------------------------------------------------------- #
class NodeStatus(str, Enum):
    online = "online"
    offline = "offline"
    unknown = "unknown"


class VMStatus(str, Enum):
    running = "running"
    stopped = "stopped"
    paused = "paused"
    unknown = "unknown"


class VMKind(str, Enum):
    qemu = "qemu"
    lxc = "lxc"


class NodeMetric(BaseModel):
    """A point-in-time snapshot of one node."""

    node: str
    status: NodeStatus = NodeStatus.unknown
    cpu_pct: float = 0.0          # 0..100
    cpu_cores: int = 0            # physical cores (for core-weighted balancing math)
    mem_used_mb: float = 0.0
    mem_total_mb: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    uptime_s: float = 0.0
    ts: float = Field(default_factory=now_ts)

    @property
    def mem_pct(self) -> float:
        return 100.0 * self.mem_used_mb / self.mem_total_mb if self.mem_total_mb else 0.0

    @property
    def disk_pct(self) -> float:
        return 100.0 * self.disk_used_gb / self.disk_total_gb if self.disk_total_gb else 0.0


class VMMetric(BaseModel):
    """A point-in-time snapshot of one VM or container."""

    vmid: int
    name: str
    node: str
    kind: VMKind = VMKind.qemu
    status: VMStatus = VMStatus.unknown
    cpu_pct: float = 0.0          # 0..100 (of its allotted cores)
    mem_used_mb: float = 0.0
    mem_max_mb: float = 0.0
    cores: int = 1
    ts: float = Field(default_factory=now_ts)

    @property
    def mem_pct(self) -> float:
        return 100.0 * self.mem_used_mb / self.mem_max_mb if self.mem_max_mb else 0.0


class StorageMetric(BaseModel):
    """A point-in-time snapshot of one storage pool."""

    storage: str
    node: str
    used_gb: float = 0.0
    total_gb: float = 0.0
    shared: bool = False
    ts: float = Field(default_factory=now_ts)

    @property
    def used_pct(self) -> float:
        return 100.0 * self.used_gb / self.total_gb if self.total_gb else 0.0


class ClusterSnapshot(BaseModel):
    """Everything the collector pulls in one cycle."""

    ts: float = Field(default_factory=now_ts)
    quorate: bool = True
    nodes: list[NodeMetric] = Field(default_factory=list)
    vms: list[VMMetric] = Field(default_factory=list)
    storage: list[StorageMetric] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
class Severity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class Event(BaseModel):
    id: Optional[int] = None
    ts: float = Field(default_factory=now_ts)
    check_id: str
    check_name: str
    severity: Severity = Severity.warning
    target: str = ""
    message: str = ""
    value: Optional[float] = None
    # Free-form context captured at fire time (metric values, thresholds, ...).
    context: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Actions / audit
# --------------------------------------------------------------------------- #
class ActionType(str, Enum):
    migrate = "migrate"
    power = "power"
    balloon = "balloon"
    notify = "notify"


class ActionStatus(str, Enum):
    proposed = "proposed"        # awaiting approval
    approved = "approved"        # approved, queued to run
    executed = "executed"        # ran (possibly dry-run)
    rejected = "rejected"        # operator declined
    blocked = "blocked"          # a guardrail refused it
    failed = "failed"            # client raised


class ActionRequest(BaseModel):
    """A request to perform something. Params depend on ``type``."""

    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    # Provenance: where did this come from?
    source: Literal["rule", "llm", "manual"] = "manual"
    check_id: Optional[str] = None
    # If False, must go through the approval queue regardless of allow-list.
    auto_execute: bool = False


class ActionRecord(BaseModel):
    """The audit-trail row for a proposed/executed/rejected action."""

    id: Optional[int] = None
    ts: float = Field(default_factory=now_ts)
    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    source: str = "manual"
    check_id: Optional[str] = None
    status: ActionStatus = ActionStatus.proposed
    dry_run: bool = True
    outcome: str = ""
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    reversibility: str = ""
    resolved_at: Optional[float] = None
