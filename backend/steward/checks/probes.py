"""Active probes for non-Proxmox check types.

Unlike ``proxmox_metric`` checks (evaluated against the snapshot by the rule
engine), these perform I/O, so the collector runs them with a timeout off the
critical path. Each returns an :class:`Event` if the check's condition holds,
else ``None``.

``shell_command`` is disabled unless ``STEWARD_ALLOW_SHELL_PROBES=true`` is set
in the environment — running arbitrary shell from a monitoring daemon is a
foot-gun, so it is opt-in and never on by default.
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import Optional

import httpx

from steward.checks.schema import Check, ComparisonOp, ProbeType, parse_target
from steward.models import ClusterSnapshot, Event


async def run_probe(check: Check, snap: Optional[ClusterSnapshot] = None) -> Optional[Event]:
    if not check.enabled:
        return None
    if check.probe_type == ProbeType.http_get:
        return await _http_get(check)
    if check.probe_type == ProbeType.tcp_port:
        return await _tcp_port(check)
    if check.probe_type == ProbeType.shell_command:
        return await _shell_command(check)
    if check.probe_type == ProbeType.process_cpu:
        return _process_cpu(check, snap)
    return None


def _event(check: Check, target: str, value: Optional[float], message: str, ctx: dict) -> Event:
    return Event(
        check_id=check.id, check_name=check.name, severity=check.severity,
        target=target, value=value, message=message, context=ctx,
    )


async def _http_get(check: Check) -> Optional[Event]:
    """target = URL. Fires if status doesn't match or latency exceeds threshold.

    Condition semantics:
      metric == "status_code": numeric compare of HTTP status.
      metric == "latency_ms":  numeric compare of round-trip latency.
      otherwise:               fire on any non-2xx / connection error.
    """
    url = check.target
    cond = check.condition
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
        latency_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:
        return _event(check, url, None, f"HTTP GET {url} failed: {exc}", {"error": str(exc)})

    if cond.metric == "status_code":
        if cond.op.apply(float(resp.status_code), cond.threshold):
            return _event(check, url, float(resp.status_code),
                          f"HTTP {url} status {resp.status_code}", {"status_code": resp.status_code})
        return None
    if cond.metric == "latency_ms":
        if cond.op.apply(latency_ms, cond.threshold):
            return _event(check, url, latency_ms,
                          f"HTTP {url} latency {latency_ms:.0f}ms", {"latency_ms": latency_ms})
        return None
    if resp.status_code >= 400:
        return _event(check, url, float(resp.status_code),
                      f"HTTP {url} returned {resp.status_code}", {"status_code": resp.status_code})
    return None


async def _tcp_port(check: Check) -> Optional[Event]:
    """target = host:port. Fires if the port is NOT reachable."""
    host, _, port_s = check.target.partition(":")
    try:
        port = int(port_s)
    except ValueError:
        return _event(check, check.target, None, f"bad tcp target: {check.target}", {})
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=5.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return None  # reachable -> healthy
    except Exception as exc:
        return _event(check, check.target, None,
                      f"TCP {check.target} unreachable: {exc}", {"error": str(exc)})


async def _shell_command(check: Check) -> Optional[Event]:
    """target = shell command. Fires if exit code matches the condition.

    Opt-in only. The condition compares the process exit code (metric
    'exit_code'); default semantics fire on any non-zero exit.
    """
    if os.environ.get("STEWARD_ALLOW_SHELL_PROBES", "").lower() not in {"1", "true", "yes"}:
        return _event(check, check.target, None,
                      "shell_command probe disabled (set STEWARD_ALLOW_SHELL_PROBES=true)",
                      {"disabled": True})
    proc = await asyncio.create_subprocess_shell(
        check.target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=15.0)
    except asyncio.TimeoutError:
        proc.kill()
        return _event(check, check.target, None, "shell probe timed out", {"timeout": True})
    code = float(proc.returncode or 0)
    cond = check.condition
    if cond.metric == "exit_code":
        fired = cond.op.apply(code, cond.threshold)
    else:
        fired = code != 0
    if fired:
        return _event(check, check.target, code,
                      f"shell `{check.target}` exit {int(code)}", {"exit_code": int(code)})
    return None


def _process_cpu(check: Check, snap: Optional[ClusterSnapshot]) -> Optional[Event]:
    """target = vm:<vmid>. Fires on that guest's CPU vs threshold (from snapshot)."""
    if snap is None:
        return None
    _, selector = parse_target(check.target)
    cond = check.condition
    for vm in snap.vms:
        if selector in (str(vm.vmid), vm.name):
            if cond.op.apply(vm.cpu_pct, cond.threshold):
                return _event(check, str(vm.vmid), vm.cpu_pct,
                              f"VM {vm.name} CPU {vm.cpu_pct:.1f}%", {"cpu_pct": vm.cpu_pct})
            return None
    return None
