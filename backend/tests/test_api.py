from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from steward.api.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_state_and_metrics(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert "flags" in body
    # collector ran at least once during startup lifespan
    assert body["snapshot"] is not None


def test_checks_crud_via_api(client):
    # builtins present
    r = client.get("/api/checks")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert "builtin.node_cpu_pressure" in ids

    new = {
        "id": "api.test",
        "name": "api test",
        "probe_type": "proxmox_metric",
        "target": "node:*",
        "condition": {"metric": "mem_pct", "op": "gt", "threshold": 75},
        "severity": "warning",
    }
    assert client.post("/api/checks", json=new).status_code == 200
    assert client.get("/api/checks/api.test").json()["name"] == "api test"

    # toggle
    toggled = client.post("/api/checks/api.test/toggle").json()
    assert toggled["enabled"] is False

    assert client.delete("/api/checks/api.test").status_code == 200
    assert client.get("/api/checks/api.test").status_code == 404


def test_flags_endpoint(client):
    r = client.post("/api/flags", json={"paused": True})
    assert r.json()["paused"] is True
    client.post("/api/flags", json={"paused": False})


def test_action_propose_and_approve(client):
    body = {"type": "migrate", "params": {"vmid": 101, "target": "pve-2"}, "mode": "propose"}
    r = client.post("/api/actions", json=body)
    assert r.status_code == 200
    action_id = r.json()["id"]
    assert r.json()["status"] == "proposed"

    approve = client.post(f"/api/actions/{action_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "executed"
    assert approve.json()["dry_run"] is True


def test_action_run_immediate(client):
    body = {"type": "notify", "params": {"message": "hello"}, "mode": "run"}
    r = client.post("/api/actions", json=body)
    assert r.json()["status"] == "executed"


def test_diff_endpoint(client):
    r = client.get("/api/diff?since_s=300")
    assert r.status_code == 200
    body = r.json()
    for key in ("from_ts", "to_ts", "span_s", "nodes", "vms"):
        assert key in body
    assert isinstance(body["nodes"], list) and isinstance(body["vms"], list)


def test_balancer_simulate_endpoint(client):
    r = client.get("/api/balancer/simulate")
    assert r.status_code == 200
    body = r.json()
    # contract the UI relies on
    for key in ("enabled", "blended_imbalance", "threshold", "imbalance_cpu",
                "imbalance_mem", "weights", "would_act", "moves"):
        assert key in body
    assert body["enabled"] is False        # balancer ships disabled
    assert body["would_act"] is False      # never acts while disabled
    assert isinstance(body["moves"], list)


def test_llm_disabled_returns_503(client):
    r = client.post("/api/llm/ask", json={"question": "how are things?"})
    assert r.status_code == 503


def test_demo_injection(client):
    r = client.post("/api/demo/inject", params={"vmid": 101, "cpu_pct": 99})
    assert r.status_code == 200


def test_auth_enforced_when_token_set(settings):
    settings.auth_token = "s3cret"
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/state", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/health").status_code == 200  # health is open
