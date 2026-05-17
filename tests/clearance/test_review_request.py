"""Tests for review-request dispatch in enrich_clearance_route (issue #25).

Tests async enrich_clearance_route's reviewer-request behavior:
- PR author in configured list → skipped (skipped_author), no API call
- User already in requested_reviewers → already_requested, no API call
- Brand-new user → requested, API call made
- DRY_RUN env → applied=False, planned=[...], no API call
- Non-ready_for_approval state → no dispatch
- API failure (httpx.HTTPError) → applied=False with exception reason
- Empty env → enabled=False
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# Minimal stub GitHubAppClient
# ---------------------------------------------------------------------------


class _StubClient:
    def __init__(
        self,
        *,
        pull_request_data: dict | None = None,
        reviews: list | None = None,
        review_threads: list | None = None,
        request_reviewers_side_effect: Any = None,
    ) -> None:
        self._pr = pull_request_data or {
            "number": 77,
            "state": "open",
            "draft": False,
            "html_url": "https://github.test/pull/77",
            "head": {"sha": "sha-abc"},
            "user": {"login": "pr-author"},
            "requested_reviewers": [],
        }
        self._reviews = reviews or []
        self._review_threads = review_threads or []
        self._request_reviewers_calls: list[dict] = []
        self._request_reviewers_side_effect = request_reviewers_side_effect

    async def pull_request(self, app_slug: str, repo: str, pr_number: int) -> dict:
        return self._pr

    async def pull_request_reviews(self, app_slug: str, repo: str, pr_number: int) -> list:
        return self._reviews

    async def pull_request_review_threads(self, app_slug: str, repo: str, pr_number: int) -> list:
        return self._review_threads

    async def request_pull_request_reviewers(
        self, app_slug: str, repo: str, pull_number: int, reviewers: list[str]
    ) -> Any:
        self._request_reviewers_calls.append(
            {"repo": repo, "pull_number": pull_number, "reviewers": reviewers}
        )
        if self._request_reviewers_side_effect is not None:
            raise self._request_reviewers_side_effect
        return {"reviewers": reviewers}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _approval(*, login: str = "approver", commit_id: str = "sha-abc") -> dict:
    return {
        "state": "APPROVED",
        "commit_id": commit_id,
        "submitted_at": "2026-05-01T10:00:00Z",
        "user": {"login": login},
    }


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.delenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    reset_review_request_users_cache()
    yield
    reset_review_request_users_cache()


def _base_route() -> dict:
    return {
        "agent": "iterwheel-clearance",
        "kind": "clearance_readiness",
        "validation": {
            "pr_number": 77,
            "issue_number": 77,
            "status": "clearance_pending",
            "conclusion": "neutral",
            "base_ref": "main",
        },
        "writeback": {"dynamic": "clearance_readiness"},
    }


async def _run_enrich(client: _StubClient, route: dict, automation: dict | None = None) -> dict:
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    return await enrich_clearance_route(
        client, route, repository="iterwheel/voyager", automation=automation
    )


# ---------------------------------------------------------------------------
# Empty env → review_request.enabled=False
# ---------------------------------------------------------------------------


async def test_empty_env_review_request_disabled() -> None:
    client = _StubClient(
        reviews=[_approval(login="some-approver")],
    )
    result = await _run_enrich(client, _base_route())
    comment_body = result["writeback"]["comment_body"]
    assert "Review request:" not in comment_body


async def test_clearance_readiness_comment_uses_upsert_mode() -> None:
    client = _StubClient(
        reviews=[_approval(login="some-approver")],
    )
    result = await _run_enrich(client, _base_route())
    assert result["writeback"]["comment_marker"] == "<!-- iterwheel:clearance-readiness -->"
    assert result["writeback"]["comment_mode"] == "upsert"


# ---------------------------------------------------------------------------
# Non-ready_for_approval state → no dispatch
# ---------------------------------------------------------------------------


async def test_no_dispatch_when_status_is_clearance_ready(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    # Configured approver has already approved → clearance_ready, no dispatch needed
    client = _StubClient(reviews=[_approval(login="required-approver")])
    result = await _run_enrich(client, _base_route())
    # API should NOT have been called
    assert client._request_reviewers_calls == []
    assert result["validation"]["status"] == "clearance_ready"


async def test_no_dispatch_when_status_is_clearance_pending(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    # Draft PR + env set + no approvals → clearance_pending → no review request dispatch
    pr_data = {
        "number": 77,
        "state": "open",
        "draft": True,
        "html_url": "https://github.test/pull/77",
        "head": {"sha": "sha-abc"},
        "user": {"login": "pr-author"},
        "requested_reviewers": [],
    }
    client = _StubClient(pull_request_data=pr_data, reviews=[])
    result = await _run_enrich(client, _base_route())
    assert client._request_reviewers_calls == []
    assert result["validation"]["status"] == "clearance_pending"


# ---------------------------------------------------------------------------
# ready_for_approval state → dispatch fires
# ---------------------------------------------------------------------------


async def test_dispatch_fires_for_ready_for_approval(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()

    # Someone else approved → ready_for_approval → dispatch should fire
    client = _StubClient(reviews=[_approval(login="someone-else")])
    result = await _run_enrich(client, _base_route())
    assert result["validation"]["status"] == "clearance_ready_for_approval"
    # API was called with required-approver
    assert len(client._request_reviewers_calls) >= 1
    requested_users = [u for call in client._request_reviewers_calls for u in call["reviewers"]]
    assert "required-approver" in requested_users


# ---------------------------------------------------------------------------
# PR author in configured list → skipped_author
# ---------------------------------------------------------------------------


async def test_pr_author_in_configured_list_is_skipped(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "pr-author,other-reviewer")
    reset_review_request_users_cache()

    client = _StubClient(reviews=[_approval(login="someone-else")])
    result = await _run_enrich(client, _base_route())
    assert result["validation"]["status"] == "clearance_ready_for_approval"
    # pr-author should NOT be in any API call
    requested = [u for call in client._request_reviewers_calls for u in call["reviewers"]]
    assert "pr-author" not in requested
    # Comment should mention skipped author
    comment_body = result["writeback"]["comment_body"]
    assert "pr-author" in comment_body


async def test_author_only_configured_reviewer_warns_and_does_not_request(
    monkeypatch, caplog
) -> None:
    import logging

    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "pr-author")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()
    caplog.set_level(logging.WARNING, logger="voyager.bots.clearance.enrichment")

    client = _StubClient(reviews=[_approval(login="someone-else")])
    result = await _run_enrich(client, _base_route())

    assert result["validation"]["status"] == "clearance_ready_for_approval"
    assert client._request_reviewers_calls == []

    comment_body = result["writeback"]["comment_body"]
    assert "⚠️ Warning: @pr-author is the only configured reviewer" in comment_body
    assert "`VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS`" in comment_body
    assert "Next: add a non-author configured reviewer" in comment_body
    assert "clearance-3-ready-for-approval" in comment_body
    assert "clearance-4-ready-for-merge" not in comment_body

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "author-only reviewer deadlock" in log_text
    assert "repository=iterwheel/voyager" in log_text
    assert "pr_number=77" in log_text
    assert "configured_users=['pr-author']" in log_text
    assert "pr_author=pr-author" in log_text


async def test_multi_reviewer_author_skip_requests_eligible_without_deadlock(
    monkeypatch, caplog
) -> None:
    import logging

    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "pr-author,eligible-reviewer")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()
    caplog.set_level(logging.WARNING, logger="voyager.bots.clearance.enrichment")

    client = _StubClient(reviews=[_approval(login="someone-else")])
    result = await _run_enrich(client, _base_route())

    requested = [u for call in client._request_reviewers_calls for u in call["reviewers"]]
    assert requested == ["eligible-reviewer"]

    comment_body = result["writeback"]["comment_body"]
    assert "requested @eligible-reviewer" in comment_body
    assert "skipped PR author @pr-author" in comment_body
    assert "only configured reviewer" not in comment_body

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "author-only reviewer deadlock" not in log_text


# ---------------------------------------------------------------------------
# User already in requested_reviewers → already_requested
# ---------------------------------------------------------------------------


async def test_already_requested_user_not_re_requested(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "already-there")
    reset_review_request_users_cache()

    pr_data = {
        "number": 77,
        "state": "open",
        "draft": False,
        "html_url": "https://github.test/pull/77",
        "head": {"sha": "sha-abc"},
        "user": {"login": "pr-author"},
        "requested_reviewers": [{"login": "already-there"}],
    }
    client = _StubClient(
        pull_request_data=pr_data,
        reviews=[_approval(login="someone-else")],
    )
    result = await _run_enrich(client, _base_route())
    # API should NOT have been called (user already requested)
    requested = [u for call in client._request_reviewers_calls for u in call["reviewers"]]
    assert "already-there" not in requested
    # Comment should mention already requested
    comment_body = result["writeback"]["comment_body"]
    assert "already" in comment_body.lower() or "already-there" in comment_body


# ---------------------------------------------------------------------------
# DRY_RUN → applied=False, planned=[...], no API call
# ---------------------------------------------------------------------------


async def test_dry_run_no_api_call(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    monkeypatch.setenv("DRY_RUN", "true")
    reset_review_request_users_cache()

    client = _StubClient(reviews=[_approval(login="someone-else")])
    result = await _run_enrich(client, _base_route())
    assert result["validation"]["status"] == "clearance_ready_for_approval"
    # No API call in dry-run
    assert client._request_reviewers_calls == []
    # Comment should mention dry-run
    comment_body = result["writeback"]["comment_body"]
    assert "dry-run" in comment_body.lower() or "planned" in comment_body.lower()


# ---------------------------------------------------------------------------
# API failure → applied=False with exception reason in comment
# ---------------------------------------------------------------------------


async def test_api_failure_gives_applied_false(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()

    client = _StubClient(
        reviews=[_approval(login="someone-else")],
        request_reviewers_side_effect=httpx.HTTPError("connection reset"),
    )
    result = await _run_enrich(client, _base_route())
    # Should not raise; should surface failure in comment or review_request
    comment_body = result["writeback"]["comment_body"]
    # Comment should contain error info or the exception class name or planned (failure path)
    assert (
        "HTTPError" in comment_body
        or "error" in comment_body.lower()
        or "failed" in comment_body.lower()
        or "planned" in comment_body.lower()
    )


# ---------------------------------------------------------------------------
# Comment body includes Review request line when status is ready_for_approval
# ---------------------------------------------------------------------------


async def test_comment_includes_review_request_line(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    client = _StubClient(reviews=[_approval(login="someone-else")])
    result = await _run_enrich(client, _base_route())
    comment_body = result["writeback"]["comment_body"]
    assert "Review request:" in comment_body


# ---------------------------------------------------------------------------
# Case-insensitive already_requested dedup (Trinity round-2 finding #3)
# ---------------------------------------------------------------------------


async def test_already_requested_match_is_case_insensitive(monkeypatch) -> None:
    """Configured 'frankyxhl' + requested_reviewers login 'Frankyxhl' → already_requested, no API call."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    pr_data = {
        "number": 77,
        "state": "open",
        "draft": False,
        "html_url": "https://github.test/pull/77",
        "head": {"sha": "sha-abc"},
        "user": {"login": "pr-author"},
        "requested_reviewers": [{"login": "Frankyxhl"}],
    }
    client = _StubClient(
        pull_request_data=pr_data,
        reviews=[_approval(login="someone-else")],
    )
    await _run_enrich(client, _base_route())
    # No API call should be made — user is already requested (case-insensitive match)
    requested = [u for call in client._request_reviewers_calls for u in call["reviewers"]]
    assert "frankyxhl" not in requested, (
        "frankyxhl should be in already_requested, not re-requested"
    )
    assert client._request_reviewers_calls == [], (
        "No API call should be made when user is already in requested_reviewers (case-insensitive)"
    )


