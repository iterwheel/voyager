"""Unit tests for the Assembly writeback dispatcher (VOY-1817 Surface 19).

Covers all five rows of the §Gate Corner Table:

  AL- / *                — denied upstream (server-level filter, not here)
  AL+ / DR+ / BE=dry     — adapter runs, no GitHub writes, returns plan
  AL+ / DR+ / BE=pi      — adapter raises NotImplementedError, caught
  AL+ / DR- / BE=dry     — comment-only (skipped_no_changes)
  AL+ / DR- / BE=pi      — progress comment only, branch/PR/codex skipped
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from voyager.bots.assembly.constants import (
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
)
from voyager.bots.assembly.writeback import dispatch_assembly_writeback


def _route(
    *,
    refusal: dict | None = None,
    contract: dict | None = None,
    with_labels: bool = True,
) -> dict:
    if contract is None and refusal is None:
        contract = {
            "repository": "iterwheel/voyager-sandbox",
            "issue_number": 69,
            "issue_url": "https://example/issues/69",
            "issue_title": "[Feature]: Implement Assembly bot MVP",
            "issue_body": "## Acceptance Criteria\n\n- [ ] Do the thing\n",
            "branch_name": "69-implement-assembly-bot-mvp",
            "base_branch": "main",
            "task_summary": "Do the thing",
            "acceptance_criteria": ["Do the thing"],
            "forbidden_operations": ["Merge pull requests"],
            "verification_commands": ["pytest tests/"],
            "delivery_id": "d",
            "requested_at": "2026-05-23T00:00:00+00:00",
            "acceptance_criteria_source": "section",
            "task_summary_source": "section",
        }
    labels = [{"name": "blueprint-ready"}, {"name": "stack-type-feature"}] if with_labels else []
    return {
        "agent": "iterwheel-assembly",
        "agent_id": "github-assembly-agent",
        "kind": "assembly_implementation",
        "event": "issue_comment",
        "action": "created",
        "delivery_id": "delivery-id-xyz",
        "validation": {
            "status": "assembly_ready" if not refusal else "assembly_refused",
            "issue_number": 69,
            "issue_labels": [label["name"] for label in labels],
        },
        "writeback": {
            "dynamic": "assembly_implementation",
            "command": "/assembly",
            "command_flags": {"dry_run": False, "allow_missing_stack": False},
            "contract": contract,
            "branch_name": "69-implement-assembly-bot-mvp",
            "refusal": refusal,
            "comment_marker": "<!-- iterwheel:assembly-implementation -->",
        },
    }


def _mock_client_for_writes() -> Any:
    client = AsyncMock()
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock(return_value={"object": {"sha": "newsha"}})
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(
        return_value={"number": 1234, "html_url": "https://example/pr/1234"}
    )
    client.update_pull_request = AsyncMock(return_value={})
    client.create_issue_comment = AsyncMock(return_value={"id": 999})
    client.upsert_issue_comment = AsyncMock(return_value={"id": 777})
    return client


# ---------------------------------------------------------------------------
# AL+ / DR+ / BE=dry
# ---------------------------------------------------------------------------


def test_dry_run_true_dry_run_backend(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    client = _mock_client_for_writes()
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["adapter_result"]["status"] == "dry_run"
    # No GitHub writes happened.
    assert client.branch_ref_exists.await_count == 0
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert client.create_issue_comment.await_count == 0
    assert client.upsert_issue_comment.await_count == 0
    assert result["pull_request"]["action"] == "dry_run_skipped"


# ---------------------------------------------------------------------------
# AL+ / DR+ / BE=pi  — adapter raises, caught
# ---------------------------------------------------------------------------


def test_dry_run_true_pi_backend_catches_not_implemented(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)
    client = _mock_client_for_writes()
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["adapter_result"]["status"] == "failed"
    assert "deferred" in result["adapter_result"]["summary"]
    failures = result["writeback_failures"]
    assert failures
    assert failures[0]["error_class"] == "NotImplementedError"
    assert client.create_branch_ref.await_count == 0


# ---------------------------------------------------------------------------
# AL+ / DR- / BE=dry  — comment-only (no commits to push)
# ---------------------------------------------------------------------------


def test_dry_run_false_dry_run_backend_comments_only(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    client = _mock_client_for_writes()
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["dry_run"] is False
    assert result["applied"] is True
    assert result["pull_request"]["action"] == "skipped_no_changes"
    # Progress comment upserted on the issue (no PR yet, so no PR-side comment).
    assert client.upsert_issue_comment.await_count == 1
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert result["assembly_comment_id"] == 777


# ---------------------------------------------------------------------------
# AL+ / DR- / BE=pi  — adapter raises, progress comment still upserts
# ---------------------------------------------------------------------------


def test_dry_run_false_pi_backend_progress_comment_runs_anyway(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)
    client = _mock_client_for_writes()
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["applied"] is True
    assert result["adapter_result"]["status"] == "failed"
    assert result["branch"] is None
    assert result["pull_request"]["action"] == "skipped_no_changes"
    # Per D11: progress comment always runs.
    assert client.upsert_issue_comment.await_count == 1
    failures = result["writeback_failures"]
    assert any(f["error_class"] == "NotImplementedError" for f in failures)


# ---------------------------------------------------------------------------
# AL- corner (refusal at server, not dispatcher — tested via refusal payload)
# ---------------------------------------------------------------------------


def test_refusal_route_posts_refusal_comment(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    client = _mock_client_for_writes()
    refusal = {
        "reason": "missing_blueprint_ready_label",
        "missing_labels": ["blueprint-ready"],
        "outside_allow_list": False,
    }
    result = asyncio.run(
        dispatch_assembly_writeback(
            client,
            _route(refusal=refusal, contract=None),
            repository="iterwheel/voyager-sandbox",
        )
    )
    assert result["refusal"] == refusal
    assert client.upsert_issue_comment.await_count == 1
    # No branch / PR / codex writes.
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert client.create_issue_comment.await_count == 0


def test_missing_repository_short_circuits(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    client = _mock_client_for_writes()
    result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=None))
    assert result["applied"] is False
    assert result["refusal"]["reason"] == "missing_repository"
    assert client.upsert_issue_comment.await_count == 0


# ---------------------------------------------------------------------------
# Happy path with commits — exercise full branch -> PR -> codex sequence
# ---------------------------------------------------------------------------


def test_full_path_with_commits_runs_sequence(monkeypatch) -> None:
    """Stub a custom adapter that returns commits; verify the full sequence runs."""
    from voyager.bots.assembly import adapters

    monkeypatch.setenv("DRY_RUN", "false")

    class _CommitAdapter:
        name = "fake-commit-adapter"

        async def execute(self, contract):
            return adapters.AdapterResult(
                status="executed",
                commit_shas=["sha1", "sha2"],
                summary="2 commits applied",
            )

    monkeypatch.setattr(adapters, "select_execution_adapter", lambda backend=None: _CommitAdapter())
    # The writeback module imported the function at module load — re-patch
    # the bound name there too.
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _CommitAdapter()
    )

    client = _mock_client_for_writes()
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["applied"] is True
    assert result["branch"]["created"] is True
    assert result["branch"]["sha"] == "newsha"
    assert result["pull_request"]["number"] == 1234
    assert result["pull_request"]["action"] == "opened"
    assert result["codex_review_comment_id"] == 999
    assert client.create_branch_ref.await_count == 1
    assert client.create_pull_request.await_count == 1
    assert client.create_issue_comment.await_count == 1  # codex trigger
    assert client.upsert_issue_comment.await_count == 2  # issue + PR comments


def test_existing_branch_is_reused_idempotent(monkeypatch) -> None:
    """D11: branch ref check is idempotent — existing branches are not re-created."""
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")

    class _CommitAdapter:
        name = "fake"

        async def execute(self, contract):
            return adapters.AdapterResult(status="executed", commit_shas=["sha1"], summary="")

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _CommitAdapter()
    )
    client = _mock_client_for_writes()
    client.branch_ref_exists = AsyncMock(return_value=True)
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["branch"]["created"] is False
    assert client.create_branch_ref.await_count == 0


def test_existing_pr_is_updated_not_recreated(monkeypatch) -> None:
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")

    class _CommitAdapter:
        name = "fake"

        async def execute(self, contract):
            return adapters.AdapterResult(status="executed", commit_shas=["sha1"], summary="")

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _CommitAdapter()
    )
    client = _mock_client_for_writes()
    client.find_pull_request_by_head = AsyncMock(
        return_value={"number": 555, "html_url": "https://example/pr/555"}
    )
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["pull_request"]["number"] == 555
    assert result["pull_request"]["action"] == "updated"
    assert client.create_pull_request.await_count == 0
    assert client.update_pull_request.await_count == 1


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Reset env between tests so DRY_RUN does not leak."""
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv(ASSEMBLY_EXECUTION_BACKEND_ENV, raising=False)
    return
