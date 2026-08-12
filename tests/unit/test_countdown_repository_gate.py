"""Repository-allowlist gate for the Countdown trigger route (CHG-1841 major finding 2).

route_countdown_trigger performs its side effect (touching the trigger file)
during route collection and always returns [], so it can never be gated by
_filter_routes_by_repository (which only filters route *dicts* after
collection). server.py must therefore check the same
_repository_allowed_for_agent predicate before calling it, so a genuine
Clearance verdict webhook from a non-allowlisted repository cannot wake
Countdown.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import pytest

SECRET = "test-secret-countdown-gate"
RESOLVED_HEADING = "✅ **Clearance: resolved**"


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _payload(repo_full_name: str) -> dict[str, Any]:
    return {
        "action": "created",
        "comment": {
            "body": f"<!-- clearance-close-reason:PRRT_1:abc123456789 -->\n{RESOLVED_HEADING}\n",
            "user": {"login": "iterwheel-clearance[bot]"},
        },
        "pull_request": {"number": 125},
        "repository": {"full_name": repo_full_name},
    }


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from voyager.server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_allowlist_env(monkeypatch):
    """Hermetic against an operator shell that already exports allowlist vars."""
    for key in list(os.environ):
        if key == "BRIDGE_ALLOWED_REPOSITORIES" or key.startswith("BRIDGE_ALLOWED_REPOSITORIES_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("DRY_RUN", "false")


def _post(client, monkeypatch, tmp_path, repo_full_name: str, delivery: str):
    trigger = tmp_path / "trigger"
    monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))
    body = json.dumps(_payload(repo_full_name)).encode("utf-8")
    response = client.post(
        "/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request_review_comment",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    return response, trigger


def test_denied_repository_does_not_touch_trigger(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "some-org/other-repo")

    response, trigger = _post(client, monkeypatch, tmp_path, "iterwheel/voyager", "gate-denied-1")

    assert response.status_code == 200
    assert not trigger.exists()


def test_allowed_repository_touches_trigger(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "iterwheel/voyager")

    response, trigger = _post(client, monkeypatch, tmp_path, "iterwheel/voyager", "gate-allowed-1")

    assert response.status_code == 200
    assert trigger.exists()
