from __future__ import annotations

import pytest

from steward.proxmox.base import ProxmoxError
from steward.proxmox.fixtures import default_cluster
from steward.proxmox.mock import MockProxmoxClient


async def test_snapshot_shape(mock_client):
    snap = await mock_client.get_cluster_resources()
    assert len(snap.nodes) == 3
    assert len(snap.vms) == 8
    assert snap.quorate is True
    assert all(0 <= n.cpu_pct <= 100 for n in snap.nodes)


async def test_migration_moves_vm(mock_client):
    before = await mock_client.get_cluster_resources()
    vm = next(v for v in before.vms if v.vmid == 101)
    assert vm.node == "pve-1"
    await mock_client.migrate_vm(101, "pve-2")
    after = await mock_client.get_cluster_resources()
    assert next(v for v in after.vms if v.vmid == 101).node == "pve-2"


async def test_migration_rejects_unknown_target(mock_client):
    with pytest.raises(ProxmoxError):
        await mock_client.migrate_vm(101, "does-not-exist")


async def test_migration_rejects_same_node(mock_client):
    with pytest.raises(ProxmoxError):
        await mock_client.migrate_vm(101, "pve-1")


async def test_migration_rejects_offline_target(mock_client):
    mock_client.set_node_online("pve-2", False)
    with pytest.raises(ProxmoxError):
        await mock_client.migrate_vm(101, "pve-2")


async def test_power_toggle(mock_client):
    await mock_client.set_vm_power(101, "stop")
    snap = await mock_client.get_cluster_resources()
    assert next(v for v in snap.vms if v.vmid == 101).status.value == "stopped"
    await mock_client.set_vm_power(101, "start")
    snap = await mock_client.get_cluster_resources()
    assert next(v for v in snap.vms if v.vmid == 101).status.value == "running"


async def test_balloon(mock_client):
    await mock_client.set_balloon(101, 2048)
    snap = await mock_client.get_cluster_resources()
    vm = next(v for v in snap.vms if v.vmid == 101)
    assert vm.mem_max_mb == 2048


async def test_load_injection_drives_pressure():
    client = MockProxmoxClient(default_cluster(), seed=2, drift=True)
    client.inject_load(vmid=101, cpu_pct=100)
    client.inject_load(vmid=102, cpu_pct=100)
    client.inject_load(vmid=103, cpu_pct=100)
    last = None
    for _ in range(8):
        snap = await client.get_cluster_resources()
        last = next(n for n in snap.nodes if n.node == "pve-1")
    assert last.cpu_pct > 50


def test_protocol_conformance():
    from steward.proxmox.base import ProxmoxClient

    assert isinstance(MockProxmoxClient(), ProxmoxClient)
