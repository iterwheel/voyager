"""Concurrency contracts for the public Clearance automation entry point."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import pytest

from voyager.bots.clearance.pipeline import compute_clearance_automation
from voyager.bots.clearance.state import StateStore

PipelineKey = tuple[str, int]


class _GatedPullRequestClient:
    """Fake client that blocks each pipeline task at its first PR fetch."""

    def __init__(self, keys: tuple[PipelineKey, ...], *, fail_first_fetch: bool = False) -> None:
        self.entered = {key: asyncio.Event() for key in keys}
        self.release = {key: asyncio.Event() for key in keys}
        self.gated_entries: list[PipelineKey] = []
        self._gated_task_ids: set[int] = set()
        self._fail_first_fetch = fail_first_fetch

    async def pull_request(
        self, app_slug: str, repository: str, pull_number: int
    ) -> dict[str, Any]:
        key = (repository, pull_number)
        task = asyncio.current_task()
        assert task is not None
        if id(task) not in self._gated_task_ids:
            self._gated_task_ids.add(id(task))
            self.gated_entries.append(key)
            self.entered[key].set()
            await self.release[key].wait()
            if self._fail_first_fetch:
                self._fail_first_fetch = False
                raise RuntimeError("simulated first pull_request failure")
        return {
            "number": pull_number,
            "title": "Concurrency fixture PR",
            "head": {"sha": "head-sha", "repo": {"full_name": repository}},
            "base": {"ref": "main", "repo": {"full_name": repository}},
            "user": {"login": "author"},
        }

    async def pull_request_review_threads(
        self, app_slug: str, repository: str, pull_number: int
    ) -> list[dict[str, Any]]:
        return []

    async def pull_request_reviews(
        self, app_slug: str, repository: str, pull_number: int
    ) -> list[dict[str, Any]]:
        return []

    async def issue_comments(
        self, app_slug: str, repository: str, issue_number: int
    ) -> list[dict[str, Any]]:
        return []

    async def pull_request_head_updated_at(
        self, app_slug: str, repository: str, pull_number: int
    ) -> None:
        return None

    async def branch_protected(self, app_slug: str, repository: str, branch: str) -> bool:
        return True


def _route(pr_number: int) -> dict[str, Any]:
    return {
        "agent": "iterwheel-clearance",
        "kind": "clearance_readiness",
        "validation": {"pr_number": pr_number, "issue_number": pr_number},
        "writeback": {"dynamic": "clearance_readiness"},
    }


def _compute(
    client: _GatedPullRequestClient, key: PipelineKey, state_root: Path
) -> Awaitable[dict[str, Any]]:
    repository, pr_number = key
    return compute_clearance_automation(
        client,
        _route(pr_number),
        repository=repository,
        store=StateStore(state_root / repository.replace("/", "-") / str(pr_number)),
    )


async def test_compute_clearance_automation_serializes_same_repository_and_pr(
    tmp_path: Path,
) -> None:
    key = ("iterwheel/voyager", 292)
    client = _GatedPullRequestClient((key,))

    first = asyncio.create_task(_compute(client, key, tmp_path))
    await client.entered[key].wait()

    second_attempted = asyncio.Event()

    async def run_second() -> dict[str, Any]:
        second_attempted.set()
        return await _compute(client, key, tmp_path)

    second = asyncio.create_task(run_second())
    await second_attempted.wait()

    assert client.gated_entries == [key]

    client.release[key].set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["enabled"] is True
    assert second_result["enabled"] is True
    assert client.gated_entries == [key, key]


def test_compute_clearance_automation_supports_same_key_contention_across_fresh_event_loops(
    tmp_path: Path,
) -> None:
    key = ("iterwheel/voyager", 297)

    async def run_contended_round(state_root: Path) -> None:
        client = _GatedPullRequestClient((key,))

        first = asyncio.create_task(_compute(client, key, state_root))
        await client.entered[key].wait()

        second_attempted = asyncio.Event()

        async def run_second() -> dict[str, Any]:
            second_attempted.set()
            return await _compute(client, key, state_root)

        second = asyncio.create_task(run_second())
        await second_attempted.wait()

        assert client.gated_entries == [key]

        client.release[key].set()
        first_result, second_result = await asyncio.gather(first, second)

        assert first_result["enabled"] is True
        assert second_result["enabled"] is True
        assert client.gated_entries == [key, key]

    asyncio.run(run_contended_round(tmp_path / "first-loop"))
    asyncio.run(run_contended_round(tmp_path / "second-loop"))


@pytest.mark.parametrize(
    ("first_key", "second_key"),
    [
        (("iterwheel/voyager", 293), ("iterwheel/voyager", 294)),
        (("other-org/voyager", 295), ("another-org/voyager", 295)),
    ],
    ids=["same-repository-different-pr", "different-repository-same-pr-number"],
)
async def test_compute_clearance_automation_allows_distinct_keys_to_enter_concurrently(
    tmp_path: Path, first_key: PipelineKey, second_key: PipelineKey
) -> None:
    client = _GatedPullRequestClient((first_key, second_key))

    first = asyncio.create_task(_compute(client, first_key, tmp_path))
    second = asyncio.create_task(_compute(client, second_key, tmp_path))

    await client.entered[first_key].wait()
    await client.entered[second_key].wait()

    assert client.gated_entries == [first_key, second_key]

    client.release[first_key].set()
    client.release[second_key].set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["enabled"] is True
    assert second_result["enabled"] is True


async def test_compute_clearance_automation_releases_same_key_after_fetch_exception(
    tmp_path: Path,
) -> None:
    key = ("iterwheel/voyager", 296)
    client = _GatedPullRequestClient((key,), fail_first_fetch=True)

    first = asyncio.create_task(_compute(client, key, tmp_path))
    await client.entered[key].wait()

    second_attempted = asyncio.Event()

    async def run_second() -> dict[str, Any]:
        second_attempted.set()
        return await _compute(client, key, tmp_path)

    second = asyncio.create_task(run_second())
    await second_attempted.wait()

    assert client.gated_entries == [key]

    client.release[key].set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["status"] == "error"
    assert second_result["enabled"] is True
    assert client.gated_entries == [key, key]
