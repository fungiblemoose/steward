from __future__ import annotations

import pytest

from steward.config import Settings
from steward.proxmox.mock import MockProxmoxClient
from steward.proxmox.fixtures import default_cluster
from steward.runtime import Steward
from steward.store.db import Store


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "test.db"),
        proxmox_mode="mock",
        dry_run=True,
        paused=False,
        action_allowlist=[101, 102, 103, 201, 202, 203, 301, 302],
        poll_interval_s=0.05,
        llm_base_url="",  # disabled by default in tests
        action_cooldown_s=0.0,
        action_max_per_hour=1000,
    )


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(str(tmp_path / "store.db"))
    yield s
    s.close()


@pytest.fixture
def mock_client() -> MockProxmoxClient:
    # deterministic, no drift so tests assert exact behaviour
    return MockProxmoxClient(default_cluster(), seed=1, drift=False)


@pytest.fixture
def steward(settings) -> Steward:
    sw = Steward(settings)
    yield sw
    sw.store.close()
