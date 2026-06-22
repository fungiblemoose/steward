"""Real Proxmox client backed by ``proxmoxer``.

GATED AND UNTESTED in this build — there is no live cluster to reach. It is
included so the abstraction is honest and the wiring is reviewable. It is only
constructed when ``STEWARD_PROXMOX_MODE=real`` and ``proxmoxer`` is installed
(an optional dependency). Every mutating call still passes through the action
executor's guardrails first.

Notes verified against the Proxmox VE API (https://pve.proxmox.com/pve-docs/api-viewer/):
  * Cluster-wide resource list: GET /cluster/resources?type=...
  * Node status:                GET /nodes/{node}/status
  * Live migration:             POST /nodes/{node}/qemu/{vmid}/migrate
                                with {target, online: 1} (LXC uses .../lxc/...).
    Live migration needs a valid target and, for local disks, the
    'with-local-disks' option (storage migration). With shared storage the
    move is memory-only. Refuse if no eligible target exists.
  * Power:                      POST /nodes/{node}/qemu/{vmid}/status/{start|stop|shutdown|reboot}
  * Balloon (memory):           POST /nodes/{node}/qemu/{vmid}/config with {balloon: <MB>}

HA interaction: if a VM is HA-managed, manual migration can conflict with the
HA manager. Production deployments should migrate via the HA stack
(/cluster/ha) or exclude HA-managed guests from auto-action. Documented here so
the two control loops don't fight; enforcement is left to the allow-list.
"""
from __future__ import annotations

from steward.config import Settings
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


class RealProxmoxClient:
    """Thin async wrapper over proxmoxer's synchronous API."""

    def __init__(self, settings: Settings) -> None:
        try:
            from proxmoxer import ProxmoxAPI  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dep
            raise ProxmoxError(
                "proxmoxer is not installed. Install with `pip install steward[proxmox]`."
            ) from exc
        if not settings.proxmox_token_value and not settings.proxmox_password:
            raise ProxmoxError("real Proxmox client requires a token or password")

        kwargs: dict = {
            "host": f"{settings.proxmox_host}:{settings.proxmox_port}",
            "user": settings.proxmox_user,
            "verify_ssl": settings.proxmox_verify_ssl,
            "service": "PVE",
        }
        if settings.proxmox_token_value:
            kwargs["token_name"] = settings.proxmox_token_name
            kwargs["token_value"] = settings.proxmox_token_value
        else:
            kwargs["password"] = settings.proxmox_password

        from proxmoxer import ProxmoxAPI

        self._api = ProxmoxAPI(**kwargs)

    async def _call(self, fn, *args, **kwargs):
        import asyncio

        return await asyncio.to_thread(fn, *args, **kwargs)

    # ---- read ----
    async def get_cluster_resources(self) -> ClusterSnapshot:
        rows = await self._call(self._api.cluster.resources.get)
        nodes: list[NodeMetric] = []
        vms: list[VMMetric] = []
        storage: list[StorageMetric] = []
        for r in rows:
            rtype = r.get("type")
            if rtype == "node":
                nodes.append(_node_from_row(r))
            elif rtype in ("qemu", "lxc"):
                vms.append(_vm_from_row(r))
            elif rtype == "storage":
                storage.append(_storage_from_row(r))
        quorate = await self._cluster_quorate()
        return ClusterSnapshot(
            ts=now_ts(), quorate=quorate, nodes=nodes, vms=vms, storage=storage
        )

    async def _cluster_quorate(self) -> bool:
        try:
            status = await self._call(self._api.cluster.status.get)
            for item in status:
                if item.get("type") == "cluster":
                    return bool(item.get("quorate", 1))
        except Exception:  # pragma: no cover - single-node has no cluster
            return True
        return True

    async def get_node_status(self, node: str) -> NodeMetric:
        st = await self._call(self._api.nodes(node).status.get)
        mem = st.get("memory", {})
        root = st.get("rootfs", {})
        return NodeMetric(
            node=node,
            status=NodeStatus.online,
            cpu_pct=float(st.get("cpu", 0.0)) * 100.0,
            mem_used_mb=float(mem.get("used", 0)) / 1e6,
            mem_total_mb=float(mem.get("total", 0)) / 1e6,
            disk_used_gb=float(root.get("used", 0)) / 1e9,
            disk_total_gb=float(root.get("total", 0)) / 1e9,
            uptime_s=float(st.get("uptime", 0)),
        )

    async def get_vms(self) -> list[VMMetric]:
        return (await self.get_cluster_resources()).vms

    async def get_storage(self) -> list[StorageMetric]:
        return (await self.get_cluster_resources()).storage

    # ---- mutate ----
    async def migrate_vm(self, vmid: int, target: str, online: bool = True) -> None:
        node = await self._node_of(vmid)
        kind = await self._kind_of(vmid)
        endpoint = self._api.nodes(node).qemu if kind == "qemu" else self._api.nodes(node).lxc
        await self._call(endpoint(vmid).migrate.post, target=target, online=1 if online else 0)

    async def set_vm_power(self, vmid: int, state: str) -> None:
        node = await self._node_of(vmid)
        kind = await self._kind_of(vmid)
        endpoint = self._api.nodes(node).qemu if kind == "qemu" else self._api.nodes(node).lxc
        await self._call(getattr(endpoint(vmid).status, state).post)

    async def set_balloon(self, vmid: int, mb: int) -> None:
        node = await self._node_of(vmid)
        await self._call(self._api.nodes(node).qemu(vmid).config.post, balloon=mb)

    async def _node_of(self, vmid: int) -> str:
        for vm in await self.get_vms():
            if vm.vmid == vmid:
                return vm.node
        raise ProxmoxError(f"no such vmid: {vmid}")

    async def _kind_of(self, vmid: int) -> str:
        for vm in await self.get_vms():
            if vm.vmid == vmid:
                return vm.kind.value
        raise ProxmoxError(f"no such vmid: {vmid}")

    async def close(self) -> None:
        return None