# ---------------------------------------------------------------------------
# Shared dry_run_enabled() default — unset DRY_RUN → dry (finding #4)
# ---------------------------------------------------------------------------


async def test_dispatch_honors_shared_dry_run_default(monkeypatch) -> None:
    """Unset DRY_RUN → shared default is 'true' (dry) → applied=False, planned=[...], no API call."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    monkeypatch.delenv("DRY_RUN", raising=False)
    reset_review_request_users_cache()

    client = _StubClient(reviews=[_approval(login="someone-else")])
    # Call _dispatch_review_request directly so we can inspect its return value precisely
    from voyager.bots.clearance.enrichment import _dispatch_review_request

    pull_request = {
        "number": 77,
        "state": "open",
        "draft": False,
        "html_url": "https://github.test/pull/77",
        "head": {"sha": "sha-abc"},
        "user": {"login": "pr-author"},
        "requested_reviewers": [],
    }
    result = await _dispatch_review_request(
        client,
        repository="iterwheel/voyager",
        pull_request=pull_request,
        configured_users=("required-approver",),
    )
    assert result["applied"] is False, (
        f"Expected applied=False (dry default) but got applied={result.get('applied')!r}"
    )
    assert result.get("planned") == ["required-approver"], (
        f"Expected planned=['required-approver'] but got {result.get('planned')!r}"
    )
    assert result.get("reason") == "dry-run"
    assert client._request_reviewers_calls == [], "No API call in dry-run"


# ---------------------------------------------------------------------------
# 422 race tolerance (Trinity finding #6)
# ---------------------------------------------------------------------------


async def test_dispatch_translates_422_to_already_requested(monkeypatch) -> None:
    """Client raises HTTPStatusError 422 with 'already requested' body → result has applied=False
    and 'already requested'/'422' in reason."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()

    request_obj = httpx.Request(
        "POST", "https://api.github.com/repos/iterwheel/voyager/pulls/77/requested_reviewers"
    )
    response = httpx.Response(
        status_code=422,
        request=request_obj,
        json={
            "message": "Validation Failed",
            "errors": [
                {
                    "resource": "PullRequest",
                    "code": "custom",
                    "field": "reviewers",
                    "message": "Reviews may only be requested from collaborators who have not already been requested.",
                }
            ],
        },
    )
    exc_422 = httpx.HTTPStatusError("422", request=request_obj, response=response)
    client = _StubClient(
        reviews=[_approval(login="someone-else")],
        request_reviewers_side_effect=exc_422,
    )
    from voyager.bots.clearance.enrichment import _dispatch_review_request

    pull_request = {
        "number": 77,
        "state": "open",
        "draft": False,
        "html_url": "https://github.test/pull/77",
        "head": {"sha": "sha-abc"},
        "user": {"login": "pr-author"},
        "requested_reviewers": [],
    }
    result = await _dispatch_review_request(
        client,
        repository="iterwheel/voyager",
        pull_request=pull_request,
        configured_users=("required-approver",),
    )
    assert result["applied"] is False
    reason = result.get("reason", "")
    assert "already requested" in reason.lower() or "422" in reason, (
        f"Expected '422' or 'already requested' in reason, got: {reason!r}"
    )


