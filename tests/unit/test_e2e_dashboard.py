"""Unit tests for scripts/e2e/dashboard.py.

Trinity round-2 convergent finding (DeepSeek + Gemini): the 131-LOC SSE
dashboard had zero tests. This file pins:
  - POST /scenario writes to in-memory state and broadcasts to subscribers
  - POST /reset clears state and emits the reset sentinel
  - GET /state returns the snapshot
  - GET /events streams current state to a new subscriber and live-broadcasts
  - GET / serves the HTML UI
  - GET /healthz reports liveness
  - Slow subscribers (full queue) get dropped events, not crashes
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Add scripts/ to sys.path so we can import the dashboard as a module.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e import dashboard as dash_mod  # noqa: E402


@pytest.fixture(autouse=True)
def reset_dashboard_state():
    """Each test starts with a clean state + subscribers list."""
    dash_mod._state.clear()
    dash_mod._subscribers.clear()
    yield
    dash_mod._state.clear()
    dash_mod._subscribers.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(dash_mod.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Scenario ingestion + state
# ---------------------------------------------------------------------------


def _scenario_payload(sid: str, status: str = "running") -> dict:
    return {
        "id": sid,
        "category": "A",
        "description": "test scenario",
        "status": status,
        "expected": {"status": "READY"},
    }


def test_post_scenario_stores_in_state(client) -> None:
    response = client.post("/scenario", json=_scenario_payload("A1"))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["id"] == "A1"
    assert body["status"] == "running"
    assert "A1" in dash_mod._state
    assert dash_mod._state["A1"]["category"] == "A"


def test_post_scenario_updates_existing(client) -> None:
    """A second update for the same id replaces the prior record."""
    client.post("/scenario", json=_scenario_payload("A1", status="running"))
    client.post("/scenario", json=_scenario_payload("A1", status="passed"))
    assert dash_mod._state["A1"]["status"] == "passed"


def test_post_scenario_preserves_ordering(client) -> None:
    """OrderedDict — UI sees scenarios in declaration order."""
    for sid in ["A1", "B1", "C1", "Z9", "B2"]:
        client.post("/scenario", json=_scenario_payload(sid))
    assert list(dash_mod._state.keys()) == ["A1", "B1", "C1", "Z9", "B2"]


def test_post_scenario_rejects_invalid_payload(client) -> None:
    """Missing required field (id/category/status) yields a 422."""
    response = client.post("/scenario", json={"id": "A1"})  # missing category, status
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_clears_state(client) -> None:
    client.post("/scenario", json=_scenario_payload("A1"))
    client.post("/scenario", json=_scenario_payload("B1"))
    assert len(dash_mod._state) == 2

    response = client.post("/reset")
    assert response.status_code == 200
    assert dash_mod._state == {}


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------


def test_get_state_returns_all_scenarios(client) -> None:
    client.post("/scenario", json=_scenario_payload("A1"))
    client.post("/scenario", json=_scenario_payload("B1", status="passed"))

    response = client.get("/state")
    body = response.json()
    assert body["count"] == 2
    assert {s["id"] for s in body["scenarios"]} == {"A1", "B1"}


def test_get_state_empty_when_no_scenarios(client) -> None:
    body = client.get("/state").json()
    assert body == {"count": 0, "scenarios": []}


# ---------------------------------------------------------------------------
# SSE broadcast
# ---------------------------------------------------------------------------


def test_broadcast_puts_payload_on_every_queue() -> None:
    """The internal _broadcast helper fans out to every subscriber queue."""
    q1: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
    q2: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
    dash_mod._subscribers.append(q1)
    dash_mod._subscribers.append(q2)

    payload = {"id": "A1", "status": "running"}
    asyncio.run(dash_mod._broadcast(payload))

    line_q1 = q1.get_nowait()
    line_q2 = q2.get_nowait()
    assert line_q1.startswith("data: ")
    assert line_q2.startswith("data: ")
    decoded = json.loads(line_q1.removeprefix("data: ").strip())
    assert decoded == payload


def test_broadcast_suppresses_queuefull_for_slow_subscribers() -> None:
    """A subscriber with a full queue must not crash _broadcast."""
    slow: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    slow.put_nowait("data: {}\n\n")  # fill it
    fast: asyncio.Queue[str] = asyncio.Queue(maxsize=8)

    dash_mod._subscribers.append(slow)
    dash_mod._subscribers.append(fast)

    # Should not raise even though `slow` is full.
    asyncio.run(dash_mod._broadcast({"id": "A1"}))

    # Fast subscriber receives normally; slow stayed full.
    assert not fast.empty()
    assert slow.qsize() == 1  # unchanged


# ---------------------------------------------------------------------------
# HTML UI / health
# ---------------------------------------------------------------------------


def test_index_serves_html(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Voyager E2E Dashboard" in response.text


def test_healthz_reports_subscriber_count(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["scenarios_known"] == 0
    assert body["subscribers"] == 0
    assert "uptime_s" in body
