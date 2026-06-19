"""Tests for CI-failing sweep — find open PRs with red CI and flag them."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from voyager.bots.ci_failing import (
    CI_FAILING_COMMENT_MARKER_PREFIX,
    CI_FAILING_LABEL,
    _ci_failing_marker,
    _existing_ci_failing_comment,
    _find_open_prs,
    _has_ci_failing_label,
    _is_failing_check,
    run_ci_failing_sweep,
)
from voyager.core.github_app import GitHubAppClient

# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestCiFailingMarker:
    """``_ci_failing_marker`` — HTML-comment marker per run-id."""

    def test_marker_contains_prefix(self) -> None:
        marker = _ci_failing_marker(42)
        assert CI_FAILING_COMMENT_MARKER_PREFIX in marker

    def test_marker_contains_run_id(self) -> None:
        marker = _ci_failing_marker(42)
        assert "42" in marker

    def test_marker_is_valid_html_comment(self) -> None:
        marker = _ci_failing_marker(99)
        assert marker.startswith("<!--")
        assert marker.endswith("-->")
        assert "99" in marker


class TestCiFailingOutcomes:
    """Outcome classification for required Checks API and Status API contexts."""

    def test_startup_failure_is_red(self) -> None:
        assert _is_failing_check({"conclusion": "startup_failure"}) is True

    def test_legacy_status_failure_is_red(self) -> None:
        assert _is_failing_check({"state": "failure", "type": "status_context"}) is True

    def test_legacy_status_error_is_red(self) -> None:
        assert _is_failing_check({"state": "error", "type": "status_context"}) is True


class TestHasCiFailingLabel:
    """``_has_ci_failing_label`` — label detection on PR dicts."""

    async def test_no_labels_returns_false(self) -> None:
        pr: dict[str, Any] = {"labels": []}
        assert await _has_ci_failing_label(pr) is False

    async def test_unrelated_labels_returns_false(self) -> None:
        pr: dict[str, Any] = {"labels": [{"name": "bug"}, {"name": "enhancement"}]}
        assert await _has_ci_failing_label(pr) is False

    async def test_ci_failing_label_present_returns_true(self) -> None:
        pr: dict[str, Any] = {"labels": [{"name": CI_FAILING_LABEL}]}
        assert await _has_ci_failing_label(pr) is True

    async def test_ci_failing_label_among_others_returns_true(self) -> None:
        pr: dict[str, Any] = {
            "labels": [{"name": "bug"}, {"name": CI_FAILING_LABEL}, {"name": "enhancement"}]
        }
        assert await _has_ci_failing_label(pr) is True

    async def test_plain_string_labels_still_work(self) -> None:
        pr: dict[str, Any] = {"labels": [CI_FAILING_LABEL]}
        assert await _has_ci_failing_label(pr) is True


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
# _existing_ci_failing_comment
# ---------------------------------------------------------------------------


class TestExistingCiFailingComment:
    """``_existing_ci_failing_comment`` — dedup marker check."""

    @pytest.mark.asyncio
    async def test_no_comments_returns_false(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _existing_ci_failing_comment(
                client, "test-bot", "iterwheel/voyager", 42, 1001
            )
            assert result is False
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_bot_comment_with_matching_marker_returns_true(self) -> None:
        comments = [
            {
                "id": 1,
                "user": {"login": "test-bot[bot]"},
                "body": _ci_failing_marker(1001),
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=comments)

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _existing_ci_failing_comment(
                client, "test-bot", "iterwheel/voyager", 42, 1001
            )
            assert result is True
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_bot_comment_with_different_run_id_marker_returns_false(self) -> None:
        comments = [
            {
                "id": 1,
                "user": {"login": "test-bot[bot]"},
                "body": _ci_failing_marker(9999),
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=comments)

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _existing_ci_failing_comment(
                client, "test-bot", "iterwheel/voyager", 42, 1001
            )
            assert result is False
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_other_user_comments_ignored(self) -> None:
        comments = [
            {
                "id": 1,
                "user": {"login": "human-user"},
                "body": _ci_failing_marker(1001),
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=comments)

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            result = await _existing_ci_failing_comment(
                client, "test-bot", "iterwheel/voyager", 42, 1001
            )
            assert result is False
        finally:
            monkeypatch.undo()
            await async_client.aclose()


# ---------------------------------------------------------------------------
# run_ci_failing_sweep  (integration)
# ---------------------------------------------------------------------------


def _search_response(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": items, "total_count": len(items)}


def _pr_search_item(number: int, *, has_ci_failing: bool = False) -> dict[str, Any]:
    label = {"name": CI_FAILING_LABEL} if has_ci_failing else {"name": "enhancement"}
    return {
        "number": number,
        "labels": [label],
    }


def _pr_detail(number: int, head_sha: str) -> dict[str, Any]:
    return {
        "number": number,
        "head": {"sha": head_sha, "ref": f"branch-{number}"},
    }


def _check_run(
    run_id: int, name: str, conclusion: str, *, url: str = "https://example.com/run"
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "conclusion": conclusion,
        "html_url": url,
    }


def _check_runs_response(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"check_runs": runs, "total_count": len(runs)}


def _install_required_checks(
    monkeypatch: pytest.MonkeyPatch,
    client: GitHubAppClient,
    checks_by_pr: dict[int, list[dict[str, Any]]],
) -> None:
    async def required_checks(_app_slug: str, _repo: str, pull_number: int) -> list[dict[str, Any]]:
        return checks_by_pr.get(pull_number, [])

    monkeypatch.setattr(client, "pull_request_required_status_checks", required_checks)


class TestGitHubAppRequiredStatusChecks:
    """``pull_request_required_status_checks`` filters GitHub rollup contexts."""

    @pytest.mark.asyncio
    async def test_returns_only_required_check_and_status_contexts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/graphql"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "commits": {
                                    "nodes": [
                                        {
                                            "commit": {
                                                "statusCheckRollup": {
                                                    "contexts": {
                                                        "pageInfo": {
                                                            "hasNextPage": False,
                                                            "endCursor": None,
                                                        },
                                                        "nodes": [
                                                            {
                                                                "__typename": "CheckRun",
                                                                "databaseId": 1001,
                                                                "name": "required-test",
                                                                "conclusion": "FAILURE",
                                                                "status": "COMPLETED",
                                                                "detailsUrl": "https://example.com/required",
                                                                "isRequired": True,
                                                            },
                                                            {
                                                                "__typename": "CheckRun",
                                                                "databaseId": 1002,
                                                                "name": "optional-test",
                                                                "conclusion": "FAILURE",
                                                                "status": "COMPLETED",
                                                                "detailsUrl": "https://example.com/optional",
                                                                "isRequired": False,
                                                            },
                                                            {
                                                                "__typename": "StatusContext",
                                                                "id": "legacy-required",
                                                                "context": "legacy-ci",
                                                                "state": "SUCCESS",
                                                                "targetUrl": "https://example.com/legacy",
                                                                "isRequired": True,
                                                            },
                                                        ],
                                                    }
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
            )

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        try:
            checks = await client.pull_request_required_status_checks(
                "test-bot",
                "iterwheel/voyager",
                42,
            )
            assert [check["name"] for check in checks] == ["required-test", "legacy-ci"]
            assert checks[0]["id"] == 1001
            assert checks[1]["id"] == "legacy-required"
        finally:
            monkeypatch.undo()
            await async_client.aclose()


class TestRunCiFailingSweep:
    """End-to-end sweep logic."""

    @pytest.mark.asyncio
    async def test_failing_pr_gets_labeled_and_commented(self) -> None:
        """AC: a PR with a failed latest run → flagged (label + comment)."""
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            seen_urls.append(url)

            # Search for open PRs
            if "/search/issues" in url:
                return httpx.Response(200, json=_search_response([_pr_search_item(1)]))

            # Fetch issue comments (check if already commented)
            if "/issues/1/comments" in url:
                return httpx.Response(200, json=[])

            # Ensure label (GET → 404 → POST)
            if "/labels/ci-failing" in url:
                return httpx.Response(404, json={"message": "Not Found"})

            # Create label
            if "/labels" in url and request.method == "POST":
                return httpx.Response(201, json={"name": CI_FAILING_LABEL})

            # Add label to issue
            if "/issues/1/labels" in url and request.method == "POST":
                return httpx.Response(200, json={"labels": [CI_FAILING_LABEL]})

            # Create issue comment
            if "/issues/1/comments" in url and request.method == "POST":
                return httpx.Response(201, json={"id": 999})

            return httpx.Response(404, json={"message": "unexpected"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        _install_required_checks(
            monkeypatch,
            client,
            {1: [_check_run(1001, "test / ci", "failure")]},
        )
        try:
            result = await run_ci_failing_sweep(client, "test-bot", "iterwheel/voyager")
            assert result["checked"] == 1
            assert result["flagged"] == [1]
            assert result["cleared"] == []
            assert result["already_failing"] == []
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_required_commit_status_failure_gets_labeled_and_commented(self) -> None:
        """Legacy Commit Status API failures count as failing required checks."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/search/issues" in url:
                return httpx.Response(200, json=_search_response([_pr_search_item(7)]))

            if "/issues/7/comments" in url:
                return httpx.Response(200, json=[])

            if "/labels/ci-failing" in url:
                return httpx.Response(404, json={"message": "Not Found"})

            if "/labels" in url and request.method == "POST":
                return httpx.Response(201, json={"name": CI_FAILING_LABEL})

            if "/issues/7/labels" in url and request.method == "POST":
                return httpx.Response(200, json={"labels": [CI_FAILING_LABEL]})

            if "/issues/7/comments" in url and request.method == "POST":
                return httpx.Response(201, json={"id": 700})

            return httpx.Response(404, json={"message": "unexpected"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        _install_required_checks(
            monkeypatch,
            client,
            {
                7: [
                    {
                        "id": "status-context:legacy-ci",
                        "name": "legacy-ci",
                        "state": "failure",
                        "html_url": "https://example.com/status",
                        "type": "status_context",
                    }
                ]
            },
        )
        try:
            result = await run_ci_failing_sweep(client, "test-bot", "iterwheel/voyager")
            assert result["checked"] == 1
            assert result["flagged"] == [7]
            assert result["cleared"] == []
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_green_pr_not_flagged(self) -> None:
        """AC: a green PR → not flagged (no label added)."""
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            seen_urls.append(url)

            if "/search/issues" in url:
                return httpx.Response(200, json=_search_response([_pr_search_item(2)]))

            return httpx.Response(404, json={"message": "unexpected"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        _install_required_checks(
            monkeypatch,
            client,
            {2: [_check_run(2001, "test / ci", "success")]},
        )
        try:
            result = await run_ci_failing_sweep(client, "test-bot", "iterwheel/voyager")
            assert result["checked"] == 1
            assert result["flagged"] == []
            assert result["cleared"] == []
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_failing_then_green_removes_label(self) -> None:
        """AC: passing/again-green PRs have the label removed."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/search/issues" in url:
                return httpx.Response(
                    200,
                    json=_search_response([_pr_search_item(3, has_ci_failing=True)]),
                )

            # Remove label
            if "/issues/3/labels/ci-failing" in url:
                return httpx.Response(200, json={})

            return httpx.Response(404, json={"message": "unexpected"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        _install_required_checks(
            monkeypatch,
            client,
            {3: [_check_run(3001, "test / ci", "success")]},
        )
        try:
            result = await run_ci_failing_sweep(client, "test-bot", "iterwheel/voyager")
            assert result["checked"] == 1
            assert result["flagged"] == []
            assert result["cleared"] == [3]
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_already_failing_with_comment_skips_duplicate(self) -> None:
        """Idempotent: at most one comment per failing-run id."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/search/issues" in url:
                return httpx.Response(
                    200,
                    json=_search_response([_pr_search_item(4, has_ci_failing=True)]),
                )

            # Label ensure (GET → 404 → POST to create)
            if "/labels/ci-failing" in url:
                return httpx.Response(404, json={"message": "Not Found"})
            if "/labels" in url and request.method == "POST":
                return httpx.Response(201, json={"name": CI_FAILING_LABEL})

            # Existing comment has the marker for run 4001
            if "/issues/4/comments" in url:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 500,
                            "user": {"login": "test-bot[bot]"},
                            "body": _ci_failing_marker(4001),
                        }
                    ],
                )

            return httpx.Response(404, json={"message": "unexpected"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        _install_required_checks(
            monkeypatch,
            client,
            {4: [_check_run(4001, "test / ci", "failure")]},
        )
        try:
            result = await run_ci_failing_sweep(client, "test-bot", "iterwheel/voyager")
            assert result["checked"] == 1
            assert result["flagged"] == [4]
            assert result["already_failing"] == [4]
            assert len(result["cleared"]) == 0
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_no_check_runs_skips_pr(self) -> None:
        """PRs with no check runs are skipped."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/search/issues" in url:
                return httpx.Response(200, json=_search_response([_pr_search_item(5)]))

            return httpx.Response(404, json={"message": "unexpected"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        _install_required_checks(monkeypatch, client, {5: []})
        try:
            result = await run_ci_failing_sweep(client, "test-bot", "iterwheel/voyager")
            assert result["checked"] == 1
            assert result["flagged"] == []
            assert result["cleared"] == []
            assert result["skipped_no_checks"] == [5]
        finally:
            monkeypatch.undo()
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_pending_required_check_does_not_clear_existing_label(self) -> None:
        """Pending required checks are neither red nor green, so keep existing signal."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/search/issues" in url:
                return httpx.Response(
                    200,
                    json=_search_response([_pr_search_item(6, has_ci_failing=True)]),
                )

            return httpx.Response(404, json={"message": "unexpected"})

        client, async_client, monkeypatch = _mock_client_and_transport(handler)
        _install_required_checks(
            monkeypatch,
            client,
            {6: [{"id": 6001, "name": "test / ci", "conclusion": None, "status": "in_progress"}]},
        )
        try:
            result = await run_ci_failing_sweep(client, "test-bot", "iterwheel/voyager")
            assert result["checked"] == 1
            assert result["flagged"] == []
            assert result["cleared"] == []
            assert result["skipped_no_checks"] == [6]
        finally:
            monkeypatch.undo()
            await async_client.aclose()
