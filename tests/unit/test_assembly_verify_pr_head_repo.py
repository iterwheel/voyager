"""Unit tests for _verify_pr_head_repo (VOY-1822 same-repo PR gate).

VOY-1822 requires managed PRs to satisfy headRepository == baseRepository.
Fork PRs are forbidden for managed Assembly/Codex implementation loops.

Tests cover:
  - Same-repo PR: head_repo matches base_repo → accept.
  - Fork PR: head_repo differs from base_repo → reject, write failure.
  - Missing head repo metadata (e.g. deleted/inaccessible fork) → fail closed.
  - Missing base repo metadata → fail closed.
"""

from __future__ import annotations

import pytest

from voyager.bots.assembly.writeback import _verify_pr_head_repo

REPOSITORY = "iterwheel/voyager"


def _pr(*, head_repo: str | None, base_repo: str | None) -> dict:
    """Build a minimal PR dict resembling a GitHub REST API response."""
    pr: dict = {"number": 1}
    if head_repo is not None:
        pr["head"] = {"repo": {"full_name": head_repo}}
    if base_repo is not None:
        pr["base"] = {"repo": {"full_name": base_repo}}
    return pr


# ---------------------------------------------------------------------------
# Same-repo acceptance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_repo_returns_true() -> None:
    """Same-repo PR (iterwheel/voyager → iterwheel/voyager) is accepted."""
    pr = _pr(head_repo=REPOSITORY, base_repo=REPOSITORY)
    result: dict = {"writeback_failures": []}
    ok = await _verify_pr_head_repo(pr, REPOSITORY, result)
    assert ok is True
    assert result["writeback_failures"] == []


# ---------------------------------------------------------------------------
# Fork rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_pr_returns_false() -> None:
    """Fork PR (fork/voyager → iterwheel/voyager) is rejected with a failure."""
    pr = _pr(head_repo="fork/voyager", base_repo=REPOSITORY)
    result: dict = {"writeback_failures": []}
    ok = await _verify_pr_head_repo(pr, REPOSITORY, result)
    assert ok is False
    assert len(result["writeback_failures"]) == 1
    entry = result["writeback_failures"][0]
    assert entry["operation"] == "verifyPRHeadRepo"
    assert entry["error_class"] == "ForkHeadRepo"
    assert "fork" in entry["suggested_action"].lower()


@pytest.mark.asyncio
async def test_fork_pr_records_repository_and_pr_number() -> None:
    """Failure entry includes repo and PR number for traceability."""
    pr = _pr(head_repo="fork/voyager", base_repo=REPOSITORY)
    result: dict = {"writeback_failures": []}
    await _verify_pr_head_repo(pr, REPOSITORY, result)
    entry = result["writeback_failures"][0]
    assert entry["repo"] == REPOSITORY
    assert entry["pr"] == 1


# ---------------------------------------------------------------------------
# Missing metadata — fail closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_head_repo_returns_false() -> None:
    """Missing head repo metadata causes fail-closed rejection.

    This guards against a fork head repo being deleted or inaccessible
    while the base repo is present — the same-repo invariant cannot be
    verified, so the managed flow must not proceed.
    """
    pr = _pr(head_repo=None, base_repo=REPOSITORY)
    result: dict = {"writeback_failures": []}
    ok = await _verify_pr_head_repo(pr, REPOSITORY, result)
    assert ok is False
    assert len(result["writeback_failures"]) == 1
    assert result["writeback_failures"][0]["operation"] == "verifyPRHeadRepo"


@pytest.mark.asyncio
async def test_missing_base_repo_returns_false() -> None:
    """Missing base repo metadata causes fail-closed rejection.

    Symmetrical guard for when the base repo information is unavailable.
    """
    pr = _pr(head_repo=REPOSITORY, base_repo=None)
    result: dict = {"writeback_failures": []}
    ok = await _verify_pr_head_repo(pr, REPOSITORY, result)
    assert ok is False
    assert len(result["writeback_failures"]) == 1
    assert result["writeback_failures"][0]["operation"] == "verifyPRHeadRepo"


@pytest.mark.asyncio
async def test_missing_both_repos_returns_false() -> None:
    """Both head and base repo metadata missing → fail closed."""
    pr = _pr(head_repo=None, base_repo=None)
    result: dict = {"writeback_failures": []}
    ok = await _verify_pr_head_repo(pr, REPOSITORY, result)
    assert ok is False
    assert len(result["writeback_failures"]) == 1


@pytest.mark.asyncio
async def test_head_repo_empty_string_returns_false() -> None:
    """Head repo as empty string should also fail closed (same as missing)."""
    pr = {
        "number": 1,
        "head": {"repo": {"full_name": ""}},
        "base": {"repo": {"full_name": REPOSITORY}},
    }
    result: dict = {"writeback_failures": []}
    ok = await _verify_pr_head_repo(pr, REPOSITORY, result)
    assert ok is False
    assert len(result["writeback_failures"]) == 1
