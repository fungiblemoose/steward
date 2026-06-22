from __future__ import annotations

from steward.models import ActionStatus


async def test_poll_once_persists_and_seeds(steward):
    snap = await steward.poll_once()
    assert snap is not None
    assert steward.latest is not None
    # builtins seeded
    ids = {c.id for c in steward.store.list_checks()}
    assert "builtin.node_cpu_pressure" in ids
    # metrics persisted
    series = steward.store.metric_series("node", "pve-1")
    assert len(series) >= 1


async def test_cpu_pressure_event_and_suggestion(steward):
    # drive pve-1 hot
    for vmid in (101, 102, 103):
        steward.client.inject_load(vmid=vmid, cpu_pct=100)
    fired = False
    for _ in range(8):
        await steward.poll_once()
        events = steward.store.list_events(check_id="builtin.node_cpu_pressure")
        if events:
            fired = True
            break
    assert fired, "CPU pressure event never fired"
    # a migration was suggested into the approval queue (auto_execute=False builtin)
    proposed = steward.store.list_actions(status=ActionStatus.proposed.value)
    assert any(a.type.value == "migrate" for a in proposed)


async def test_flags_toggle_persist(steward):
    steward.set_paused(True)
    assert steward.is_paused() is True
    steward.set_dry_run(False)
    assert steward.is_dry_run() is False
    steward.set_allowlist([1, 2, 3])
    assert steward.allowlist() == [1, 2, 3]


async def test_no_auto_execute_for_builtin_suggestions(steward):
    """Builtin suggestions must land in the queue, never auto-fire."""
    for vmid in (101, 102, 103):
        steward.client.inject_load(vmid=vmid, cpu_pct=100)
    for _ in range(8):
        await steward.poll_once()
    executed = steward.store.list_actions(status=ActionStatus.executed.value)
    assert executed == []  # nothing executed automatically
