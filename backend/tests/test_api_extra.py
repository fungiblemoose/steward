from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from steward.api.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_logs_endpoint(client):
    r = client.get("/api/logs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    # startup logs at least one INFO line
    assert any(rec["level"] == "INFO" for rec in r.json())


def test_logs_level_filter(client):
    r = client.get("/api/logs", params={"level": "WARNING"})
    assert r.status_code == 200
    assert all(rec["level"] == "WARNING" for rec in r.json())


def test_checkset_export_import_roundtrip(client):
    exported = client.get("/api/checksets/export").json()
    assert exported["version"] == 1
    n = len(exported["checks"])
    assert n >= 5  # builtins

    # delete one, then re-import to restore it
    client.delete("/api/checks/builtin.storage_near_full")
    assert client.get("/api/checks/builtin.storage_near_full").status_code == 404

    r = client.post("/api/checksets/import", json=exported)
    assert r.json()["imported"] == n
    assert client.get("/api/checks/builtin.storage_near_full").status_code == 200


def test_import_reports_errors(client):
    r = client.post("/api/checksets/import", json={"checks": [{"bogus": "data"}]})
    body = r.json()
    assert body["imported"] == 0 and len(body["errors"]) == 1