async def test_dispatch_422_with_unrelated_error_is_not_already_requested(monkeypatch) -> None:
    """Client raises HTTPStatusError 422 with an unrelated error body (not 'already requested') →
    result has applied=False and reason does NOT mention 'already requested'. Codex delta P1."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()

    request_obj = httpx.Request(
        "POST", "https://api.github.com/repos/iterwheel/voyager/pulls/77/requested_reviewers"
    )
    response = httpx.Response(
        status_code=422,
        request=request_obj,
        json={
            "message": "Validation Failed",
            "errors": [
                {
                    "resource": "PullRequest",
                    "code": "invalid",
                    "field": "reviewers",
                    "message": "Could not resolve to a User with the login of 'nonexistent-user'",
                }
            ],
        },
    )
    exc_422 = httpx.HTTPStatusError("422", request=request_obj, response=response)
    client = _StubClient(
        reviews=[_approval(login="someone-else")],
        request_reviewers_side_effect=exc_422,
    )
    from voyager.bots.clearance.enrichment import _dispatch_review_request

    pull_request = {
        "number": 77,
        "state": "open",
        "draft": False,
        "html_url": "https://github.test/pull/77",
        "head": {"sha": "sha-abc"},
        "user": {"login": "pr-author"},
        "requested_reviewers": [],
    }
    result = await _dispatch_review_request(
        client,
        repository="iterwheel/voyager",
        pull_request=pull_request,
        configured_users=("required-approver",),
    )
    assert result["applied"] is False
    reason = result.get("reason", "")
    assert "already requested" not in reason.lower(), (
        f"Unrelated 422 should NOT be classified as 'already requested'; got reason: {reason!r}"
    )


# ---------------------------------------------------------------------------
# Sanitized exception message — no credential leak (Trinity finding #7)
# ---------------------------------------------------------------------------


async def test_dispatch_does_not_leak_exception_url_in_comment(monkeypatch) -> None:
    """HTTPError with sensitive URL in message → reason must NOT contain 'token=', 'ghp_', or 'https://'."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()

    sensitive_msg = "https://api.github.com/repos/iterwheel/voyager/pulls/42/requested_reviewers token=ghp_abc123"
    client = _StubClient(
        reviews=[_approval(login="someone-else")],
        request_reviewers_side_effect=httpx.HTTPError(sensitive_msg),
    )
    from voyager.bots.clearance.enrichment import _dispatch_review_request

    pull_request = {
        "number": 77,
        "state": "open",
        "draft": False,
        "html_url": "https://github.test/pull/77",
        "head": {"sha": "sha-abc"},
        "user": {"login": "pr-author"},
        "requested_reviewers": [],
    }
    result = await _dispatch_review_request(
        client,
        repository="iterwheel/voyager",
        pull_request=pull_request,
        configured_users=("required-approver",),
    )
    reason = result.get("reason", "")
    assert "token=" not in reason, f"Sensitive 'token=' leaked into reason: {reason!r}"
    assert "ghp_" not in reason, f"Sensitive 'ghp_' leaked into reason: {reason!r}"
    assert "https://" not in reason, f"Sensitive URL leaked into reason: {reason!r}"


