from __future__ import annotations

import types
from typing import Any

import pytest

from voyager.core.github_app import GitHubAppClient


@pytest.mark.asyncio
async def test_review_threads_fetches_original_line_anchor() -> None:
    client = GitHubAppClient({})
    captured: dict[str, Any] = {}

    async def fake_graphql(
        self: GitHubAppClient,
        app_slug: str,
        repository: str,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        _ = self, app_slug, repository
        captured["query"] = query
        captured["variables"] = variables
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PRRT_1",
                                "isResolved": False,
                                "isOutdated": True,
                                "viewerCanResolve": True,
                                "path": "app.py",
                                "line": None,
                                "originalLine": 42,
                                "startLine": None,
                                "originalStartLine": 40,
                                "diffSide": "RIGHT",
                                "startDiffSide": "RIGHT",
                                "comments": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [],
                                },
                            }
                        ],
                    }
                }
            }
        }

    client.graphql = types.MethodType(fake_graphql, client)  # type: ignore[method-assign]

    threads = await client.pull_request_review_threads("test-app", "org/repo", 123)

    query = captured["query"]
    assert "originalLine" in query
    assert "originalStartLine" in query
    assert "startDiffSide" in query
    assert captured["variables"]["number"] == 123
    assert threads[0]["line"] is None
    assert threads[0]["originalLine"] == 42
    assert threads[0]["originalStartLine"] == 40
