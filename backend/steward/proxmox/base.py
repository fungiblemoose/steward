"""The Proxmox client protocol.

Both the mock simulator and the real proxmoxer-backed client implement this.
The rest of Steward only ever talks to this interface, so swapping mock for
real (or, later, a different hypervisor entirely) touches nothing else.

All methods are async. Mutating methods are only ever invoked by the action
executor, and only after every guardrail in :mod:`steward.actions` has passed.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from steward.models import ClusterSnapshot, NodeMetric, StorageMetric, VMMetric


class ProxmoxError(RuntimeError):
    """Raised by client implementations on an unrecoverable client-side error."""


@runtime_checkable
class ProxmoxClient(Protocol):
    # ---- read ----
    async def get_cluster_resources(self) -> ClusterSnapshot:
        """One consistent snapshot of nodes, VMs/CTs, and storage."""
        ...

    async def get_node_status(self, node: str) -> NodeMetric: ...

    async def get_vms(self) -> list[VMMetric]: ...

    async def get_storage(self) -> list[StorageMetric]: ...

    # ---- mutate (guarded by the executor; never called in dry-run) ----
    async def migrate_vm(self, vmid: int, target: str, online: bool = True) -> None: ...

    async def set_vm_power(self, vmid: int, state: str) -> None:
        """state in {start, stop, shutdown, reboot}."""
        ...

    async def set_balloon(self, vmid: int, mb: int) -> None: ...

    # ---- lifecycle ----
    async def close(self) -> None: ...