# ---------------------------------------------------------------------------
# Failure reason visible in comment (Codex delta P1)
# ---------------------------------------------------------------------------


async def test_dispatch_failure_reason_appears_in_comment_body(monkeypatch) -> None:
    """When _dispatch_review_request returns applied=False + planned=[...] + reason='API request failed (HTTPError)',
    the build_clearance_comment output MUST surface the failure reason — not show only
    'planned @user' which makes the failure invisible to the operator. Codex delta P1."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()

    review_request = {
        "enabled": True,
        "applied": False,
        "planned": ["frankyxhl"],
        "already_requested": [],
        "skipped_author": [],
        "reason": "API request failed (HTTPError)",
    }

    from voyager.bots.clearance.enrichment import build_clearance_comment

    evaluation = {
        "status": "clearance_ready_for_approval",
        "conclusion": "neutral",
        "issue_number": 77,
        "pr_number": 77,
        "classifier": "clearance-v1",
        "summary": "Ready for approval.",
        "review_state": {
            "current_approvals": ["someone-else"],
            "stale_approvals": [],
            "blocking_reviewers": [],
            "unresolved_thread_count": 0,
        },
        "confidence": {
            "reasons": [],
            "semantic_fix_verified": False,
            "semantic_fix_note": "",
        },
        "labels": {"add": [], "remove": []},
        "reactions": {"add": [], "remove": []},
        "pr_url": "https://github.test/pull/77",
        "head_sha": "sha-abc",
        "target_kind": "pull_request",
    }
    comment = build_clearance_comment(evaluation, automation=None, review_request=review_request)
    assert "API request failed" in comment, (
        f"Failure reason should appear in operator-facing comment; got: {comment!r}"
    )


# ---------------------------------------------------------------------------
# Log warning does not leak URL/token via exc_info (Codex delta P1)
# ---------------------------------------------------------------------------


async def test_dispatch_failure_log_does_not_leak_secrets(monkeypatch, caplog) -> None:
    """Codex delta P1: even though `reason` field is sanitized, _log.warning(exc, exc_info=True)
    can include the full exception message (with URLs / token fragments) in log records via
    the formatted traceback. Log records must NOT include obvious secrets."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    monkeypatch.setenv("DRY_RUN", "false")
    reset_review_request_users_cache()
    import logging

    caplog.set_level(logging.WARNING, logger="voyager.bots.clearance.enrichment")

    secret_url = "https://api.github.com/repos/o/r/pulls/42/requested_reviewers token=ghp_SECRET123"

    class _LeakyClient:
        async def request_pull_request_reviewers(self, *args, **kwargs):
            raise httpx.HTTPError(secret_url)

    from voyager.bots.clearance.enrichment import _dispatch_review_request

    pull_request = {"number": 42, "user": {"login": "someone-else"}, "requested_reviewers": []}
    result = await _dispatch_review_request(
        _LeakyClient(),
        repository="o/r",
        pull_request=pull_request,
        configured_users=("frankyxhl",),
    )
    assert result["applied"] is False
    assert "ghp_" not in result["reason"], "reason leaked token"
    assert "token=" not in result["reason"], "reason leaked token"

    log_text = "\n".join(
        record.getMessage() + (str(record.exc_info) if record.exc_info else "")
        for record in caplog.records
    )
    assert "ghp_" not in log_text, f"log records leaked token: {log_text!r}"
    assert "token=" not in log_text, f"log records leaked token: {log_text!r}"
    assert secret_url not in log_text, f"log records leaked full URL: {log_text!r}"


