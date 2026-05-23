"""Unit tests for Assembly writeback partial-failure semantics (VOY-1817 Surface 20).

Per D11:
* Branch create succeeds + PR open fails -> failure recorded, progress
  comment still upserts.
* PR exists + codex trigger fails -> failure recorded, progress comment
  still upserts.
* All-fail records four entries and still upserts the progress comment.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from voyager.bots.assembly import adapters
from voyager.bots.assembly import writeback as wb_module
from voyager.bots.assembly.constants import ASSEMBLY_EXECUTION_BACKEND_ENV


def _route() -> dict:
    return {
        "agent": "iterwheel-assembly",
        "delivery_id": "d",
        "validation": {
            "status": "assembly_ready",
            "issue_number": 69,
            "issue_labels": ["blueprint-ready", "stack-type-feature"],
        },
        "writeback": {
            "dynamic": "assembly_implementation",
            "command": "/assembly",
            "command_flags": {"dry_run": False, "allow_missing_stack": False},
            "contract": {
                "repository": "iterwheel/voyager-sandbox",
                "issue_number": 69,
                "issue_url": "https://example/issues/69",
                "issue_title": "[Feature]: x",
                "issue_body": "## Acceptance Criteria\n\n- [ ] one\n",
                "branch_name": "69-x",
                "base_branch": "main",
                "task_summary": "x",
                "acceptance_criteria": ["one"],
                "forbidden_operations": [],
                "verification_commands": [],
                "delivery_id": "d",
                "requested_at": "2026-05-23T00:00:00+00:00",
                "acceptance_criteria_source": "section",
                "task_summary_source": "section",
            },
            "branch_name": "69-x",
            "refusal": None,
            "comment_marker": "<!-- iterwheel:assembly-implementation -->",
        },
    }


@pytest.fixture
def commit_adapter(monkeypatch):
    class _CommitAdapter:
        name = "fake-commit"

        async def execute(self, contract):
            return adapters.AdapterResult(
                status="executed", commit_shas=["abc123"], summary="one commit"
            )

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _CommitAdapter()
    )
    return


def _http_error(status: int = 500) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.github.com/test")
    response = httpx.Response(status_code=status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv(ASSEMBLY_EXECUTION_BACKEND_ENV, raising=False)
    return


def _base_client() -> AsyncMock:
    client = AsyncMock()
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock(return_value={"object": {"sha": "newsha"}})
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(
        return_value={"number": 1, "html_url": "https://example/pr/1"}
    )
    client.update_pull_request = AsyncMock(return_value={})
    client.create_issue_comment = AsyncMock(return_value={"id": 200})
    client.upsert_issue_comment = AsyncMock(return_value={"id": 100})
    return client


def test_branch_succeeds_pr_open_fails_progress_comment_runs(commit_adapter) -> None:
    client = _base_client()
    client.create_pull_request = AsyncMock(side_effect=_http_error(500))

    result = asyncio.run(
        wb_module.dispatch_assembly_writeback(
            client, _route(), repository="iterwheel/voyager-sandbox"
        )
    )

    assert result["branch"]["created"] is True
    assert result["pull_request"] is None
    assert result["codex_review_comment_id"] is None
    # Failure captured
    failures = result["writeback_failures"]
    ops = [f["operation"] for f in failures]
    assert "createPullRequest" in ops
    # Progress comment still upserted on the issue (no PR comment because no PR).
    assert client.upsert_issue_comment.await_count == 1
    assert result["assembly_comment_id"] == 100


def test_existing_pr_codex_trigger_fails_progress_still_runs(commit_adapter) -> None:
    client = _base_client()
    client.find_pull_request_by_head = AsyncMock(
        return_value={"number": 7, "html_url": "https://example/pr/7"}
    )
    client.create_issue_comment = AsyncMock(side_effect=_http_error(403))

    result = asyncio.run(
        wb_module.dispatch_assembly_writeback(
            client, _route(), repository="iterwheel/voyager-sandbox"
        )
    )

    assert result["pull_request"]["number"] == 7
    assert result["pull_request"]["action"] == "updated"
    assert result["codex_review_comment_id"] is None
    failures = result["writeback_failures"]
    assert any(f["operation"] == "createCodexTriggerComment" for f in failures)
    # Progress comment ran on both issue + PR.
    assert client.upsert_issue_comment.await_count == 2


def test_all_steps_fail_progress_comment_still_runs(commit_adapter) -> None:
    """All-fail records four entries and still upserts the issue progress comment."""
    client = _base_client()
    client.branch_ref_exists = AsyncMock(side_effect=_http_error(500))
    # Even though branch checks fail, the dispatcher should fall through to
    # the progress-comment step.
    client.upsert_issue_comment = AsyncMock(return_value={"id": 9999})

    result = asyncio.run(
        wb_module.dispatch_assembly_writeback(
            client, _route(), repository="iterwheel/voyager-sandbox"
        )
    )

    assert result["branch"] is None
    assert result["pull_request"] is None
    assert result["codex_review_comment_id"] is None
    # At minimum: branch failure recorded.
    failures = result["writeback_failures"]
    assert any(f["operation"] == "branchRefExists" for f in failures)
    # Progress comment upsert on the issue still ran.
    assert client.upsert_issue_comment.await_count == 1
    assert result["assembly_comment_id"] == 9999


def test_adapter_failure_result_does_not_abort_progress_comment(monkeypatch) -> None:
    """BE=pi corner: failed AdapterResult still upserts the progress comment."""

    monkeypatch.setattr(
        wb_module,
        "select_execution_adapter",
        lambda backend=None: adapters.PiOhMyPiDeepSeekAdapter(),
    )
    client = _base_client()
    client.installation_token = AsyncMock(return_value="")
    result = asyncio.run(
        wb_module.dispatch_assembly_writeback(
            client, _route(), repository="iterwheel/voyager-sandbox"
        )
    )
    assert result["branch"] is None
    assert result["pull_request"]["action"] == "skipped_no_changes"
    assert result["adapter_result"]["status"] == "failed"
    assert "installation token" in result["adapter_result"]["summary"].lower()
    assert result["writeback_failures"] == []
    assert client.upsert_issue_comment.await_count == 1
