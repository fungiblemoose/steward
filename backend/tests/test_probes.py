from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from steward.checks.probes import run_probe
from steward.checks.schema import Check, ComparisonOp, Condition, ProbeType
from steward.models import ClusterSnapshot, VMMetric, VMStatus


def _check(probe_type, target, *, metric="", op=ComparisonOp.gt, threshold=0.0, enabled=True):
    return Check(
        id="t", name="t", probe_type=probe_type, target=target,
        condition=Condition(metric=metric, op=op, threshold=threshold), enabled=enabled,
    )


# --------------------------------------------------------------------------- #
# dispatch / disabled
# --------------------------------------------------------------------------- #
async def test_disabled_check_never_probes():
    chk = _check(ProbeType.tcp_port, "127.0.0.1:1", enabled=False)
    assert await run_probe(chk) is None


# --------------------------------------------------------------------------- #
# process_cpu (snapshot-based, deterministic)
# --------------------------------------------------------------------------- #
def _snap_with_vm(cpu: float) -> ClusterSnapshot:
    return ClusterSnapshot(vms=[VMMetric(vmid=101, name="web", node="pve-1",
                                         status=VMStatus.running, cpu_pct=cpu, cores=2)])


async def test_process_cpu_fires_over_threshold():
    chk = _check(ProbeType.process_cpu, "vm:101", op=ComparisonOp.gt, threshold=80.0)
    ev = await run_probe(chk, _snap_with_vm(95.0))
    assert ev is not None and ev.value == 95.0


async def test_process_cpu_quiet_under_threshold():
    chk = _check(ProbeType.process_cpu, "vm:101", op=ComparisonOp.gt, threshold=80.0)
    assert await run_probe(chk, _snap_with_vm(20.0)) is None


# --------------------------------------------------------------------------- #
# tcp_port
# --------------------------------------------------------------------------- #
async def test_tcp_port_reachable_is_quiet():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        chk = _check(ProbeType.tcp_port, f"127.0.0.1:{port}")
        assert await run_probe(chk) is None  # reachable -> healthy
    finally:
        srv.close()


async def test_tcp_port_unreachable_fires():
    # grab a port then free it so nothing is listening there
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    chk = _check(ProbeType.tcp_port, f"127.0.0.1:{port}")
    ev = await run_probe(chk)
    assert ev is not None and "unreachable" in ev.message


async def test_tcp_port_bad_target():
    ev = await run_probe(_check(ProbeType.tcp_port, "no-colon-here"))
    assert ev is not None and "bad tcp target" in ev.message


# --------------------------------------------------------------------------- #
# shell_command (opt-in)
# --------------------------------------------------------------------------- #
async def test_shell_probe_disabled_by_default(monkeypatch):
    monkeypatch.delenv("STEWARD_ALLOW_SHELL_PROBES", raising=False)
    ev = await run_probe(_check(ProbeType.shell_command, "true"))
    assert ev is not None and ev.context.get("disabled") is True


async def test_shell_probe_exit_zero_is_quiet(monkeypatch):
    monkeypatch.setenv("STEWARD_ALLOW_SHELL_PROBES", "true")
    assert await run_probe(_check(ProbeType.shell_command, "exit 0")) is None


async def test_shell_probe_nonzero_fires(monkeypatch):
    monkeypatch.setenv("STEWARD_ALLOW_SHELL_PROBES", "true")
    ev = await run_probe(_check(ProbeType.shell_command, "exit 1"))
    assert ev is not None and ev.value == 1.0


async def test_shell_probe_exit_code_condition(monkeypatch):
    monkeypatch.setenv("STEWARD_ALLOW_SHELL_PROBES", "1")
    chk = _check(ProbeType.shell_command, "exit 3", metric="exit_code",
                 op=ComparisonOp.eq, threshold=3)
    ev = await run_probe(chk)
    assert ev is not None and ev.value == 3.0


# --------------------------------------------------------------------------- #
# http_get
# --------------------------------------------------------------------------- #
class _Status(BaseHTTPRequestHandler):
    code = 200

    def do_GET(self):  # noqa: N802
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def http_server():
    srv = HTTPServer(("127.0.0.1", 0), _Status)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


async def test_http_get_status_code_condition_fires(http_server):
    _Status.code = 503
    port = http_server.server_address[1]
    chk = _check(ProbeType.http_get, f"http://127.0.0.1:{port}/",
                 metric="status_code", op=ComparisonOp.gte, threshold=500)
    ev = await run_probe(chk)
    assert ev is not None and ev.value == 503.0


async def test_http_get_healthy_is_quiet(http_server):
    _Status.code = 200
    port = http_server.server_address[1]
    chk = _check(ProbeType.http_get, f"http://127.0.0.1:{port}/")
    assert await run_probe(chk) is None  # default: fire only on >=400


async def test_http_get_connection_error_fires():
    # nothing listening here -> connection refused -> fires
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    chk = _check(ProbeType.http_get, f"http://127.0.0.1:{port}/")
    ev = await run_probe(chk)
    assert ev is not None and "failed" in ev.message