# ---------------------------------------------------------------------------
# CHG-1813: writeback failure warning in enrichment panel
# ---------------------------------------------------------------------------


async def test_writeback_failure_warning_in_enrichment_panel() -> None:
    """When automation has writeback_failures, the enrichment comment must include the warning."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache
    from voyager.bots.clearance.enrichment import build_clearance_comment

    reset_review_request_users_cache()

    evaluation = {
        "status": "clearance_blocked",
        "conclusion": "failure",
        "issue_number": 77,
        "pr_number": 77,
        "classifier": "clearance-v1",
        "summary": "Blocked.",
        "review_state": {
            "current_approvals": [],
            "stale_approvals": [],
            "blocking_reviewers": [],
            "unresolved_thread_count": 0,
        },
        "confidence": {"reasons": [], "semantic_fix_verified": False, "semantic_fix_note": ""},
        "labels": {"add": ["clearance-2-blocked"], "remove": []},
        "reactions": {"add": [], "remove": []},
        "pr_url": "https://github.test/pull/77",
        "head_sha": "sha-abc",
        "target_kind": "pull_request",
    }
    automation = {
        "enabled": True,
        "status": "error",
        "reason": "1 writeback operation failed; first: resolveReviewThread (HTTPStatusError, HTTP 403)",
        "sync_actions": [],
        "sync_actions_count": 1,
        "dry_run": False,
        "head_sha": "sha-abc",
        "writeback_failures": [
            {
                "operation": "resolveReviewThread",
                "error_class": "HTTPStatusError",
                "status": 403,
                "repo": "iterwheel/voyager",
                "pr": 77,
                "issue": None,
                "thread_id": "PRRT_test",
                "suggested_action": "Verify the GitHub App permissions.",
            }
        ],
        "writeback_failure_count": 1,
        "writeback_failure_reason": "1 writeback operation failed; first: resolveReviewThread (HTTPStatusError, HTTP 403)",
    }

    comment = build_clearance_comment(evaluation, automation=automation)
    assert "⚠️ Automation writeback: resolveReviewThread failed" in comment
    assert "HTTP 403" in comment
    assert "iterwheel/voyager#77 thread PRRT_test" in comment
    assert "Verify the GitHub App permissions" in comment
    # No secrets
    assert "ghp_" not in comment
    assert "token=" not in comment


async def test_no_writeback_warning_when_no_failures() -> None:
    """When automation has no writeback_failures, no warning appears."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache
    from voyager.bots.clearance.enrichment import build_clearance_comment

    reset_review_request_users_cache()

    evaluation = {
        "status": "clearance_ready",
        "conclusion": "success",
        "issue_number": 77,
        "pr_number": 77,
        "classifier": "clearance-v1",
        "summary": "Ready.",
        "review_state": {
            "current_approvals": ["alice"],
            "stale_approvals": [],
            "blocking_reviewers": [],
            "unresolved_thread_count": 0,
        },
        "confidence": {"reasons": [], "semantic_fix_verified": False, "semantic_fix_note": ""},
        "labels": {"add": ["clearance-4-ready-for-merge"], "remove": []},
        "reactions": {"add": ["+1"], "remove": []},
        "pr_url": "https://github.test/pull/77",
        "head_sha": "sha-abc",
        "target_kind": "pull_request",
    }
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [],
        "sync_actions_count": 0,
        "dry_run": False,
        "head_sha": "sha-abc",
    }

    comment = build_clearance_comment(evaluation, automation=automation)
    assert "⚠️ Automation writeback:" not in comment
    assert "writeback failure" not in comment.lower()
