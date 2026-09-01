"""Codex P2 on #345: the issue-referencing PR search must paginate fully.

A single 30-item page can miss the existing Assembly PR once more than 30
open PR bodies match the broad numeric query (``<n> in:body``); the
title-edit branch reuse would then mint a duplicate PR — exactly what #257
exists to prevent. GitHub search caps results at 1000 (10 x per_page=100).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from voyager.core.github_app import GitHubAppClient


def _page(count: int, start: int = 0) -> dict[str, Any]:
    return {"items": [{"number": start + i} for i in range(count)]}


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("github error", request=request, response=response)


@pytest.mark.asyncio
async def test_search_follows_pages_until_short_page() -> None:
    client = GitHubAppClient({})
    client.request = AsyncMock(side_effect=[_page(100), _page(100), _page(7)])  # type: ignore[method-assign]

    items = await client.find_open_prs_referencing_issue("slug", "o/r", 69)

    assert len(items) == 207
    paths = [c.args[2] for c in client.request.await_args_list]
    assert paths[0].endswith("page=1")
    assert paths[1].endswith("page=2")
    assert paths[2].endswith("page=3")
    assert client.request.await_count == 3  # short page stops the loop


@pytest.mark.asyncio
async def test_search_stops_at_github_1000_result_cap() -> None:
    client = GitHubAppClient({})
    client.request = AsyncMock(side_effect=[_page(100)] * 11)  # type: ignore[method-assign]

    items = await client.find_open_prs_referencing_issue("slug", "o/r", 69)

    assert len(items) == 1000
    assert client.request.await_count == 10  # hard cap, no infinite loop


@pytest.mark.asyncio
async def test_search_failure_before_any_page_returns_empty() -> None:
    client = GitHubAppClient({})
    client.request = AsyncMock(side_effect=_status_error(502))  # type: ignore[method-assign]

    assert await client.find_open_prs_referencing_issue("slug", "o/r", 69) == []


@pytest.mark.asyncio
async def test_search_failure_mid_pagination_keeps_collected_pages() -> None:
    client = GitHubAppClient({})
    client.request = AsyncMock(side_effect=[_page(100), _status_error(502)])  # type: ignore[method-assign]

    items = await client.find_open_prs_referencing_issue("slug", "o/r", 69)

    assert len(items) == 100
