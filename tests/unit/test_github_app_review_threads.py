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


@pytest.mark.asyncio
async def test_review_threads_enriches_known_limitation_identity() -> None:
    client = GitHubAppClient({})

    async def fake_graphql(
        self: GitHubAppClient,
        app_slug: str,
        repository: str,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        _ = self, app_slug, repository, query, variables
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PRRT_title",
                                "isResolved": False,
                                "isOutdated": False,
                                "viewerCanResolve": True,
                                "path": "app.py",
                                "line": 10,
                                "originalLine": 10,
                                "startLine": None,
                                "originalStartLine": None,
                                "diffSide": "RIGHT",
                                "startDiffSide": None,
                                "comments": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [
                                        {
                                            "databaseId": 101,
                                            "author": {"login": "chatgpt-codex-connector"},
                                            "body": (
                                                "**<sub><sub>![P2 Badge](https://img.shields.io"
                                                "/badge/P2-yellow)</sub></sub>  Avoid stale cache writes**"
                                            ),
                                            "url": "https://example/comments/101",
                                            "createdAt": "2026-06-17T00:00:00Z",
                                            "replyTo": None,
                                        }
                                    ],
                                },
                            },
                            {
                                "id": "PRRT_kind",
                                "isResolved": False,
                                "isOutdated": False,
                                "viewerCanResolve": True,
                                "path": "ci.yml",
                                "line": 20,
                                "originalLine": 20,
                                "startLine": None,
                                "originalStartLine": None,
                                "diffSide": "RIGHT",
                                "startDiffSide": None,
                                "comments": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [
                                        {
                                            "databaseId": 102,
                                            "author": {"login": "chatgpt-codex-connector"},
                                            "body": "**P2** required check paths-ignore mismatch",
                                            "url": "https://example/comments/102",
                                            "createdAt": "2026-06-17T00:00:00Z",
                                            "replyTo": None,
                                        }
                                    ],
                                },
                            },
                        ],
                    }
                }
            }
        }

    client.graphql = types.MethodType(fake_graphql, client)  # type: ignore[method-assign]

    threads = await client.pull_request_review_threads("test-app", "org/repo", 123)

    assert threads[0]["ruleId"] == "codex-title:avoid stale cache writes"
    assert threads[0]["comments"]["nodes"][0]["ruleId"] == "codex-title:avoid stale cache writes"
    assert (
        threads[1]["ruleId"]
        == "required_check_coupling:codex-title:required check paths-ignore mismatch"
    )
    assert threads[1]["findingKind"] == "required_check_coupling"
    assert threads[1]["comments"]["nodes"][0]["findingKind"] == "required_check_coupling"


@pytest.mark.asyncio
async def test_capability_query_is_lightweight_and_guards_repeated_cursor() -> None:
    # The capability-only query must NOT request comment bodies, and must stop instead of
    # looping forever when GitHub returns hasNextPage with a repeated/null endCursor.
    client = GitHubAppClient({})
    captured: dict[str, Any] = {}
    calls = {"n": 0}

    async def fake_graphql(
        self: GitHubAppClient,
        app_slug: str,
        repository: str,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        _ = self, app_slug, repository, variables
        captured["query"] = query
        calls["n"] += 1
        # Always claim another page with the SAME cursor — a malformed/looping response.
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "STUCK"},
                        "nodes": [{"id": f"PRRT_{calls['n']}", "isResolved": False}],
                    }
                }
            }
        }

    client.graphql = types.MethodType(fake_graphql, client)  # type: ignore[method-assign]

    threads = await client.pull_request_review_thread_capabilities("test-app", "org/repo", 7)

    assert "comments(" not in captured["query"]  # no comment-body fetch
    assert "viewerCanResolve" in captured["query"]
    assert calls["n"] == 2  # first page + one repeat, then the seen-cursor guard breaks
    assert [t["id"] for t in threads] == ["PRRT_1", "PRRT_2"]
