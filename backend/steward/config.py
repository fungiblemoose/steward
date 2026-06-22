"""Environment-driven configuration for Steward.

All settings come from environment variables (prefixed ``STEWARD_``) or a
``.env`` file. Nothing sensitive is ever hard-coded. See ``.env.example`` for
the full list of knobs with safe placeholder values.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STEWARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ----
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    db_path: str = "./data/steward.db"

    # ---- Proxmox ----
    proxmox_mode: Literal["mock", "real"] = "mock"
    proxmox_host: str = "proxmox.example.local"
    proxmox_port: int = 8006
    proxmox_user: str = "monitor@pve"
    proxmox_token_name: str = ""
    proxmox_token_value: str = ""
    proxmox_password: str = ""
    proxmox_verify_ssl: bool = True

    # ---- Collector ----
    poll_interval_s: float = 10.0
    ring_buffer_size: int = 360

    # ---- Demo ----
    # When true (mock only), scripts periodic incidents for screenshots/gifs.
    demo_mode: bool = False

    # ---- Retention ----
    metrics_retention_hours: int = 72

    # ---- Predictive pressure (deterministic EWMA forecast) ----
    predictive_enabled: bool = True
    predictive_threshold: float = 90.0
    predictive_lookahead_s: float = 1800.0
    predictive_cooldown_s: float = 600.0

    # ---- Action guardrails ----
    dry_run: bool = True
    paused: bool = False
    action_allowlist: list[int] = Field(default_factory=list)
    action_cooldown_s: float = 300.0
    action_max_per_hour: int = 10

    # ---- Autonomous balancer (Tier 0) ----
    # The builtin.autonomous_balancer check is the on/off switch (disabled by
    # default). These tune *how* it balances once enabled.
    balancer_weight_cpu: float = 0.5         # blended-imbalance weights (CPU vs mem)
    balancer_weight_mem: float = 0.5
    balancer_max_target_pct: float = 80.0    # never migrate onto a node past this CPU/mem%
    balancer_min_improvement: float = 2.0    # min blended-imbalance drop to bother moving
    balancer_max_moves_per_cycle: int = 1    # moves initiated per poll (converge gradually)
    balancer_require_trend: bool = True      # only act when imbalance is rising/sustained
    balancer_migration_settle_s: float = 120.0  # wait for a recent migration to settle first

    # ---- LLM ----
    llm_base_url: str = ""
    llm_model: str = "llama3.2:3b"
    llm_api_key: str = "ollama"
    llm_timeout_s: float = 60.0

    # ---- Notifications ----
    notify_kind: Literal["none", "ntfy", "webhook"] = "none"
    notify_ntfy_url: str = ""
    notify_webhook_url: str = ""

    # ---- Escalation to Claude Code (Tier 2) ----
    # Off unless a webhook URL is set. Only *repeated, unresolved* incidents
    # escalate — a single blip never pages anyone.
    escalation_webhook_url: str = ""
    escalation_timeout_s: float = 15.0
    escalation_min_occurrences: int = 3      # fires this many times...
    escalation_min_age_s: float = 600.0      # ...over at least this long...
    escalation_cooldown_s: float = 3600.0    # ...and don't re-page within this
    escalation_ttl_s: float = 1800.0         # quiet this long => treat as resolved

    # ---- Auth ----
    auth_token: str = ""

    @field_validator("action_allowlist", mode="before")
    @classmethod
    def _parse_allowlist(cls, v: object) -> object:
        """Accept a comma-separated string (from env) or a list."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url.strip())

    @property
    def escalation_enabled(self) -> bool:
        return bool(self.escalation_webhook_url.strip())

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_token.strip())


@lru_cache
def get_settings() -> Settings:
    """Cached singleton accessor. Tests can clear via ``get_settings.cache_clear()``."""
    return Settings()
