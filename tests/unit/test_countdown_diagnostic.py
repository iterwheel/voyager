from __future__ import annotations

from typing import Any

import httpx
import pytest

from voyager.core.countdown_diagnostic import (
    query_review_thread_capabilities,
    run_review_thread_resolve_canary,
)
from voyager.core.github_app import GitHubGraphQLError


class _FakeGitHubClient:
    def __init__(
        self,
        *,
        viewer_can_resolve: bool,
        resolved_after_mutation: bool = False,
        resolve_error: BaseException | str | None = None,
    ) -> None:
        self.viewer_can_resolve = viewer_can_resolve
        self.resolved_after_mutation = resolved_after_mutation
        self.resolve_error = resolve_error
        self.resolve_calls: list[tuple[str, str, str]] = []
        self.graphql_calls: list[dict[str, Any]] = []

    async def graphql(
        self,
        app_slug: str,
        repository: str,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        self.graphql_calls.append(
            {
                "app_slug": app_slug,
                "repository": repository,
                "query": query,
                "variables": variables,
            }
        )
        return {
            "viewer": {"login": "iterwheel-countdown[bot]"},
            "nodes": [
                {
                    "__typename": "PullRequestReviewThread",
                    "id": variables["threadIds"][0],
                    "isResolved": self.resolved_after_mutation,
                    "isOutdated": False,
                    "viewerCanResolve": self.viewer_can_resolve,
                    "viewerCanReply": True,
                    "pullRequest": {
                        "number": 42,
                        "repository": {"nameWithOwner": "iterwheel/voyager-sandbox"},
                    },
                }
            ],
        }

    async def resolve_review_thread(
        self,
        app_slug: str,
        repository: str,
        thread_id: str,
    ) -> dict[str, Any]:
        self.resolve_calls.append((app_slug, repository, thread_id))
        if self.resolve_error:
            if isinstance(self.resolve_error, BaseException):
                raise self.resolve_error
            raise RuntimeError(self.resolve_error)
        self.resolved_after_mutation = True
        return {"id": thread_id, "isResolved": True, "resolvedBy": {"login": app_slug + "[bot]"}}


@pytest.mark.asyncio
async def test_query_review_thread_capabilities_reports_countdown_actor_and_flags() -> None:
    client = _FakeGitHubClient(viewer_can_resolve=True)

    report = await query_review_thread_capabilities(
        client,  # type: ignore[arg-type]
        repository="iterwheel/voyager-sandbox",
        pr=42,
        thread_ids=["PRRT_123"],
    )

    assert report.actor_login == "iterwheel-countdown[bot]"
    assert report.app_slug == "iterwheel-countdown"
    thread = report.threads[0]
    assert thread.thread_id == "PRRT_123"
    assert thread.repository == "iterwheel/voyager-sandbox"
    assert thread.pr == 42
    assert thread.is_resolved is False
    assert thread.is_outdated is False
    assert thread.viewer_can_resolve is True
    assert thread.viewer_can_reply is True
    query = client.graphql_calls[0]["query"]
    assert "viewerCanResolve" in query
    assert "viewerCanReply" in query
    assert "viewer" in query


@pytest.mark.asyncio
async def test_resolve_canary_skips_when_countdown_cannot_resolve() -> None:
    client = _FakeGitHubClient(viewer_can_resolve=False)

    report = await run_review_thread_resolve_canary(
        client,  # type: ignore[arg-type]
        repository="iterwheel/voyager-sandbox",
        pr=42,
        thread_ids=["PRRT_123"],
    )

    assert client.resolve_calls == []
    assert report.operations[0].applied is False
    assert report.operations[0].reason == "viewerCanResolve is false"
    assert report.after.threads[0].is_resolved is False


@pytest.mark.asyncio
async def test_resolve_canary_applies_and_requeries_after_success() -> None:
    client = _FakeGitHubClient(viewer_can_resolve=True)

    report = await run_review_thread_resolve_canary(
        client,  # type: ignore[arg-type]
        repository="iterwheel/voyager-sandbox",
        pr=42,
        thread_ids=["PRRT_123"],
    )

    assert client.resolve_calls == [
        ("iterwheel-countdown", "iterwheel/voyager-sandbox", "PRRT_123")
    ]
    assert report.before.threads[0].is_resolved is False
    assert report.operations[0].applied is True
    assert report.operations[0].resolved_by == "iterwheel-countdown[bot]"
    assert report.after.threads[0].is_resolved is True
    assert len(client.graphql_calls) == 2


@pytest.mark.asyncio
async def test_resolve_canary_preserves_mutation_failure_and_requeries_after() -> None:
    client = _FakeGitHubClient(
        viewer_can_resolve=True,
        resolve_error=(
            "GitHub GraphQL user-token request returned errors: "
            "first_type=FORBIDDEN; first_message=Resource_not_accessible_by_integration"
        ),
    )

    report = await run_review_thread_resolve_canary(
        client,  # type: ignore[arg-type]
        app_slug="github-app-user",
        repository="iterwheel/voyager-sandbox",
        pr=42,
        thread_ids=["PRRT_123"],
    )

    assert client.resolve_calls == [("github-app-user", "iterwheel/voyager-sandbox", "PRRT_123")]
    assert report.before.threads[0].viewer_can_resolve is True
    assert report.operations[0].applied is False
    assert report.operations[0].reason == (
        "GitHub GraphQL user-token request returned errors: "
        "first_type=FORBIDDEN; first_message=Resource_not_accessible_by_integration"
    )
    assert report.after.threads[0].is_resolved is False
    assert len(client.graphql_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolve_error", "expected_reason"),
    [
        (
            GitHubGraphQLError([{"type": "FORBIDDEN", "message": "raw thread id"}]),
            "resolveReviewThread failed: GraphQLError first_type=FORBIDDEN",
        ),
        (
            httpx.HTTPStatusError(
                "forbidden",
                request=httpx.Request("POST", "https://api.github.com/graphql"),
                response=httpx.Response(403),
            ),
            "resolveReviewThread failed: HTTP 403",
        ),
    ],
)
async def test_resolve_canary_preserves_github_client_failures_and_requeries_after(
    resolve_error: BaseException,
    expected_reason: str,
) -> None:
    client = _FakeGitHubClient(viewer_can_resolve=True, resolve_error=resolve_error)

    report = await run_review_thread_resolve_canary(
        client,  # type: ignore[arg-type]
        repository="iterwheel/voyager-sandbox",
        pr=42,
        thread_ids=["PRRT_123"],
    )

    assert client.resolve_calls == [
        ("iterwheel-countdown", "iterwheel/voyager-sandbox", "PRRT_123")
    ]
    assert report.operations[0].applied is False
    assert report.operations[0].reason == expected_reason
    assert report.after.threads[0].is_resolved is False
    assert len(client.graphql_calls) == 2
