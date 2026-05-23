"""Unit tests for ``/healthz`` metadata extension (CHG-1820 Surface 12).

Asserts the ``/healthz`` JSON response gained ``version`` and ``build_commit``
fields without losing any existing keys — an additive regression check.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from voyager.server import app


def test_healthz_returns_version_and_build_commit() -> None:
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert isinstance(body["version"], str)
    assert "build_commit" in body
    assert isinstance(body["build_commit"], str)


def test_healthz_preserves_existing_keys() -> None:
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "service" in body
    assert "time" in body
    assert "dry_run" in body
