"""Fixture data for the mock cluster.

Deliberately generic: node names are ``pve-1..n``, VMs are ordinary roles, no
real hostnames/IPs/storage names. Anyone can read this in a public repo.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeNode:
    name: str
    cpu_cores: int
    mem_total_mb: float
    disk_total_gb: float
    online: bool = True
    # base idle load contributed by the host itself
    base_cpu_pct: float = 4.0
    base_mem_mb: float = 1500.0


@dataclass
class FakeVM:
    vmid: int
    name: str
    node: str
    kind: str = "qemu"          # qemu | lxc
    cores: int = 2
    mem_max_mb: float = 4096.0
    running: bool = True
    # target operating point the simulator drifts around
    target_cpu_pct: float = 15.0
    target_mem_pct: float = 45.0
    # current values (mutated by the simulator each tick)
    cpu_pct: float = 15.0
    mem_used_mb: float = 1800.0


@dataclass
class FakeStorage:
    name: str
    node: str
    total_gb: float
    used_gb: float
    shared: bool = False


@dataclass
class FakeCluster:
    nodes: list[FakeNode] = field(default_factory=list)
    vms: list[FakeVM] = field(default_factory=list)
    storage: list[FakeStorage] = field(default_factory=list)
    quorate: bool = True


def default_cluster() -> FakeCluster:
    """A realistic 3-node cluster with shared storage and a mix of VMs/CTs."""
    nodes = [
        FakeNode("pve-1", cpu_cores=16, mem_total_mb=64_000, disk_total_gb=480),
        FakeNode("pve-2", cpu_cores=16, mem_total_mb=64_000, disk_total_gb=480),
        FakeNode("pve-3", cpu_cores=8, mem_total_mb=32_000, disk_total_gb=240),
    ]
    vms = [
        FakeVM(101, "web-frontend", "pve-1", cores=6, mem_max_mb=8192,
               target_cpu_pct=35, target_mem_pct=55, cpu_pct=33, mem_used_mb=4500),
        FakeVM(102, "api-server", "pve-1", cores=6, mem_max_mb=8192,
               target_cpu_pct=40, target_mem_pct=60, cpu_pct=42, mem_used_mb=4900),
        FakeVM(103, "postgres", "pve-1", cores=4, mem_max_mb=16384,
               target_cpu_pct=25, target_mem_pct=70, cpu_pct=24, mem_used_mb=11400),
        FakeVM(201, "worker-a", "pve-2", cores=4, mem_max_mb=8192,
               target_cpu_pct=30, target_mem_pct=50, cpu_pct=29, mem_used_mb=4100),
        FakeVM(202, "worker-b", "pve-2", cores=4, mem_max_mb=8192,
               target_cpu_pct=30, target_mem_pct=50, cpu_pct=31, mem_used_mb=4050),
        FakeVM(203, "cache", "pve-2", kind="lxc", cores=2, mem_max_mb=4096,
               target_cpu_pct=10, target_mem_pct=40, cpu_pct=9, mem_used_mb=1600),
        FakeVM(301, "monitoring", "pve-3", cores=2, mem_max_mb=4096,
               target_cpu_pct=20, target_mem_pct=55, cpu_pct=21, mem_used_mb=2200),
        FakeVM(302, "backup", "pve-3", kind="lxc", cores=2, mem_max_mb=4096,
               target_cpu_pct=8, target_mem_pct=35, cpu_pct=7, mem_used_mb=1400),
    ]
    storage = [
        FakeStorage("local", "pve-1", total_gb=480, used_gb=210, shared=False),
        FakeStorage("local", "pve-2", total_gb=480, used_gb=190, shared=False),
        FakeStorage("local", "pve-3", total_gb=240, used_gb=120, shared=False),
        FakeStorage("ceph-pool", "pve-1", total_gb=4000, used_gb=1850, shared=True),
        FakeStorage("ceph-pool", "pve-2", total_gb=4000, used_gb=1850, shared=True),
        FakeStorage("ceph-pool", "pve-3", total_gb=4000, used_gb=1850, shared=True),
    ]
    return FakeCluster(nodes=nodes, vms=vms, storage=storage, quorate=True)
