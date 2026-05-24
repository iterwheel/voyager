"""CHG-1813: Tests for GitHubGraphQLError and writeback failure helpers."""

from __future__ import annotations

import logging

import httpx
import pytest

from voyager.core.github_app import GitHubAppClient, GitHubGraphQLError
from voyager.core.writeback import (
    _safe_exception_fields,
    build_writeback_failure,
    format_writeback_failure_warning,
)

# ---------------------------------------------------------------------------
# GitHubGraphQLError
# ---------------------------------------------------------------------------


def test_graphql_errors_raise_typed_exception():
    """GitHubAppClient.graphql() must raise GitHubGraphQLError (not RuntimeError)."""
    errors = [{"type": "FORBIDDEN", "message": "Resource not accessible"}]
    exc = GitHubGraphQLError(errors)
    assert isinstance(exc, Exception)
    assert exc.errors is errors
    # The error type name is included (sanitized — no raw payload message)
    assert "1 error" in str(exc)
    # Raw exception message is NOT in str()
    assert "Resource not accessible" not in str(exc)


def test_graphql_error_is_not_runtime_error():
    """GitHubGraphQLError must NOT be a RuntimeError subclass."""
    assert not issubclass(GitHubGraphQLError, RuntimeError)


@pytest.mark.asyncio
async def test_graphql_error_log_sanitizes_token_messages(monkeypatch, caplog):
    """GitHubAppClient.graphql() must not write raw GraphQL error tokens to logs."""
    errors = [
        {
            "type": "FORBIDDEN",
            "message": (
                "Denied token=github_pat_SECRET123 Bearer ghp_SECRET456 "
                "ghs_SECRET789 ghs_abc.def-ghi.jkl ghs_abcd-ef-"
            ),
        }
    ]
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"errors": errors}))
    async_client = httpx.AsyncClient(transport=transport)
    client = GitHubAppClient({})

    async def fake_installation_token(_app_slug: str, *, repository: str | None = None) -> str:
        return "ghs_INSTALLATION_TOKEN"

    monkeypatch.setattr(client, "installation_token", fake_installation_token)
    monkeypatch.setattr(client, "_async_client", lambda: async_client)
    caplog.set_level(logging.WARNING, logger="voyager.core.github_app")

    with pytest.raises(GitHubGraphQLError):
        await client.graphql(
            "iterwheel-clearance",
            "iterwheel/voyager",
            query="query { viewer { login } }",
            variables={},
        )
    await async_client.aclose()

    log_text = caplog.text
    assert "github.graphql.errors" in log_text
    assert "FORBIDDEN" in log_text
    assert "token=[redacted]" in log_text
    assert "Bearer [redacted]" in log_text
    assert "github_pat_" not in log_text
    assert "ghp_" not in log_text
    assert "ghs_" not in log_text
    assert "def-ghi" not in log_text
    assert "abcd-ef" not in log_text
    assert "SECRET" not in log_text


# ---------------------------------------------------------------------------
# build_writeback_failure — non-status HTTPError
# ---------------------------------------------------------------------------


def test_build_writeback_failure_non_status_http_error():
    exc = httpx.HTTPError("connection reset")
    failure = build_writeback_failure(
        operation="resolveReviewThread",
        exc=exc,
        repository="iterwheel/sandbox",
        pr=49,
        thread_id="PRRT_abc",
    )
    assert failure["operation"] == "resolveReviewThread"
    assert failure["error_class"] == "HTTPError"
    assert failure["status"] is None
    assert failure["repo"] == "iterwheel/sandbox"
    assert failure["pr"] == 49
    assert failure["thread_id"] == "PRRT_abc"
    assert "connection reset" not in str(failure)
    # Base httpx.HTTPError (not status, not timeout, not connect/read) gets generic suggestion
    assert "retry after correcting" in failure["suggested_action"]


def test_build_writeback_failure_timeout_error():
    exc = TimeoutError("timed out")
    failure = build_writeback_failure(
        operation="resolveReviewThread",
        exc=exc,
        repository="iterwheel/sandbox",
        pr=49,
        thread_id="PRRT_abc",
    )
    assert failure["error_class"] == "TimeoutError"
    assert failure["status"] is None
    assert "reachabilit" in failure["suggested_action"]


def test_build_writeback_failure_graphql_error():
    exc = GitHubGraphQLError(
        [{"type": "FORBIDDEN", "message": "Resource not accessible by integration"}]
    )
    failure = build_writeback_failure(
        operation="resolveReviewThread",
        exc=exc,
        repository="iterwheel/sandbox",
        pr=49,
        thread_id="PRRT_abc",
    )
    assert failure["error_class"] == "GraphQLError"
    assert failure["status"] is None
    assert "Verify the GitHub App permissions" in failure["suggested_action"]
    assert "viewerCanResolve" in failure["suggested_action"]
    assert failure["graphql_error_types"] == ["FORBIDDEN"]
    assert failure["graphql_error_messages"] == ["Resource not accessible by integration"]
    assert failure["graphql_error_summary"] == "FORBIDDEN: Resource not accessible by integration"


def test_build_writeback_failure_graphql_error_sanitizes_public_fields():
    exc = GitHubGraphQLError(
        [
            {
                "type": "FORBIDDEN",
                "message": (
                    "Resource token=ghp_SECRET123 Bearer ghs_SECRET456 "
                    "github_pat_SECRET789 ghs_abc.def-ghi.jkl "
                    "ghs_abcd-ef- not accessible"
                ),
            }
        ]
    )
    failure = build_writeback_failure(
        operation="resolveReviewThread",
        exc=exc,
        repository="iterwheel/sandbox",
        pr=49,
        thread_id="PRRT_abc",
    )
    all_text = str(failure)
    assert "ghp_" not in all_text
    assert "ghs_" not in all_text
    assert "github_pat_" not in all_text
    assert "def-ghi" not in all_text
    assert "abcd-ef" not in all_text
    assert "token=ghp" not in all_text
    assert "token=[redacted]" in all_text
    assert "Bearer [redacted]" in all_text


