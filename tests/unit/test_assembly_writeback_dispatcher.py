"""Unit tests for the Assembly writeback dispatcher (VOY-1817 Surface 19).

Covers all five rows of the §Gate Corner Table:

  AL- / *                — denied upstream (server-level filter, not here)
  AL+ / DR+ / BE=dry     — adapter runs, no GitHub writes, returns plan
  AL+ / DR+ / BE=pi      — adapter fails without dry-run token, no GitHub writes
  AL+ / DR- / BE=dry     — comment-only (skipped_no_changes)
  AL+ / DR- / BE=pi      — adapter failure surfaced, progress comment still upserts
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from voyager.bots.assembly.audit import (
    AssemblySessionMetadata,
    find_audit_manifest,
    is_audit_id,
    load_audit_manifest,
    load_session_metadata,
    session_metadata_path,
    write_session_metadata,
)
from voyager.bots.assembly.constants import (
    ASSEMBLY_AUDIT_DIR_ENV,
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
            "command_flags": {"dry_run": False, "allow_missing_stack": False, "resume": False},
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
        return_value={
            "number": 1234,
            "html_url": "https://example/pr/1234",
            "head": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
            "base": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
        }
    )
    client.update_pull_request = AsyncMock(return_value={})
    client.create_issue_comment = AsyncMock(return_value={"id": 999})
    client.upsert_issue_comment = AsyncMock(return_value={"id": 777})
    client.installation_token = AsyncMock(return_value="")
    return client


def _existing_same_repo_pr(*, number: int = 1234, sha: str = "a" * 40) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://example/pr/{number}",
        "head": {
            "sha": sha,
            "repo": {"full_name": "iterwheel/voyager-sandbox"},
        },
        "base": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
    }


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
# AL+ / DR+ / BE=pi  — adapter fails without dry-run token, no writes
# ---------------------------------------------------------------------------


def test_dry_run_true_pi_backend_returns_failed_without_writes(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)
    client = _mock_client_for_writes()
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["adapter_result"]["status"] == "failed"
    assert "installation token" in result["adapter_result"]["summary"].lower()
    assert result["writeback_failures"] == []
    client.installation_token.assert_not_awaited()
    assert client.create_branch_ref.await_count == 0


# ---------------------------------------------------------------------------
# AL+ / DR- / BE=dry  — comment-only (no commits to push)
# ---------------------------------------------------------------------------


def test_dry_run_false_dry_run_backend_comments_only(monkeypatch, tmp_path) -> None:
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
    assert is_audit_id(result["audit_id"])
    manifest_path = find_audit_manifest(result["audit_id"], root=tmp_path / "audit")
    assert manifest_path is not None
    manifest = load_audit_manifest(manifest_path)
    assert manifest.repository == "iterwheel/voyager-sandbox"
    assert manifest.issue_number == 69
    assert manifest.adapter_status == "dry_run"
    body = client.upsert_issue_comment.await_args.kwargs["body"]
    assert f"Audit ID `{result['audit_id']}`" in body
    assert "rules/VOY-1823-SOP-Assembly-OMP-Audit-Lookup.md" in body


# ---------------------------------------------------------------------------
# AL+ / DR- / BE=pi  — adapter failure surfaced, progress comment still upserts
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
    assert "installation token" in result["adapter_result"]["summary"].lower()
    assert result["branch"] is None
    assert result["pull_request"]["action"] == "skipped_no_changes"
    # Per D11: progress comment always runs.
    assert client.upsert_issue_comment.await_count == 1
    client.installation_token.assert_awaited_once()
    assert result["writeback_failures"] == []


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


def test_full_path_with_commits_runs_sequence(monkeypatch, tmp_path) -> None:
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
    manifest_path = find_audit_manifest(result["audit_id"], root=tmp_path / "audit")
    assert manifest_path is not None
    manifest = load_audit_manifest(manifest_path)
    assert manifest.pr_number == 1234
    assert manifest.branch_name == "69-implement-assembly-bot-mvp"
    assert manifest.commit_shas == ("sha1", "sha2")
    issue_body = client.upsert_issue_comment.call_args_list[-2].kwargs["body"]
    pr_body = client.upsert_issue_comment.call_args_list[-1].kwargs["body"]
    assert f"Audit ID `{result['audit_id']}`" in issue_body
    assert f"Audit ID `{result['audit_id']}`" in pr_body


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
        return_value={
            "number": 555,
            "html_url": "https://example/pr/555",
            "head": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
            "base": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
        }
    )
    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    assert result["pull_request"]["number"] == 555
    assert result["pull_request"]["action"] == "updated"
    assert client.create_pull_request.await_count == 0
    assert client.update_pull_request.await_count == 1


def test_duplicate_no_changes_preserves_existing_pr_progress_context(monkeypatch) -> None:
    """Regression for #85: duplicate no_changes must not downgrade progress.

    The first dispatch creates a PR. The second dispatch targets the same
    branch and returns no_changes, so the issue/PR comments should preserve
    the existing PR context and keep the cumulative progress status applied.
    """
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")

    class _SequentialAdapter:
        name = "fake"

        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, contract):
            self.calls += 1
            if self.calls == 1:
                return adapters.AdapterResult(
                    status="executed",
                    commit_shas=["a" * 40],
                    summary="created branch changes",
                )
            return adapters.AdapterResult(
                status="no_changes",
                commit_shas=[],
                summary="duplicate run found no repository changes",
            )

    adapter = _SequentialAdapter()
    monkeypatch.setattr(wb_module, "select_execution_adapter", lambda backend=None: adapter)

    client = _mock_client_for_writes()
    client.find_pull_request_by_head = AsyncMock(
        side_effect=[
            None,
            {
                "number": 1234,
                "html_url": "https://example/pr/1234",
                "head": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
                "base": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
            },
        ]
    )

    first = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    second = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )

    assert first["pull_request"]["action"] == "opened"
    assert second["adapter_result"]["status"] == "no_changes"
    assert second["branch"]["name"] == "69-implement-assembly-bot-mvp"
    assert second["pull_request"] == {
        "number": 1234,
        "url": "https://example/pr/1234",
        "action": "updated",
    }
    assert client.create_pull_request.await_count == 1
    assert client.create_issue_comment.await_count == 1  # no duplicate Codex trigger
    assert client.upsert_issue_comment.await_count == 4  # issue + PR for both runs

    issue_body = client.upsert_issue_comment.call_args_list[-2].kwargs["body"]
    pr_body = client.upsert_issue_comment.call_args_list[-1].kwargs["body"]
    assert "Assembly acknowledgement — status: `applied`" in issue_body
    assert "Assembly progress — status: `applied`" in pr_body
    assert "- Branch: `69-implement-assembly-bot-mvp`" in issue_body
    assert "- Pull request: #1234 (updated)" in issue_body
    assert "- Adapter: `no_changes`" in issue_body
    assert "status: `no_changes`" not in issue_body


def test_resume_request_uses_compatible_stored_session(monkeypatch, tmp_path) -> None:
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")
    route = _route()
    route["writeback"]["command_flags"]["resume"] = True
    session_id = "/private/session.jsonl"
    previous_sha = "a" * 40
    new_sha = "b" * 40

    write_session_metadata(
        AssemblySessionMetadata(
            repository="iterwheel/voyager-sandbox",
            issue_number=69,
            branch_name="69-implement-assembly-bot-mvp",
            pr_number=1234,
            head_sha=previous_sha,
            backend_name="resume-capable",
            session_id=session_id,
        )
    )

    class _ResumeAdapter:
        name = "resume-capable"
        supports_resume = True

        async def execute(self, contract, context):
            assert context.session_mode == "resumed"
            assert context.resume_requested is True
            assert context.resume_session_id == session_id
            return adapters.AdapterResult(
                status="executed",
                commit_shas=[new_sha],
                summary="resumed session and committed changes",
                details={"session_id": session_id},
            )

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _ResumeAdapter()
    )

    client = _mock_client_for_writes()
    client.find_pull_request_by_head = AsyncMock(
        side_effect=[
            _existing_same_repo_pr(number=1234, sha=previous_sha),
            _existing_same_repo_pr(number=1234, sha=previous_sha),
        ]
    )
    client.branch_ref_exists = AsyncMock(return_value=True)

    result = asyncio.run(
        dispatch_assembly_writeback(client, route, repository="iterwheel/voyager-sandbox")
    )

    assert result["session"]["mode"] == "resumed"
    assert result["session"]["expected_head_sha"] == previous_sha
    assert result["branch"]["sha"] == new_sha
    issue_body = client.upsert_issue_comment.call_args_list[-2].kwargs["body"]
    assert "- Session: `resumed`" in issue_body
    assert session_id not in issue_body

    manifest_path = find_audit_manifest(result["audit_id"], root=tmp_path / "audit")
    assert manifest_path is not None
    manifest = load_audit_manifest(manifest_path)
    assert manifest.session_mode == "resumed"
    assert manifest.session_id == session_id

    stored_path = session_metadata_path(
        repository="iterwheel/voyager-sandbox",
        issue_number=69,
        branch_name="69-implement-assembly-bot-mvp",
        pr_number=1234,
        root=tmp_path / "audit",
    )
    stored = load_session_metadata(stored_path)
    assert stored.head_sha == new_sha
    assert stored.session_id == session_id


def test_resume_resolution_runs_under_branch_lock(monkeypatch) -> None:
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")
    route = _route()
    route["writeback"]["command_flags"]["resume"] = True

    class _NoChangesAdapter:
        name = "resume-capable"
        supports_resume = True

        async def execute(self, contract, context):
            return adapters.AdapterResult(
                status="no_changes",
                commit_shas=[],
                summary="no changes",
            )

    async def _assert_locked_resolve(**kwargs):
        repository = kwargs["repository"]
        contract = kwargs["contract"]
        assert wb_module._get_lock(repository, contract.branch_name).locked()
        return {
            "requested": True,
            "mode": "resume_fallback",
            "fallback_reason": "test fallback",
            "pr_number": None,
            "expected_head_sha": None,
        }

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _NoChangesAdapter()
    )
    monkeypatch.setattr(wb_module, "_resolve_session", _assert_locked_resolve)

    client = _mock_client_for_writes()
    result = asyncio.run(
        dispatch_assembly_writeback(client, route, repository="iterwheel/voyager-sandbox")
    )

    assert result["session"]["mode"] == "resume_fallback"


def test_resume_request_falls_back_when_stored_head_is_stale(monkeypatch) -> None:
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")
    route = _route()
    route["writeback"]["command_flags"]["resume"] = True
    session_id = "/private/stale-session.jsonl"

    write_session_metadata(
        AssemblySessionMetadata(
            repository="iterwheel/voyager-sandbox",
            issue_number=69,
            branch_name="69-implement-assembly-bot-mvp",
            pr_number=1234,
            head_sha="a" * 40,
            backend_name="resume-capable",
            session_id=session_id,
        )
    )

    class _ResumeAdapter:
        name = "resume-capable"
        supports_resume = True

        async def execute(self, contract, context):
            assert context.session_mode == "resume_fallback"
            assert context.resume_session_id is None
            return adapters.AdapterResult(
                status="no_changes",
                commit_shas=[],
                summary="fresh fallback completed without changes",
            )

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _ResumeAdapter()
    )
    client = _mock_client_for_writes()
    client.find_pull_request_by_head = AsyncMock(
        side_effect=[
            _existing_same_repo_pr(number=1234, sha="c" * 40),
            _existing_same_repo_pr(number=1234, sha="c" * 40),
        ]
    )

    result = asyncio.run(
        dispatch_assembly_writeback(client, route, repository="iterwheel/voyager-sandbox")
    )

    assert result["session"]["mode"] == "resume_fallback"
    assert "head" in result["session"]["fallback_reason"]
    issue_body = client.upsert_issue_comment.call_args_list[-2].kwargs["body"]
    assert "- Session: `resume_fallback`" in issue_body
    assert "stored session metadata mismatch: head" in issue_body
    assert session_id not in issue_body


def test_resume_request_falls_back_when_backend_does_not_support_resume(monkeypatch) -> None:
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")
    route = _route()
    route["writeback"]["command_flags"]["resume"] = True

    class _FreshOnlyAdapter:
        name = "fresh-only"
        supports_resume = False

        async def execute(self, contract, context):
            assert context.session_mode == "resume_fallback"
            assert context.resume_requested is True
            return adapters.AdapterResult(
                status="no_changes",
                commit_shas=[],
                summary="backend ran fresh",
            )

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _FreshOnlyAdapter()
    )
    client = _mock_client_for_writes()

    result = asyncio.run(
        dispatch_assembly_writeback(client, route, repository="iterwheel/voyager-sandbox")
    )

    assert result["session"]["mode"] == "resume_fallback"
    assert result["session"]["fallback_reason"] == "backend `fresh-only` does not support resume"
    body = client.upsert_issue_comment.await_args.kwargs["body"]
    assert "- Session: `resume_fallback`" in body
    assert "backend `fresh-only` does not support resume" in body


def test_first_run_no_changes_stays_visible_without_existing_pr(monkeypatch) -> None:
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")

    class _NoChangesAdapter:
        name = "fake"

        async def execute(self, contract):
            return adapters.AdapterResult(
                status="no_changes",
                commit_shas=[],
                summary="no files changed",
            )

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _NoChangesAdapter()
    )
    client = _mock_client_for_writes()

    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )

    assert result["branch"] is None
    assert result["pull_request"] == {
        "number": None,
        "url": None,
        "action": "skipped_no_changes",
    }
    assert client.create_pull_request.await_count == 0
    assert client.upsert_issue_comment.await_count == 1
    body = client.upsert_issue_comment.await_args.kwargs["body"]
    assert "Assembly acknowledgement — status: `no_changes`" in body
    assert "- Branch: `pending`" in body
    assert "- Pull request: skipped_no_changes" in body


# ---------------------------------------------------------------------------
# VOY-1818 Surface 9 — actor-gate refusal handling
# ---------------------------------------------------------------------------


def test_unauthorized_actor_refusal_dry_run_false_upserts_comment(monkeypatch) -> None:
    """DRY_RUN=false + actor-gate refusal -> upsert_issue_comment with the
    unauthorized_actor body; no branch / PR / codex calls.
    """
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    client = _mock_client_for_writes()
    refusal = {
        "reason": "unauthorized_actor",
        "missing_labels": [],
        "outside_allow_list": False,
        "actor_login": "drive-by",
        "actor_association": "CONTRIBUTOR",
    }
    result = asyncio.run(
        dispatch_assembly_writeback(
            client,
            _route(refusal=refusal, contract=None),
            repository="iterwheel/voyager-sandbox",
        )
    )
    assert result["refusal"]["reason"] == "unauthorized_actor"
    assert result["refusal"]["actor_login"] == "drive-by"
    assert result["refusal"]["actor_association"] == "CONTRIBUTOR"
    # Exactly one comment upsert; no branch / PR / codex writes.
    assert client.upsert_issue_comment.await_count == 1
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert client.create_issue_comment.await_count == 0
    # The body that was passed to upsert must contain the unauthorized_actor
    # reason and the actor's own identity (per D12 — actor's own login is OK).
    call = client.upsert_issue_comment.await_args
    body = call.kwargs.get("body") or (call.args[-1] if call.args else "")
    assert "unauthorized_actor" in body
    assert "drive-by" in body
    # D12 — refusal body MUST NOT echo any operator-set list.  In particular
    # it must not enumerate the default-trusted-association set.
    assert "OWNER MEMBER COLLABORATOR" not in body
    assert "OWNER, MEMBER, COLLABORATOR" not in body
    assert "frankyxhl" not in body


def test_unauthorized_actor_refusal_dry_run_true_skips_comment(monkeypatch) -> None:
    """DRY_RUN=true + actor-gate refusal -> no upsert_issue_comment call.

    The dispatcher already short-circuits dry-run refusals via
    _post_refusal_comment (writeback.py:374). The refusal still appears in
    the returned result dict so the writeback ring captures it.
    """
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    client = _mock_client_for_writes()
    refusal = {
        "reason": "unauthorized_actor",
        "missing_labels": [],
        "outside_allow_list": False,
        "actor_login": "drive-by",
        "actor_association": "NONE",
    }
    result = asyncio.run(
        dispatch_assembly_writeback(
            client,
            _route(refusal=refusal, contract=None),
            repository="iterwheel/voyager-sandbox",
        )
    )
    assert result["dry_run"] is True
    assert result["refusal"]["reason"] == "unauthorized_actor"
    assert result["refusal"]["actor_login"] == "drive-by"
    # No GitHub mutations under dry-run.
    assert client.upsert_issue_comment.await_count == 0
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert client.create_issue_comment.await_count == 0


def test_non_actor_refusal_does_not_carry_actor_keys(monkeypatch) -> None:
    """Negative assertion (Surface 9 case 3) — when a non-actor refusal
    reaches the dispatcher, the refusal payload passed to the comment
    renderer does NOT carry actor_login / actor_association.

    Regression guard for the Surface 5 renderer fork: the renderer branches
    on reason == "unauthorized_actor"; if a non-actor refusal carried actor
    keys, the renderer would silently misroute.
    """
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    client = _mock_client_for_writes()
    refusal = {
        "reason": "pr_not_issue",
        "missing_labels": [],
        "outside_allow_list": False,
    }
    result = asyncio.run(
        dispatch_assembly_writeback(
            client,
            _route(refusal=refusal, contract=None),
            repository="iterwheel/voyager-sandbox",
        )
    )
    assert result["refusal"]["reason"] == "pr_not_issue"
    assert "actor_login" not in result["refusal"]
    assert "actor_association" not in result["refusal"]
    # Comment still upserted.
    assert client.upsert_issue_comment.await_count == 1


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch, tmp_path):
    """Reset env between tests so DRY_RUN does not leak."""
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv(ASSEMBLY_EXECUTION_BACKEND_ENV, raising=False)
    monkeypatch.setenv(ASSEMBLY_AUDIT_DIR_ENV, str(tmp_path / "audit"))
    return


# ---------------------------------------------------------------------------
# CHG-1819 Surface 7 (F3) — per-(repo, branch) writeback serialization
# ---------------------------------------------------------------------------
#
# Two `dispatch_assembly_writeback` tasks for the same (repository, branch_name)
# tuple must serialize across the branch -> PR -> codex sequence so that
# duplicate webhook deliveries do not race on `create_branch_ref` (which
# returns 422 "Reference already exists" on the second concurrent caller).
#
# Determinism: we use `asyncio.Event` gating, not sleep + call-order list —
# the latter is flaky on slow CI when both coroutines schedule into the same
# tick. The pattern is documented in CHG-1819 Surface 7 verbatim.
#
# Expected RED phase (before the impl worker wraps the branch/PR/codex
# sequence in `async with _get_lock(...)`): both tasks reach the patched
# `_ensure_branch` before either is released, so `len(seen) == 2` and the
# `assert len(seen) == 1` line fails with `AssertionError: expected 1, got 2`.


def _route_for_concurrency(*, branch_name: str = "69-implement-assembly-bot-mvp") -> dict:
    """Build a route dict whose contract + writeback both target ``branch_name``.

    The branch_name appears in two places: ``writeback["branch_name"]`` (read
    by the dispatcher to skip the make_branch_name fallback) and inside the
    nested contract dict so that the rebuilt-by-dispatcher contract keeps
    the same value (the dispatcher prefers the writeback-side branch_name
    when present).
    """
    route = _route()
    route["writeback"]["branch_name"] = branch_name
    contract = route["writeback"]["contract"] or {}
    contract["branch_name"] = branch_name
    route["writeback"]["contract"] = contract
    return route


def test_concurrent_deliveries_are_serialized(monkeypatch) -> None:
    """F3: two deliveries for the same (repo, branch) must serialize.

    Patches the `_ensure_branch` symbol bound inside the writeback module
    with a fake that gates on an `asyncio.Event`; if the dispatcher holds
    the per-(repo, branch) lock across the branch -> PR -> codex sequence,
    only one task can enter `_ensure_branch` while `released_event` is unset.
    """
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")

    class _CommitAdapter:
        name = "fake-commit-adapter"

        async def execute(self, contract):
            return adapters.AdapterResult(status="executed", commit_shas=["sha-only"], summary="")

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _CommitAdapter()
    )

    entered_event = asyncio.Event()
    released_event = asyncio.Event()
    seen: list[str] = []

    original_ensure_branch = wb_module._ensure_branch

    async def fake_ensure_branch(client, repository, contract, head_sha, result):
        seen.append("entered")
        entered_event.set()
        await released_event.wait()
        # Delegate to the real branch step so the dispatcher reaches the PR
        # and progress-comment steps deterministically — the lock contract is
        # "hold across the whole branch -> PR -> codex sequence", so the
        # test must exercise the full sequence, not just the entry point.
        return await original_ensure_branch(client, repository, contract, head_sha, result)

    monkeypatch.setattr(wb_module, "_ensure_branch", fake_ensure_branch)

    async def driver() -> tuple[int, int]:
        client_a = _mock_client_for_writes()
        client_b = _mock_client_for_writes()
        route_a = _route_for_concurrency()
        route_b = _route_for_concurrency()
        task_a = asyncio.create_task(
            dispatch_assembly_writeback(client_a, route_a, repository="iterwheel/voyager-sandbox")
        )
        task_b = asyncio.create_task(
            dispatch_assembly_writeback(client_b, route_b, repository="iterwheel/voyager-sandbox")
        )
        # Wait for the FIRST task to reach the fake branch step.
        await entered_event.wait()
        # Give the second task ample scheduler ticks to try to enter — if it
        # is not blocked by the lock, it will append "entered" to `seen`.
        # 50 ms is much longer than realistic asyncio scheduler latency on
        # CI; the matching CHG-1819 Surface 7 spec also uses 0.05.
        await asyncio.sleep(0.05)
        first_count = len(seen)
        released_event.set()
        await asyncio.gather(task_a, task_b)
        return first_count, len(seen)

    first_count, final_count = asyncio.run(driver())
    # While the first task held the lock, only it should have entered the
    # branch step.  If the lock is missing, both tasks enter before either
    # releases and `first_count == 2` (RED-phase failure).
    assert first_count == 1, (
        f"expected exactly 1 task in branch step while lock held, got {first_count}"
    )
    # Both tasks must eventually run — the lock serializes them, it does not
    # drop the second delivery.
    assert final_count == 2


def test_distinct_branches_are_parallel(monkeypatch) -> None:
    """F3 / D5: two deliveries for the same repo but DIFFERENT branch_name
    must NOT serialize — the lock is keyed on (repo, branch), and a
    different branch is a different GitHub-side resource.
    """
    from voyager.bots.assembly import adapters
    from voyager.bots.assembly import writeback as wb_module

    monkeypatch.setenv("DRY_RUN", "false")

    class _CommitAdapter:
        name = "fake-commit-adapter"

        async def execute(self, contract):
            return adapters.AdapterResult(status="executed", commit_shas=["sha-only"], summary="")

    monkeypatch.setattr(
        wb_module, "select_execution_adapter", lambda backend=None: _CommitAdapter()
    )

    entered_a = asyncio.Event()
    entered_b = asyncio.Event()
    released_event = asyncio.Event()
    original_ensure_branch = wb_module._ensure_branch

    async def fake_ensure_branch(client, repository, contract, head_sha, result):
        # Distinguish callers by branch_name — the contract carries it.
        if contract.branch_name.endswith("-alpha"):
            entered_a.set()
        else:
            entered_b.set()
        await released_event.wait()
        return await original_ensure_branch(client, repository, contract, head_sha, result)

    monkeypatch.setattr(wb_module, "_ensure_branch", fake_ensure_branch)

    async def driver() -> None:
        client_a = _mock_client_for_writes()
        client_b = _mock_client_for_writes()
        route_a = _route_for_concurrency(branch_name="69-implement-alpha")
        route_b = _route_for_concurrency(branch_name="70-implement-beta")
        task_a = asyncio.create_task(
            dispatch_assembly_writeback(client_a, route_a, repository="iterwheel/voyager-sandbox")
        )
        task_b = asyncio.create_task(
            dispatch_assembly_writeback(client_b, route_b, repository="iterwheel/voyager-sandbox")
        )
        # Both events should fire before either task is released.  Wait at
        # most ~1s for both events to be set (much longer than realistic
        # scheduler latency under uncontended `asyncio.Lock`).
        await asyncio.wait_for(
            asyncio.gather(entered_a.wait(), entered_b.wait()),
            timeout=1.0,
        )
        released_event.set()
        await asyncio.gather(task_a, task_b)

    # If the implementation accidentally serializes across distinct
    # (repo, branch) keys, the second `entered_*` event never fires and
    # `asyncio.wait_for` raises TimeoutError.
    asyncio.run(driver())


# ---------------------------------------------------------------------------
# CHG-1819 Surface 8 (F2, part b) — dispatcher does not read backend
# from command_flags.  Structural source-inspection gate.
# ---------------------------------------------------------------------------


def test_dispatcher_does_not_read_backend_from_command_flags() -> None:
    """F2: assert the dispatcher source code never reads ``backend`` from
    ``command_flags`` (or any other attribute-style access).

    The dispatcher's comment block legitimately mentions ``backend`` in
    prose; strip comment-only lines before checking for attribute/subscript
    access patterns. This guards against the dead lookup re-appearing in a
    future refactor.
    """
    import inspect

    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    source = inspect.getsource(dispatch_assembly_writeback)
    # Drop comment-only lines (first non-whitespace char is `#`).  We keep
    # docstring text — the docstring on `dispatch_assembly_writeback` does
    # not mention `backend`, so leaving it in does not produce false
    # positives, and dropping it would over-fit the test.
    code_lines = [line for line in source.splitlines() if line.lstrip()[:1] != "#"]
    code_text = "\n".join(code_lines)
    # Both attribute and subscript styles must be absent.
    assert 'command_flags.get("backend")' not in code_text
    assert "command_flags['backend']" not in code_text
    assert 'command_flags["backend"]' not in code_text
