"""Unit tests for the e2e debug endpoint (GET /e2e/recent_writebacks).

The endpoint has layered defense-in-depth (trinity round 1 P1):
  1. VOYAGER_E2E_DEBUG=1 gates everything (404 otherwise)
  2. Loopback-only by default (non-loopback → 404, override with
     VOYAGER_E2E_ALLOW_NON_LOOPBACK=1)
  3. Optional VOYAGER_E2E_TOKEN paired with X-Voyager-E2E-Token header
  4. Cache-Control: no-store on the response

These tests pin all four layers + the unchanged "returns deque contents"
behavior.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """Default test client — bypasses loopback check since TestClient's
    request.client.host is `testclient`, not 127.0.0.1. Individual tests
    that exercise the loopback gate override the env explicitly."""
    monkeypatch.setenv("VOYAGER_E2E_ALLOW_NON_LOOPBACK", "1")
    from voyager.server import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Debug-gate layer
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize("truthy", ["true", "TRUE", "Yes", "y", "on"])
def test_endpoint_accepts_various_truthy_forms(monkeypatch, client, truthy) -> None:
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", truthy)
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 200


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


# ---------------------------------------------------------------------------
# Cache-Control header
# ---------------------------------------------------------------------------


def test_endpoint_sets_no_store_cache_header(monkeypatch, client) -> None:
    """Sensitive payload — no caching by intermediaries."""
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 200
    cache = response.headers.get("cache-control", "")
    assert "no-store" in cache, f"expected no-store in Cache-Control, got: {cache!r}"


# ---------------------------------------------------------------------------
# Loopback gate
# ---------------------------------------------------------------------------


def test_endpoint_404_for_non_loopback_when_override_unset(monkeypatch) -> None:
    """Without the override, non-loopback clients (e.g. TestClient with
    `testclient` host) get a 404 — same shape as the debug-gate 404 so
    it doesn't leak the endpoint's existence."""
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    monkeypatch.delenv("VOYAGER_E2E_ALLOW_NON_LOOPBACK", raising=False)
    from voyager.server import app

    raw_client = TestClient(app, raise_server_exceptions=False)
    response = raw_client.get("/e2e/recent_writebacks")
    assert response.status_code == 404


def test_endpoint_200_when_loopback_override_set(monkeypatch) -> None:
    """The escape hatch for operators running on bastions etc."""
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    monkeypatch.setenv("VOYAGER_E2E_ALLOW_NON_LOOPBACK", "1")
    from voyager.server import app

    raw_client = TestClient(app, raise_server_exceptions=False)
    response = raw_client.get("/e2e/recent_writebacks")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Token gate (when VOYAGER_E2E_TOKEN is set)
# ---------------------------------------------------------------------------


def test_endpoint_401_when_token_required_and_header_missing(monkeypatch, client) -> None:
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    monkeypatch.setenv("VOYAGER_E2E_TOKEN", "secret-abc")
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 401


def test_endpoint_401_when_token_required_and_header_wrong(monkeypatch, client) -> None:
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    monkeypatch.setenv("VOYAGER_E2E_TOKEN", "secret-abc")
    response = client.get("/e2e/recent_writebacks", headers={"X-Voyager-E2E-Token": "wrong-token"})
    assert response.status_code == 401


def test_endpoint_200_when_token_required_and_header_matches(monkeypatch, client) -> None:
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    monkeypatch.setenv("VOYAGER_E2E_TOKEN", "secret-abc")
    response = client.get("/e2e/recent_writebacks", headers={"X-Voyager-E2E-Token": "secret-abc"})
    assert response.status_code == 200


def test_endpoint_token_unset_means_no_header_required(monkeypatch, client) -> None:
    """Backward-compat: existing operators who set only VOYAGER_E2E_DEBUG=1
    keep working without configuring a token."""
    monkeypatch.setenv("VOYAGER_E2E_DEBUG", "1")
    monkeypatch.delenv("VOYAGER_E2E_TOKEN", raising=False)
    response = client.get("/e2e/recent_writebacks")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Schema visibility
# ---------------------------------------------------------------------------


def test_endpoint_not_in_openapi_schema(client) -> None:
    """Operator-discoverable surfaces (/docs, /openapi.json) must not list
    this endpoint — GLM r1 P3."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json().get("paths", {})
    assert "/e2e/recent_writebacks" not in paths


# ---------------------------------------------------------------------------
# PR-number extractor from webhook payloads (Codex GH-bot PR #15 P1)
# ---------------------------------------------------------------------------


def test_extract_pr_number_from_pull_request_payload() -> None:
    from voyager.server import _extract_pr_number_from_payload

    payload = {"pull_request": {"number": 42, "head": {"sha": "abc"}}}
    assert _extract_pr_number_from_payload(payload) == 42


def test_extract_pr_number_from_pull_request_review_payload() -> None:
    from voyager.server import _extract_pr_number_from_payload

    payload = {
        "pull_request": {"number": 17},
        "review": {"id": 99, "user": {"login": "chatgpt-codex-connector[bot]"}},
    }
    assert _extract_pr_number_from_payload(payload) == 17


def test_extract_pr_number_from_issue_comment_on_pr() -> None:
    """issue_comment events on PRs put the number under `issue` (PRs are
    issues internally). The `pull_request` field inside issue marks the
    issue as belonging to a PR."""
    from voyager.server import _extract_pr_number_from_payload

    payload = {"issue": {"number": 88, "pull_request": {"url": "..."}}}
    assert _extract_pr_number_from_payload(payload) == 88


