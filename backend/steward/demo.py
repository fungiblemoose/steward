"""Scripted demo incidents for screenshots/gifs (mock client only).

Cycles through a small storyline so a fresh `--demo` boot shows interesting
state: a CPU spike, recovery, a memory spike, a stopped VM, recovery. Purely
cosmetic and only ever touches the in-memory simulator.
"""
from __future__ import annotations

import asyncio
import logging

from steward.proxmox.mock import MockProxmoxClient

log = logging.getLogger("steward.demo")

# (delay_seconds_before_step, description, callable(client))
def _script():
    return [
        (15, "CPU spike on pve-1", lambda c: (
            c.inject_load(vmid=101, cpu_pct=100),
            c.inject_load(vmid=102, cpu_pct=100),
            c.inject_load(vmid=103, cpu_pct=100),
        )),
        (30, "pve-1 recovers", lambda c: c.clear_load()),
        (20, "Memory spike on pve-2", lambda c: (
            c.inject_load(vmid=201, mem_pct=99),
            c.inject_load(vmid=202, mem_pct=99),
        )),
        (30, "pve-2 recovers", lambda c: c.clear_load()),
        (20, "worker-b unexpectedly stops", lambda c: asyncio.create_task(
            c.set_vm_power(202, "stop"))),
        (25, "worker-b restarted", lambda c: asyncio.create_task(
            c.set_vm_power(202, "start"))),
    ]


async def run_demo(client: MockProxmoxClient, stop: asyncio.Event) -> None:
    if not isinstance(client, MockProxmoxClient):
        log.warning("demo mode requested but client is not the mock; skipping")
        return
    log.info("demo mode active — scripting incidents")
    while not stop.is_set():
        for delay, desc, fn in _script():
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return  # stop fired
            except asyncio.TimeoutError:
                pass
            log.info("demo: %s", desc)
            try:
                fn(client)
            except Exception as exc:  # noqa: BLE001
                log.warning("demo step failed: %s", exc)
