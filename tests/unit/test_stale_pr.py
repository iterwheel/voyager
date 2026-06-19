"""Tests for stale-PR triage — label/comment on inactive open PRs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from voyager.bots.stale_pr import (
    STALE_COMMENT_MARKER,
    STALE_LABEL,
    _build_stale_comment_body,
    _find_open_prs,
    _has_recent_reminder_comment,
    _has_stale_label,
    _is_older_than,
    run_stale_pr_triage,
)
from voyager.core.github_app import GitHubAppClient

# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestIsOlderThan:
    """``_is_older_than`` — staleness timestamp check."""

    def test_none_returns_false(self) -> None:
        assert _is_older_than(None, days=7) is False

    def test_empty_string_returns_false(self) -> None:
        assert _is_older_than("", days=7) is False

    def test_garbage_string_returns_false(self) -> None:
        assert _is_older_than("not-a-date", days=7) is False

    def test_old_timestamp_returns_true(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        assert _is_older_than(old, days=7) is True

    def test_recent_timestamp_returns_false(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        assert _is_older_than(recent, days=7) is False

    def test_exactly_n_days_old_returns_true(self) -> None:
        boundary = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        assert _is_older_than(boundary, days=7) is True

    def test_z_suffix_normalized(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=14)).isoformat().replace("+00:00", "Z")
        assert _is_older_than(old, days=7) is True


class TestHasStaleLabel:
    """``_has_stale_label`` — label detection on GitHub PR dicts."""

    async def test_no_labels_returns_false(self) -> None:
        pr: dict[str, Any] = {"labels": []}
        assert await _has_stale_label(pr) is False

    async def test_unrelated_labels_returns_false(self) -> None:
        pr: dict[str, Any] = {"labels": [{"name": "bug"}, {"name": "enhancement"}]}
        assert await _has_stale_label(pr) is False

    async def test_stale_label_present_returns_true(self) -> None:
        pr: dict[str, Any] = {"labels": [{"name": STALE_LABEL}]}
        assert await _has_stale_label(pr) is True

    async def test_stale_label_among_others_returns_true(self) -> None:
        pr: dict[str, Any] = {
            "labels": [{"name": "bug"}, {"name": STALE_LABEL}, {"name": "enhancement"}]
        }
        assert await _has_stale_label(pr) is True

    async def test_plain_string_labels_still_work(self) -> None:
        pr: dict[str, Any] = {"labels": [STALE_LABEL]}
        assert await _has_stale_label(pr) is True


class TestBuildStaleCommentBody:
    """``_build_stale_comment_body`` — reminder comment text."""

    def test_includes_marker(self) -> None:
        body = _build_stale_comment_body(stale_days=7)
        assert STALE_COMMENT_MARKER in body

    def test_informs_no_automatic_close(self) -> None:
        body = _build_stale_comment_body(stale_days=7)
        assert "no automatic close" in body.lower()

    def test_mentions_stale_label(self) -> None:
        body = _build_stale_comment_body(stale_days=7)
        assert STALE_LABEL in body

    def test_uses_customizable_stale_days(self) -> None:
        body = _build_stale_comment_body(stale_days=14)
        assert "14 days" in body


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_client_and_transport(
    handler: Any,
) -> tuple[GitHubAppClient, httpx.AsyncClient, pytest.MonkeyPatch]:
    """Build a GitHubAppClient with a MockTransport and a faked
    ``installation_token`` so that ``client.request()`` bypasses the
    real auth flow and calls through to the mock HTTP layer."""
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    client = GitHubAppClient({})
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(client, "_async_client", lambda: async_client)
    monkeypatch.setattr(client, "installation_token", AsyncMock(return_value="ghp_test"))
    return client, async_client, monkeypatch


# ---------------------------------------------------------------------------
# _find_open_prs
# ---------------------------------------------------------------------------


class TestFindOpenPrs:
    """``_find_open_prs`` — search issues API."""

    @pytest.mark.asyncio
    async def test_returns_items_from_search_response(self) -> None:
        items = [
            {"number": 1, "updated_at": "2026-06-01T00:00:00Z", "labels": []},
            {"number": 2, "updated_at": "2026-06-18T00:00:00Z", "labels": []},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/search/issues" in str(request.url)
            return httpx.Response(200, json={"items": items, "total_count": 2})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _find_open_prs(client, "test-bot", "iterwheel/voyager")
            assert len(result) == 2
            assert result[0]["number"] == 1
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_paginates_until_short_page(self) -> None:
        first_page = [
            {"number": number, "updated_at": "2026-06-01T00:00:00Z", "labels": []}
            for number in range(1, 101)
        ]
        second_page = [{"number": 101, "updated_at": "2026-06-01T00:00:00Z", "labels": []}]
        requested_pages: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_pages.append(request.url.params.get("page"))
            page = request.url.params.get("page")
            if page == "1":
                return httpx.Response(200, json={"items": first_page, "total_count": 101})
            if page == "2":
                return httpx.Response(200, json={"items": second_page, "total_count": 101})
            return httpx.Response(500, json={"message": "unexpected page"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _find_open_prs(client, "test-bot", "iterwheel/voyager")
            assert [pr["number"] for pr in result] == list(range(1, 102))
            assert requested_pages == ["1", "2"]
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty_list(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"items": [], "total_count": 0})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _find_open_prs(client, "test-bot", "iterwheel/voyager")
            assert result == []
        finally:
            monkeypatch.undo()
            await async_client.aclose()


# ---------------------------------------------------------------------------
# _has_recent_reminder_comment
# ---------------------------------------------------------------------------


class TestHasRecentReminderComment:
    """``_has_recent_reminder_comment`` — dedup marker check."""

    @pytest.mark.asyncio
    async def test_no_comments_returns_false(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _has_recent_reminder_comment(
                client,
                "test-bot",
                "iterwheel/voyager",
                42,
                within_days=7,
            )
            assert result is False
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_bot_comment_with_marker_within_window_returns_true(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        comments = [
            {
                "id": 1,
                "user": {"login": "test-bot[bot]"},
                "body": STALE_COMMENT_MARKER,
                "created_at": recent,
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=comments)

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _has_recent_reminder_comment(
                client,
                "test-bot",
                "iterwheel/voyager",
                42,
                within_days=7,
            )
            assert result is True
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_bot_comment_with_marker_outside_window_returns_false(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        comments = [
            {
                "id": 1,
                "user": {"login": "test-bot[bot]"},
                "body": STALE_COMMENT_MARKER,
                "created_at": old,
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=comments)

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _has_recent_reminder_comment(
                client,
                "test-bot",
                "iterwheel/voyager",
                42,
                within_days=7,
            )
            assert result is False
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_other_user_comments_ignored(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        comments = [
            {
                "id": 1,
                "user": {"login": "human-user"},
                "body": STALE_COMMENT_MARKER,
                "created_at": recent,
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=comments)

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _has_recent_reminder_comment(
                client,
                "test-bot",
                "iterwheel/voyager",
                42,
                within_days=7,
            )
            assert result is False
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_bot_comment_without_marker_ignored(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        comments = [
            {
                "id": 1,
                "user": {"login": "test-bot[bot]"},
                "body": "some other comment",
                "created_at": recent,
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=comments)

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _has_recent_reminder_comment(
                client,
                "test-bot",
                "iterwheel/voyager",
                42,
                within_days=7,
            )
            assert result is False
        finally:
            monkeypatch.undo()
            await async_client.aclose()


# ---------------------------------------------------------------------------
# Integration-style: run_stale_pr_triage via MockTransport
# ---------------------------------------------------------------------------

_STALE_PR_NUMBER = 1
_FRESH_PR_NUMBER = 2
_ALREADY_STALE_PR_NUMBER = 3
_REPO = "iterwheel/voyager"
_APP_SLUG = "test-bot"
_STALE_DAYS = 7
_OLD_TS = (datetime.now(UTC) - timedelta(days=14)).isoformat()
_FRESH_TS = (datetime.now(UTC) - timedelta(hours=1)).isoformat()


def _search_response() -> dict[str, Any]:
    return {
        "total_count": 3,
        "items": [
            {
                "number": _STALE_PR_NUMBER,
                "updated_at": _OLD_TS,
                "labels": [],
                "title": "Old stale PR",
            },
            {
                "number": _FRESH_PR_NUMBER,
                "updated_at": _FRESH_TS,
                "labels": [{"name": "bug"}],
                "title": "Recent PR",
            },
            {
                "number": _ALREADY_STALE_PR_NUMBER,
                "updated_at": _OLD_TS,
                "labels": [{"name": STALE_LABEL}],
                "title": "Already stale PR",
            },
        ],
    }


def _build_triage_mock_client(
    *,
    comments_response: list[dict[str, Any]] | None = None,
    capture: list[dict[str, Any]] | None = None,
) -> tuple[GitHubAppClient, httpx.AsyncClient, pytest.MonkeyPatch]:
    """Build a GitHubAppClient backed by MockTransport for triage tests.

    Responds to GET /search/issues, GET /comments, POST /labels, POST /comments.
    """
    comments = comments_response or []
    captured: list[dict[str, Any]] = capture if capture is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        captured.append({"method": request.method, "url": url, "path": path})

        if "/search/issues" in url:
            return httpx.Response(200, json=_search_response())
        if "/comments" in url and request.method == "GET":
            return httpx.Response(200, json=comments)
        if path.endswith(f"/labels/{STALE_LABEL}") and request.method == "GET":
            return httpx.Response(404, json={"message": "not found"})
        if path == "/repos/iterwheel/voyager/labels" and request.method == "POST":
            return httpx.Response(201, json={"name": STALE_LABEL})
        if "/issues/" in path and path.endswith("/labels") and request.method == "POST":
            return httpx.Response(200, json={})
        if "/comments" in url and request.method == "POST":
            return httpx.Response(201, json={"id": 999, "body": "created"})
        return httpx.Response(404, json={"message": "unexpected"})

    return _mock_client_and_transport(handler)


class TestRunStalePrTriage:
    """``run_stale_pr_triage`` — end-to-end triage cycle."""

    @pytest.mark.asyncio
    async def test_stale_pr_gets_labeled_and_commented_once(self) -> None:
        capture: list[dict[str, Any]] = []
        client, async_client, monkeypatch = _build_triage_mock_client(
            comments_response=[],
            capture=capture,
        )
        try:
            summary = await run_stale_pr_triage(
                client,
                _APP_SLUG,
                _REPO,
                stale_days=_STALE_DAYS,
            )

            assert summary["checked"] == 3
            assert _STALE_PR_NUMBER in summary["labeled"]
            assert _ALREADY_STALE_PR_NUMBER in summary["already_labeled"]
            assert _FRESH_PR_NUMBER in summary["skipped_fresh"]
            assert _STALE_PR_NUMBER in summary["commented"]
            assert _ALREADY_STALE_PR_NUMBER in summary["commented"]

            repo_label_calls = [
                c
                for c in capture
                if c["method"] == "POST" and c["path"] == "/repos/iterwheel/voyager/labels"
            ]
            issue_label_calls = [
                c
                for c in capture
                if c["method"] == "POST"
                and "/issues/" in c["path"]
                and c["path"].endswith("/labels")
            ]
            comment_calls = [
                c for c in capture if c["method"] == "POST" and "/comments" in c["url"]
            ]
            label_lookup_index = next(
                i
                for i, call in enumerate(capture)
                if call["method"] == "GET" and call["path"].endswith(f"/labels/{STALE_LABEL}")
            )
            repo_label_index = capture.index(repo_label_calls[0])
            issue_label_index = capture.index(issue_label_calls[0])
            assert label_lookup_index < repo_label_index < issue_label_index
            assert len(repo_label_calls) == 1
            assert len(issue_label_calls) == 1
            # Both stale PRs get a comment — #1 is newly labeled, #3 already
            # had the label (from a prior run) but no existing reminder comment
            assert len(comment_calls) == 2
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_fresh_pr_untouched(self) -> None:
        capture: list[dict[str, Any]] = []
        client, async_client, monkeypatch = _build_triage_mock_client(capture=capture)
        try:
            summary = await run_stale_pr_triage(
                client,
                _APP_SLUG,
                _REPO,
                stale_days=_STALE_DAYS,
            )

            assert _FRESH_PR_NUMBER in summary["skipped_fresh"]
            assert _FRESH_PR_NUMBER not in summary["labeled"]
            assert _FRESH_PR_NUMBER not in summary["commented"]
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_already_stale_pr_not_relabeled(self) -> None:
        capture: list[dict[str, Any]] = []
        client, async_client, monkeypatch = _build_triage_mock_client(capture=capture)
        try:
            summary = await run_stale_pr_triage(
                client,
                _APP_SLUG,
                _REPO,
                stale_days=_STALE_DAYS,
            )

            assert _ALREADY_STALE_PR_NUMBER in summary["already_labeled"]
            assert _ALREADY_STALE_PR_NUMBER not in summary["labeled"]
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_recent_comment_suppresses_new_comment(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        comments = [
            {
                "id": 1,
                "user": {"login": "test-bot[bot]"},
                "body": STALE_COMMENT_MARKER,
                "created_at": recent,
            },
        ]

        capture: list[dict[str, Any]] = []
        client, async_client, monkeypatch = _build_triage_mock_client(
            comments_response=comments,
            capture=capture,
        )
        try:
            summary = await run_stale_pr_triage(
                client,
                _APP_SLUG,
                _REPO,
                stale_days=_STALE_DAYS,
            )

            assert _STALE_PR_NUMBER not in summary["commented"]

            comment_create_calls = [
                c
                for c in capture
                if c["method"] == "POST" and "/comments" in c["url"] and "/issues" in c["url"]
            ]
            assert len(comment_create_calls) == 0
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_old_comment_allows_new_comment(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        comments = [
            {
                "id": 1,
                "user": {"login": "test-bot[bot]"},
                "body": STALE_COMMENT_MARKER,
                "created_at": old,
            },
        ]

        capture: list[dict[str, Any]] = []
        client, async_client, monkeypatch = _build_triage_mock_client(
            comments_response=comments,
            capture=capture,
        )
        try:
            summary = await run_stale_pr_triage(
                client,
                _APP_SLUG,
                _REPO,
                stale_days=_STALE_DAYS,
            )

            assert _STALE_PR_NUMBER in summary["commented"]
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_empty_repo_no_crashes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"items": [], "total_count": 0})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            summary = await run_stale_pr_triage(
                client,
                _APP_SLUG,
                _REPO,
                stale_days=_STALE_DAYS,
            )
            assert summary["checked"] == 0
        finally:
            monkeypatch.undo()
            await async_client.aclose()