def test_extract_pr_number_from_issue_without_pr_marker_returns_none() -> None:
    """Plain issue (not a PR) — should NOT return its number for our purposes."""
    from voyager.server import _extract_pr_number_from_payload

    payload = {"issue": {"number": 5}}  # no `pull_request` key
    assert _extract_pr_number_from_payload(payload) is None


def test_extract_pr_number_from_check_suite() -> None:
    from voyager.server import _extract_pr_number_from_payload

    payload = {"check_suite": {"pull_requests": [{"number": 33}]}}
    assert _extract_pr_number_from_payload(payload) == 33


# ---------------------------------------------------------------------------
# Production repository allow-list gate
# ---------------------------------------------------------------------------


def test_allowed_repositories_env_key_normalizes_agent_slug() -> None:
    from voyager.server import _allowed_repositories_env_key

    assert (
        _allowed_repositories_env_key("iterwheel-clearance")
        == "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE"
    )
    assert (
        _allowed_repositories_env_key("iterwheel clearance/beta")
        == "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE_BETA"
    )


def test_parse_allowed_repositories_ignores_empty_segments() -> None:
    from voyager.server import _parse_allowed_repositories

    assert _parse_allowed_repositories(None) == set()
    assert _parse_allowed_repositories("   ") == set()
    assert _parse_allowed_repositories(
        "iterwheel/voyager-sandbox,  frankyxhl/trinity\nfrankyxhl/babs,,"
    ) == {
        "iterwheel/voyager-sandbox",
        "frankyxhl/trinity",
        "frankyxhl/babs",
    }


def test_repository_allowlist_defaults_allow_in_dry_run(monkeypatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)
    monkeypatch.setenv("DRY_RUN", "true")

    assert _repository_allowed_for_agent("test-org/test-repo", "iterwheel-clearance")


def test_repository_allowlist_defaults_deny_in_production(monkeypatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)
    monkeypatch.setenv("DRY_RUN", "false")

    assert not _repository_allowed_for_agent("test-org/test-repo", "iterwheel-clearance")


def test_repository_allowlist_global_allows_exact_repo(monkeypatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "iterwheel/voyager-sandbox")
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)

    assert _repository_allowed_for_agent("iterwheel/voyager-sandbox", "iterwheel-clearance")
    assert not _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance")


def test_repository_allowlist_agent_specific_overrides_global(monkeypatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "iterwheel/voyager-sandbox")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", "frankyxhl/trinity")

    assert _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance")
    assert not _repository_allowed_for_agent("iterwheel/voyager-sandbox", "iterwheel-clearance")


def test_repository_allowlist_blank_agent_specific_falls_back_to_global(monkeypatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "iterwheel/voyager-sandbox")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", "   ")

    assert _repository_allowed_for_agent("iterwheel/voyager-sandbox", "iterwheel-clearance")
    assert not _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance")


def test_repository_allowlist_supports_owner_wildcard(monkeypatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "frankyxhl/*")
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)

    assert _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance")
    assert not _repository_allowed_for_agent("iterwheel/voyager-sandbox", "iterwheel-clearance")
    assert not _repository_allowed_for_agent("frankyxhl/", "iterwheel-clearance")
    assert not _repository_allowed_for_agent("frankyxhl/trinity/subpath", "iterwheel-clearance")


def test_repository_allowlist_denies_missing_repository_when_allowlist_configured(
    monkeypatch,
) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "*")
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)

    assert not _repository_allowed_for_agent(None, "iterwheel-clearance")


def test_repository_allowlist_supports_global_wildcard(monkeypatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", "*")
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)

    assert _repository_allowed_for_agent("iterwheel/voyager-sandbox", "iterwheel-clearance")
    assert _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance")


def test_filter_routes_by_repository_splits_allowed_and_denied(monkeypatch) -> None:
    from voyager.server import _filter_routes_by_repository

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_BLUEPRINT", "test-org/test-repo")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_STACK", "other-org/other-repo")
    routes = [
        {"agent": "iterwheel-blueprint", "kind": "blueprint_intake"},
        {"agent": "iterwheel-stack", "kind": "stack_classification"},
    ]

    allowed, denied = _filter_routes_by_repository(routes, "test-org/test-repo")

    assert allowed == [routes[0]]
    assert denied == [routes[1]]


def test_webhook_debug_context_for_pull_request_review() -> None:
    from voyager.server import _webhook_debug_context

    payload = {
        "action": "submitted",
        "sender": {"login": "voyager-e2e-bot[bot]"},
        "review": {
            "id": 200,
            "state": "commented",
            "user": {"login": "voyager-e2e-bot[bot]"},
        },
    }

    assert _webhook_debug_context("pull_request_review", payload) == {
        "action": "submitted",
        "sender_login": "voyager-e2e-bot[bot]",
        "review_id": 200,
        "review_state": "commented",
        "review_user_login": "voyager-e2e-bot[bot]",
    }


def test_webhook_debug_context_for_pull_request_review_comment() -> None:
    from voyager.server import _webhook_debug_context

    payload = {
        "action": "created",
        "sender": {"login": "ryosaeba1985"},
        "comment": {
            "id": 901,
            "in_reply_to_id": 900,
            "pull_request_review_id": 300,
            "user": {"login": "ryosaeba1985"},
        },
    }

    assert _webhook_debug_context("pull_request_review_comment", payload) == {
        "action": "created",
        "sender_login": "ryosaeba1985",
        "review_comment_id": 901,
        "review_comment_in_reply_to_id": 900,
        "review_id": 300,
        "review_comment_user_login": "ryosaeba1985",
    }


def test_extract_pr_number_returns_none_for_unrecognized_payload() -> None:
    from voyager.server import _extract_pr_number_from_payload

    assert _extract_pr_number_from_payload({}) is None
    assert _extract_pr_number_from_payload({"action": "foo"}) is None