def test_build_writeback_failure_http_status_error_403():
    exc = httpx.HTTPStatusError(
        "Forbidden",
        request=httpx.Request("POST", "https://api.github.com/graphql"),
        response=httpx.Response(403),
    )
    failure = build_writeback_failure(
        operation="resolveReviewThread",
        exc=exc,
        repository="iterwheel/sandbox",
        pr=49,
        thread_id="PRRT_abc",
    )
    assert failure["error_class"] == "HTTPStatusError"
    assert failure["status"] == 403
    assert "Verify the GitHub App permissions" in failure["suggested_action"]


def test_build_writeback_failure_http_status_error_429():
    exc = httpx.HTTPStatusError(
        "Too Many Requests",
        request=httpx.Request("POST", "https://api.github.com/graphql"),
        response=httpx.Response(429),
    )
    failure = build_writeback_failure(
        operation="resolveReviewThread",
        exc=exc,
        repository="iterwheel/sandbox",
        pr=49,
        thread_id="PRRT_abc",
    )
    assert failure["status"] == 429
    assert "rate-limit" in failure["suggested_action"].lower()


def test_build_writeback_failure_token_bearing_message_redacted():
    """build_writeback_failure must NOT include raw exception string or tokens."""
    exc = httpx.HTTPError("https://api.github.com/repos?token=ghp_SECRET123")
    failure = build_writeback_failure(
        operation="addLabels",
        exc=exc,
        repository="iterwheel/sandbox",
        issue=42,
    )
    all_text = str(failure)
    assert "ghp_" not in all_text
    assert "token=" not in all_text
    assert "SECRET" not in all_text
    assert "https://" not in all_text


# ---------------------------------------------------------------------------
# format_writeback_failure_warning
# ---------------------------------------------------------------------------


def test_format_warning_thread_operation():
    failure = {
        "operation": "resolveReviewThread",
        "error_class": "HTTPStatusError",
        "status": 403,
        "repo": "iterwheel/sandbox",
        "pr": 49,
        "issue": None,
        "thread_id": "PRRT_abc",
        "suggested_action": "Verify permissions.",
    }
    line = format_writeback_failure_warning(failure)
    assert "⚠️ Automation writeback: resolveReviewThread failed (HTTPStatusError, HTTP 403)" in line
    assert "iterwheel/sandbox#49 thread PRRT_abc" in line
    assert "Verify permissions." in line


def test_format_warning_includes_graphql_summary():
    failure = {
        "operation": "resolveReviewThread",
        "error_class": "GraphQLError",
        "status": None,
        "repo": "iterwheel/sandbox",
        "pr": 49,
        "issue": None,
        "thread_id": "PRRT_abc",
        "graphql_error_summary": "FORBIDDEN: Resource not accessible by integration",
        "suggested_action": "Check reviewThreads.viewerCanResolve before retrying.",
    }
    line = format_writeback_failure_warning(failure)
    assert "GitHub GraphQL: FORBIDDEN: Resource not accessible by integration." in line
    assert "reviewThreads.viewerCanResolve" in line


def test_format_warning_issue_operation():
    failure = {
        "operation": "addLabels",
        "error_class": "HTTPError",
        "status": None,
        "repo": "iterwheel/sandbox",
        "pr": None,
        "issue": 42,
        "thread_id": None,
        "suggested_action": "Check and retry.",
    }
    line = format_writeback_failure_warning(failure)
    assert "iterwheel/sandbox#42" in line
    assert " thread " not in line


def test_format_warning_malformed_unknown_target():
    """A10: missing pr and issue → 'unknown target'."""
    failure = {
        "operation": "upsertComment",
        "error_class": "HTTPError",
        "status": None,
        "repo": "iterwheel/sandbox",
        "pr": None,
        "issue": None,
        "thread_id": None,
        "suggested_action": "Check and retry.",
    }
    line = format_writeback_failure_warning(failure)
    assert "unknown target" in line


def test_format_warning_no_secrets():
    failure = {
        "operation": "resolveReviewThread",
        "error_class": "HTTPError",
        "status": None,
        "repo": "iterwheel/sandbox",
        "pr": 49,
        "issue": None,
        "thread_id": "PRRT_abc",
        "suggested_action": "Check.",
    }
    line = format_writeback_failure_warning(failure)
    assert "ghp_" not in line
    assert "token=" not in line
    assert "Bearer" not in line


# ---------------------------------------------------------------------------
# _safe_exception_fields
# ---------------------------------------------------------------------------


def test_safe_exception_fields_http_status_error():
    exc = httpx.HTTPStatusError(
        "Forbidden",
        request=httpx.Request("POST", "https://api.github.com/graphql"),
        response=httpx.Response(403),
    )
    fields = _safe_exception_fields(exc)
    assert fields["error_class"] == "HTTPStatusError"
    assert fields["status"] == 403


def test_safe_exception_fields_generic():
    exc = RuntimeError("some error")
    fields = _safe_exception_fields(exc)
    assert fields["error_class"] == "RuntimeError"
    assert fields["status"] is None


def test_safe_exception_fields_no_message_leak():
    """_safe_exception_fields must never include str(exc)."""
    exc = RuntimeError("secret token=ghp_leaked")
    fields = _safe_exception_fields(exc)
    all_text = str(fields)
    assert "ghp_" not in all_text
    assert "token=" not in all_text
    assert "secret" not in all_text
