"""Unit tests for _verify_pr_head_repo (VOY-1822 same-repo PR gate).

VOY-1822 requires managed PRs to satisfy headRepository == baseRepository.
Fork PRs are forbidden for managed Assembly/Codex implementation loops.

Tests cover:
  - Same-repo PR: head_repo matches base_repo → accept.
  - Fork PR: head_repo differs from base_repo → reject, write failure.
  - Missing head repo metadata (e.g. deleted/inaccessible fork) → fail closed.
  - Missing base repo metadata → fail closed.
  - Fork PR in no_changes path: preserve_existing respects same-repo gate (no "updated").
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from voyager.bots.assembly.job_contract import AssemblyJobContract
from voyager.bots.assembly.writeback import (
    _preserve_existing_pr_context_for_no_changes,
    _verify_pr_head_repo,
)

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


def _fake_contract(*, issue_number: int) -> AssemblyJobContract:
    """Build a minimal contract for no_changes tests."""
    return AssemblyJobContract(
        repository=REPOSITORY,
        issue_number=issue_number,
        issue_url=f"https://github.com/{REPOSITORY}/issues/{issue_number}",
        issue_title="Test",
        issue_body="body",
        branch_name=f"{issue_number}-test",
        base_branch="main",
        task_summary="summary",
        acceptance_criteria=["ac1"],
        forbidden_operations=[],
        verification_commands=[],
        delivery_id="d1",
        requested_at="2026-01-01T00:00:00Z",
        acceptance_criteria_source="section",
        task_summary_source="section",
        extra={},
    )


# ---------------------------------------------------------------------------
# _preserve_existing_pr_context_for_no_changes — no_changes fork PR path
# ---------------------------------------------------------------------------
# These tests verify that duplicate no_changes runs do NOT preserve the
# PR context when the existing PR is from a fork (VOY-1822 Follow-up 5).


def _no_changes_adapter_result() -> dict:
    """Build an adapter_result dict with no_changes status (no commits)."""
    return {"status": "no_changes", "commit_shas": [], "summary": "nothing to do", "details": {}}


@pytest.mark.asyncio
async def test_no_changes_fork_pr_does_not_preserve_context() -> None:
    """Fork PR in no_changes path does NOT set pull_request as updated.

    Regression gate for VOY-1822 Follow-up 5: _preserve_existing_pr_context_for_no_changes
    must verify head repo before preserving context, otherwise a duplicate
    no_changes run overwrites the "skipped_no_changes" state with "updated".
    """
    client = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(
        return_value={
            "number": 1,
            "html_url": "http://pr",
            "head": {"repo": {"full_name": "fork/voyager"}, "sha": "abc"},
            "base": {"repo": {"full_name": REPOSITORY}},
        }
    )
    result: dict = {
        "writeback_failures": [],
        "adapter_result": _no_changes_adapter_result(),
        "pull_request": {"number": None, "url": None, "action": "skipped_no_changes"},
    }
    contract = _fake_contract(issue_number=1)
    await _preserve_existing_pr_context_for_no_changes(client, REPOSITORY, contract, result)
    # pull_request must remain "skipped_no_changes" — NOT "updated"
    assert result["pull_request"]["action"] == "skipped_no_changes"
    # branch key is NOT set when verification fails (context not preserved)
    assert result.get("branch") is None


@pytest.mark.asyncio
async def test_no_changes_fork_pr_records_failure() -> None:
    """Fork PR in no_changes path records verifyPRHeadRepo failure."""
    client = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(
        return_value={
            "number": 1,
            "html_url": "http://pr",
            "head": {"repo": {"full_name": "fork/voyager"}, "sha": "abc"},
            "base": {"repo": {"full_name": REPOSITORY}},
        }
    )
    result: dict = {
        "writeback_failures": [],
        "adapter_result": _no_changes_adapter_result(),
        "pull_request": {"number": None, "url": None, "action": "skipped_no_changes"},
    }
    contract = _fake_contract(issue_number=1)
    await _preserve_existing_pr_context_for_no_changes(client, REPOSITORY, contract, result)
    failures = result["writeback_failures"]
    assert len(failures) == 1
    assert failures[0]["operation"] == "verifyPRHeadRepo"
    assert failures[0]["error_class"] == "ForkHeadRepo"
    assert failures[0]["pr"] == 1


@pytest.mark.asyncio
async def test_no_changes_missing_head_repo_does_not_preserve_context() -> None:
    """Missing head repo metadata in no_changes path does NOT preserve context."""
    client = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(
        return_value={
            "number": 1,
            "html_url": "http://pr",
            "head": {"sha": "abc"},
        }
    )
    result: dict = {
        "writeback_failures": [],
        "adapter_result": _no_changes_adapter_result(),
        "pull_request": {"number": None, "url": None, "action": "skipped_no_changes"},
    }
    contract = _fake_contract(issue_number=1)
    await _preserve_existing_pr_context_for_no_changes(client, REPOSITORY, contract, result)
    assert result["pull_request"]["action"] == "skipped_no_changes"
    assert len(result["writeback_failures"]) == 1
    assert result["writeback_failures"][0]["operation"] == "verifyPRHeadRepo"


@pytest.mark.asyncio
async def test_no_changes_same_repo_preserves_context() -> None:
    """Same-repo PR in no_changes path preserves PR context (no regression)."""
    client = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(
        return_value={
            "number": 42,
            "html_url": "http://pr",
            "head": {"repo": {"full_name": REPOSITORY}, "sha": "abc"},
            "base": {"repo": {"full_name": REPOSITORY}},
        }
    )
    result: dict = {
        "writeback_failures": [],
        "adapter_result": _no_changes_adapter_result(),
        "pull_request": {"number": None, "url": None, "action": "skipped_no_changes"},
    }
    contract = _fake_contract(issue_number=1)
    await _preserve_existing_pr_context_for_no_changes(client, REPOSITORY, contract, result)
    # Same-repo PR must be preserved — pull_request should be "updated"
    assert result["pull_request"]["action"] == "updated"
    assert result["pull_request"]["number"] == 42
    assert result["branch"]["name"] == contract.branch_name
    assert result["writeback_failures"] == []
