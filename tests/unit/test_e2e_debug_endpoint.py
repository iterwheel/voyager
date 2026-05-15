"""Unit tests for the e2e debug endpoint (GET /e2e/recent_writebacks).

Endpoint is gated behind ``VOYAGER_E2E_DEBUG=1`` so the production deployment
never accidentally exposes the writeback deque. These tests pin both the
gate-off-by-default behavior and the gate-on response shape.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from voyager.server import app

    return TestClient(app, raise_server_exceptions=False)


def test_endpoint_404_when_env_unset(monkeypatch, client) -> None:
    monkeypatch.delenv("VOYAGER_E2E_DEBUG", raising=False)
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 404


def test_endpoint_404_when_env_explicitly_false(monkeypatch, client) -> None:
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "0")
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 404


def test_endpoint_200_when_env_truthy(monkeypatch, client) -> None:
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 200
    body = response.json()
    assert "count" in body
    assert "writebacks" in body
    assert isinstance(body["writebacks"], list)


def test_endpoint_returns_deque_contents_when_enabled(monkeypatch, client) -> None:
    """Insert into the deque and confirm the endpoint returns it."""
    from voyager import server

    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    server._recent_writebacks.clear()
    server._recent_writebacks.append({"delivery_id": "abc", "event": "pr_review", "status": "OK"})

    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["writebacks"][0]["delivery_id"] == "abc"

    server._recent_writebacks.clear()


@pytest.mark.parametrize("truthy", ["true", "TRUE", "Yes", "y", "on"])
def test_endpoint_accepts_various_truthy_forms(monkeypatch, client, truthy) -> None:
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", truthy)
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 200
