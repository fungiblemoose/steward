"""Build the configured Proxmox client. Defaults to the safe mock."""
from __future__ import annotations

from steward.config import Settings
from steward.proxmox.base import ProxmoxClient
from steward.proxmox.mock import MockProxmoxClient


def build_client(settings: Settings) -> ProxmoxClient:
    if settings.proxmox_mode == "real":
        # Imported lazily so the optional proxmoxer dep isn't required for mock.
        from steward.proxmox.real import RealProxmoxClient

        return RealProxmoxClient(settings)
    return MockProxmoxClient()
