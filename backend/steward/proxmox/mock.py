"""In-memory Proxmox simulator.

Serves fixture data and maintains a mutable fake cluster so migration, power,
and balloon actions are observable end-to-end without touching real infra.
Includes a *load injector* so tests (and the ``--demo`` mode) can drive a node
into pressure and assert the rule engine reacts.

The simulation is intentionally simple and deterministic when seeded: each
``step`` nudges every running VM's CPU/mem toward a target operating point with
a little noise; node-level metrics are derived from the VMs they host plus a
host baseline.
"""
from __future__ import annotations

import asyncio
import random
from typing import Optional

from steward.models import (
    ClusterSnapshot,
    NodeMetric,
    NodeStatus,
    StorageMetric,
    VMKind,
    VMMetric,
    VMStatus,
    now_ts,
)
from steward.proxmox.base import ProxmoxError
from steward.proxmox.fixtures import FakeCluster, FakeVM, default_cluster


class MockProxmoxClient:
    """Implements the :class:`ProxmoxClient` protocol against a fake cluster."""

    def __init__(
        self,
        cluster: Optional[FakeCluster] = None,
        *,
        seed: Optional[int] = None,
        drift: bool = True,
    ) -> None:
        self.cluster = cluster or default_cluster()
        self._rng = random.Random(seed)
        self._drift = drift
        self._lock = asyncio.Lock()
        # injected pressure overrides: vmid -> (cpu_pct, mem_pct)
        self._injected: dict[int, tuple[Optional[float], Optional[float]]] = {}

    # ------------------------------------------------------------------ #
    # Simulation
    # ------------------------------------------------------------------ #
    def step(self) -> None:
        """Advance the simulation by one tick (pure, synchronous)."""
        for vm in self.cluster.vms:
            if not vm.running:
                vm.cpu_pct = 0.0
                vm.mem_used_mb = 0.0
                continue
            inj = self._injected.get(vm.vmid)
            target_cpu = inj[0] if inj and inj[0] is not None else vm.target_cpu_pct
            target_mem_pct = inj[1] if inj and inj[1] is not None else vm.target_mem_pct
            vm.cpu_pct = _approach(vm.cpu_pct, target_cpu, self._rng, jitter=4.0)
            target_mem_mb = vm.mem_max_mb * target_mem_pct / 100.0
            vm.mem_used_mb = _approach(
                vm.mem_used_mb, target_mem_mb, self._rng, jitter=vm.mem_max_mb * 0.02
            )
            vm.mem_used_mb = min(vm.mem_used_mb, vm.mem_max_mb)

    def _node_metric(self, node) -> NodeMetric:
        vms = [v for v in self.cluster.vms if v.node == node.name and v.running]
        # CPU: host baseline + core-weighted average of guest CPU, capped at 100.
        guest_cpu = sum(v.cpu_pct * v.cores for v in vms)
        cpu = node.base_cpu_pct + (guest_cpu / node.cpu_cores if node.cpu_cores else 0)
        cpu = max(0.0, min(100.0, cpu))
        mem_used = node.base_mem_mb + sum(v.mem_used_mb for v in vms)
        mem_used = min(mem_used, node.mem_total_mb)
        disk_used = sum(
            s.used_gb for s in self.cluster.storage if s.node == node.name and not s.shared
        )
        return NodeMetric(
            node=node.name,
            status=NodeStatus.online if node.online else NodeStatus.offline,
            cpu_pct=round(cpu, 2),
            mem_used_mb=round(mem_used, 1),
            mem_total_mb=node.mem_total_mb,
            disk_used_gb=round(disk_used, 1),
            disk_total_gb=node.disk_total_gb,
            uptime_s=86_400.0,
        )

    def _snapshot(self) -> ClusterSnapshot:
        nodes = [self._node_metric(n) for n in self.cluster.nodes]
        vms = [
            VMMetric(
                vmid=v.vmid,
                name=v.name,
                node=v.node,
                kind=VMKind(v.kind),
                status=VMStatus.running if v.running else VMStatus.stopped,
                cpu_pct=round(v.cpu_pct, 2) if v.running else 0.0,
                mem_used_mb=round(v.mem_used_mb, 1) if v.running else 0.0,
                mem_max_mb=v.mem_max_mb,
                cores=v.cores,
            )
            for v in self.cluster.vms
        ]
        storage = [
            StorageMetric(
                storage=s.name,
                node=s.node,
                used_gb=s.used_gb,
                total_gb=s.total_gb,
                shared=s.shared,
            )
            for s in self.cluster.storage
        ]
        return ClusterSnapshot(
            ts=now_ts(), quorate=self.cluster.quorate, nodes=nodes, vms=vms, storage=storage
        )

    # ------------------------------------------------------------------ #
    # Test / demo controls (not part of the ProxmoxClient protocol)
    # ------------------------------------------------------------------ #
    def inject_load(
        self, *, vmid: int, cpu_pct: Optional[float] = None, mem_pct: Optional[float] = None
    ) -> None:
        """Force a VM toward a CPU/mem operating point to create pressure."""
        self._injected[vmid] = (cpu_pct, mem_pct)

    def clear_load(self, vmid: Optional[int] = None) -> None:
        if vmid is None:
            self._injected.clear()
        else:
            self._injected.pop(vmid, None)

    def set_node_online(self, node: str, online: bool) -> None:
        for n in self.cluster.nodes:
            if n.name == node:
                n.online = online

    def set_quorate(self, quorate: bool) -> None:
        self.cluster.quorate = quorate

    def set_storage_used(self, storage: str, node: str, used_gb: float) -> None:
        for s in self.cluster.storage:
            if s.name == storage and s.node == node:
                s.used_gb = used_gb

    def _find_vm(self, vmid: int) -> FakeVM:
        for v in self.cluster.vms:
            if v.vmid == vmid:
                return v
        raise ProxmoxError(f"no such vmid: {vmid}")

    def node_names(self) -> list[str]:
        return [n.name for n in self.cluster.nodes]

    # ------------------------------------------------------------------ #
    # ProxmoxClient protocol — read
    # ------------------------------------------------------------------ #
    async def get_cluster_resources(self) -> ClusterSnapshot:
        async with self._lock:
            if self._drift:
                self.step()
            return self._snapshot()

    async def get_node_status(self, node: str) -> NodeMetric:
        async with self._lock:
            for n in self.cluster.nodes:
                if n.name == node:
                    return self._node_metric(n)
        raise ProxmoxError(f"no such node: {node}")

    async def get_vms(self) -> list[VMMetric]:
        return (await self.get_cluster_resources()).vms

    async def get_storage(self) -> list[StorageMetric]:
        return (await self.get_cluster_resources()).storage

    # ------------------------------------------------------------------ #
    # ProxmoxClient protocol — mutate
    # ------------------------------------------------------------------ #
    async def migrate_vm(self, vmid: int, target: str, online: bool = True) -> None:
        async with self._lock:
            vm = self._find_vm(vmid)
            if target not in self.node_names():
                raise ProxmoxError(f"no such target node: {target}")
            if target == vm.node:
                raise ProxmoxError(f"vm {vmid} already on {target}")
            tnode = next(n for n in self.cluster.nodes if n.name == target)
            if not tnode.online:
                raise ProxmoxError(f"target node {target} is offline")
            vm.node = target

    async def set_vm_power(self, vmid: int, state: str) -> None:
        async with self._lock:
            vm = self._find_vm(vmid)
            if state in {"start", "reboot"}:
                vm.running = True
                vm.cpu_pct = vm.target_cpu_pct
                vm.mem_used_mb = vm.mem_max_mb * vm.target_mem_pct / 100.0
            elif state in {"stop", "shutdown"}:
                vm.running = False
            else:
                raise ProxmoxError(f"unknown power state: {state}")

    async def set_balloon(self, vmid: int, mb: int) -> None:
        async with self._lock:
            vm = self._find_vm(vmid)
            if mb <= 0:
                raise ProxmoxError("balloon target must be positive")
            vm.mem_max_mb = float(mb)
            vm.mem_used_mb = min(vm.mem_used_mb, vm.mem_max_mb)

    async def close(self) -> None:  # nothing to clean up
        return None


def _approach(current: float, target: float, rng: random.Random, jitter: float) -> float:
    """Move ``current`` ~40% of the way to ``target`` plus bounded noise."""
    nxt = current + (target - current) * 0.4 + rng.uniform(-jitter, jitter)
    return max(0.0, nxt)
