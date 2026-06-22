from __future__ import annotations

import pytest

from steward.actions.executor import ActionExecutor, RuntimeFlags
from steward.models import ActionRequest, ActionStatus, ActionType
from steward.notify import NoopNotifier
from steward.proxmox.fixtures import default_cluster
from steward.proxmox.mock import MockProxmoxClient


class Flags:
    """Mutable flag holder for tests."""

    def __init__(self, snap):
        self.paused = False
        self.dry_run = True
        self.allowlist = [101]
        self.cooldown_s = 0.0
        self.max_per_hour = 1000
        self.snap = snap


def make_executor(store, settings, flags: Flags, client=None):
    client = client or MockProxmoxClient(default_cluster(), seed=3, drift=False)
    rf = RuntimeFlags(
        paused=lambda: flags.paused,
        dry_run=lambda: flags.dry_run,
        allowlist=lambda: flags.allowlist,
        cooldown_s=lambda: flags.cooldown_s,
        max_per_hour=lambda: flags.max_per_hour,
        snapshot=lambda: flags.snap,
    )
    return ActionExecutor(settings, store, client, NoopNotifier(), rf), client


@pytest.fixture
async def ctx(store, settings):
    client = MockProxmoxClient(default_cluster(), seed=3, drift=False)
    snap = await client.get_cluster_resources()
    flags = Flags(snap)
    executor, _ = make_executor(store, settings, flags, client)
    return executor, flags, client, snap


async def test_dry_run_does_not_mutate(ctx):
    executor, flags, client, snap = ctx
    flags.dry_run = True
    req = ActionRequest(type=ActionType.migrate, params={"vmid": 101, "target": "pve-2"},
                        auto_execute=True)
    rec = await executor.run(req, approved_by_human=True)
    assert rec.status == ActionStatus.executed
    assert rec.dry_run is True
    assert rec.after["node"] == "pve-2"  # simulated
    # real cluster unchanged
    fresh = await client.get_cluster_resources()
    assert next(v for v in fresh.vms if v.vmid == 101).node == "pve-1"


async def test_real_run_mutates(ctx):
    executor, flags, client, snap = ctx
    flags.dry_run = False
    req = ActionRequest(type=ActionType.migrate, params={"vmid": 101, "target": "pve-2"},
                        auto_execute=True)
    rec = await executor.run(req, approved_by_human=True)
    assert rec.status == ActionStatus.executed and rec.dry_run is False
    assert rec.after["node"] == "pve-2"  # audited after-state read from client
    fresh = await client.get_cluster_resources()
    assert next(v for v in fresh.vms if v.vmid == 101).node == "pve-2"


async def test_kill_switch_blocks(ctx):
    executor, flags, client, snap = ctx
    flags.paused = True
    req = ActionRequest(type=ActionType.migrate, params={"vmid": 101, "target": "pve-2"},
                        auto_execute=True)
    rec = await executor.run(req, approved_by_human=True)
    assert rec.status == ActionStatus.blocked
    assert "kill switch" in rec.outcome.lower()


async def test_allowlist_blocks_auto_action(ctx):
    executor, flags, client, snap = ctx
    flags.allowlist = [101]
    # 202 is not allow-listed -> auto path blocked
    req = ActionRequest(type=ActionType.migrate, params={"vmid": 202, "target": "pve-1"},
                        source="rule", auto_execute=True)
    rec = await executor.run(req, approved_by_human=False)
    assert rec.status == ActionStatus.blocked
    assert "allow-list" in rec.outcome.lower()


async def test_human_approval_overrides_allowlist(ctx):
    executor, flags, client, snap = ctx
    flags.allowlist = []  # nothing allow-listed
    req = ActionRequest(type=ActionType.migrate, params={"vmid": 202, "target": "pve-1"})
    rec = await executor.run(req, approved_by_human=True)
    assert rec.status == ActionStatus.executed  # human consent overrides allow-list


async def test_cooldown_blocks_second_action(ctx):
    executor, flags, client, snap = ctx
    flags.cooldown_s = 9999
    req1 = ActionRequest(type=ActionType.balloon, params={"vmid": 101, "mb": 2048},
                         auto_execute=True)
    rec1 = await executor.run(req1, approved_by_human=True)
    assert rec1.status == ActionStatus.executed
    req2 = ActionRequest(type=ActionType.balloon, params={"vmid": 101, "mb": 4096},
                         auto_execute=True)
    rec2 = await executor.run(req2, approved_by_human=True)
    assert rec2.status == ActionStatus.blocked and "cooldown" in rec2.outcome.lower()


async def test_rate_limit_blocks(ctx):
    executor, flags, client, snap = ctx
    flags.cooldown_s = 0
    flags.max_per_hour = 2
    for _ in range(2):
        rec = await executor.run(
            ActionRequest(type=ActionType.notify, params={"message": "hi"}, auto_execute=True),
            approved_by_human=True,
        )
        assert rec.status == ActionStatus.executed
    rec = await executor.run(
        ActionRequest(type=ActionType.notify, params={"message": "hi"}, auto_execute=True),
        approved_by_human=True,
    )
    assert rec.status == ActionStatus.blocked and "rate limit" in rec.outcome.lower()


async def test_propose_then_approve(ctx):
    executor, flags, client, snap = ctx
    rec = executor.propose(ActionRequest(
        type=ActionType.migrate, params={"vmid": 101, "target": "pve-3"}, source="rule"))
    assert rec.status == ActionStatus.proposed
    approved = await executor.approve(rec.id)
    assert approved.status == ActionStatus.executed
    assert approved.after["node"] == "pve-3"


async def test_reject(ctx):
    executor, flags, client, snap = ctx
    rec = executor.propose(ActionRequest(type=ActionType.power, params={"vmid": 101, "state": "stop"}))
    rejected = executor.reject(rec.id)
    assert rejected.status == ActionStatus.rejected


async def test_migration_planner_resolves_target(ctx):
    executor, flags, client, snap = ctx
    # strategy with a node, no explicit vmid/target -> planner fills both
    req = ActionRequest(
        type=ActionType.migrate,
        params={"strategy": "busiest_vm_to_least_loaded_node", "node": "pve-1"},
        auto_execute=True,
    )
    rec = await executor.run(req, approved_by_human=True)
    assert rec.status == ActionStatus.executed
    assert "vmid" in rec.params and "target" in rec.params
    assert rec.params["target"] != "pve-1"


async def test_audit_row_written_for_blocked(ctx):
    executor, flags, client, snap = ctx
    flags.paused = True
    await executor.run(
        ActionRequest(type=ActionType.notify, params={"message": "x"}, auto_execute=True),
        approved_by_human=True,
    )
    rows = executor.store.list_actions()
    assert len(rows) == 1 and rows[0].status == ActionStatus.blocked
    assert rows[0].reversibility  # reversibility note always present
