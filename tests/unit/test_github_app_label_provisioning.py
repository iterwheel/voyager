from __future__ import annotations

from unittest.mock import AsyncMock, call

import httpx
import pytest

from voyager.core.github_app import GitHubAppClient


def _status_error(status_code: int, *, body: object | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(status_code, json=body, request=request)
    return httpx.HTTPStatusError("github error", request=request, response=response)


@pytest.mark.asyncio
async def test_ensure_label_creates_missing_repo_label() -> None:
    client = GitHubAppClient({})
    client.request = AsyncMock(side_effect=[_status_error(404), {"name": "assembly-fix-round-1"}])  # type: ignore[method-assign]

    await client.ensure_label(
        "iterwheel-assembly",
        "iterwheel/voyager",
        "assembly-fix-round-1",
        color="#cfd3d7",
        description="Assembly automated fix round marker.",
    )

    assert client.request.await_args_list == [
        call(
            "iterwheel-assembly",
            "GET",
            "/repos/iterwheel/voyager/labels/assembly-fix-round-1",
            repository="iterwheel/voyager",
        ),
        call(
            "iterwheel-assembly",
            "POST",
            "/repos/iterwheel/voyager/labels",
            repository="iterwheel/voyager",
            json_body={
                "name": "assembly-fix-round-1",
                "color": "cfd3d7",
                "description": "Assembly automated fix round marker.",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_ensure_label_treats_concurrent_already_exists_as_success() -> None:
    client = GitHubAppClient({})
    client.request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _status_error(404),
            _status_error(422, body={"errors": [{"code": "already_exists"}]}),
        ]
    )

    await client.ensure_label("iterwheel-assembly", "iterwheel/voyager", "assembly-fix-round-1")

    assert client.request.await_count == 2