def _node_from_row(r: dict) -> NodeMetric:
    maxmem = float(r.get("maxmem", 0)) / 1e6
    maxdisk = float(r.get("maxdisk", 0)) / 1e9
    return NodeMetric(
        node=r.get("node", "?"),
        status=NodeStatus.online if r.get("status") == "online" else NodeStatus.offline,
        cpu_pct=float(r.get("cpu", 0.0)) * 100.0,
        mem_used_mb=float(r.get("mem", 0)) / 1e6,
        mem_total_mb=maxmem,
        disk_used_gb=float(r.get("disk", 0)) / 1e9,
        disk_total_gb=maxdisk,
        uptime_s=float(r.get("uptime", 0)),
    )


def _vm_from_row(r: dict) -> VMMetric:
    maxmem = float(r.get("maxmem", 0)) / 1e6
    return VMMetric(
        vmid=int(r.get("vmid", 0)),
        name=r.get("name", str(r.get("vmid", "?"))),
        node=r.get("node", "?"),
        kind=VMKind.qemu if r.get("type") == "qemu" else VMKind.lxc,
        status=VMStatus.running if r.get("status") == "running" else VMStatus.stopped,
        cpu_pct=float(r.get("cpu", 0.0)) * 100.0,
        mem_used_mb=float(r.get("mem", 0)) / 1e6,
        mem_max_mb=maxmem,
        cores=int(r.get("maxcpu", 1)),
    )


def _storage_from_row(r: dict) -> StorageMetric:
    return StorageMetric(
        storage=r.get("storage", "?"),
        node=r.get("node", "?"),
        used_gb=float(r.get("disk", 0)) / 1e9,
        total_gb=float(r.get("maxdisk", 0)) / 1e9,
        shared=bool(r.get("shared", 0)),
    )
