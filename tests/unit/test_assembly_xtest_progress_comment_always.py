"""Cross-test for Assembly D11 "progress comment always runs" guarantee.

Four failure paths, all must still upsert the progress comment:
(a) adapter raises generic Exception (not NotImplementedError)
(b) branch create raises httpx.HTTPStatusError
(c) PR open raises httpx.HTTPStatusError
(d) codex trigger raises httpx.HTTPStatusError
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from voyager.bots.assembly.adapters import AdapterResult
from voyager.bots.assembly.writeback import dispatch_assembly_writeback


def _contract_dict() -> dict:
    return {
        "repository": "o/r",
        "issue_number": 1,
        "issue_url": "https://github.com/o/r/issues/1",
        "issue_title": "Test",
        "issue_body": "body",
        "branch_name": "1-test",
        "base_branch": "main",
        "task_summary": "summary",
        "acceptance_criteria": ["ac1"],
        "forbidden_operations": [],
        "verification_commands": [],
        "delivery_id": "d",
        "requested_at": "2026-01-01T00:00:00Z",
        "acceptance_criteria_source": "section",
        "task_summary_source": "section",
        "extra": {},
    }


def _route_with_contract() -> dict:
    wb: dict = {
        "dynamic": "assembly_implementation",
        "refusal": None,
        "contract": _contract_dict(),
        "command_flags": {},
        "comment_marker": "<!-- iterwheel:assembly-implementation -->",
        "issue_labels": ["blueprint-ready", "stack-type-feature"],
        "issue_state": "open",
        "branch_name": "1-test",
    }
    return {
        "agent": "iterwheel-assembly",
        "agent_id": "github-assembly-agent",
        "kind": "assembly_implementation",
        "event": "issue_comment",
        "action": "created",
        "validation": {
            "status": "assembly_ready",
            "conclusion": "success",
            "issue_number": 1,
            "issue_labels": ["blueprint-ready", "stack-type-feature"],
            "issue_state": "open",
            "command": "/assembly",
            "command_flags": {},
        },
        "writeback": wb,
        "delivery_id": "d1",
    }


def _make_executing_adapter():
    """Return a mock adapter that produces one commit so branch/PR steps run."""
    adapter = Mock()
    adapter.name = "dry-run"
    adapter.execute = AsyncMock(
        return_value=AdapterResult(
            status="executed",
            commit_shas=["abc123def456"],
            summary="done",
        )
    )
    return adapter


# ---------------------------------------------------------------------------
# (a) Adapter raises generic Exception → progress comment still upserts
# ---------------------------------------------------------------------------


class TestProgressCommentAdapterGenericException:
    @pytest.mark.asyncio
    async def test_generic_adapter_exception_still_upserts_progress(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        failing_adapter = Mock()
        failing_adapter.name = "failing-adapter"
        failing_adapter.execute = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=failing_adapter,
            ),
        ):
            result = await dispatch_assembly_writeback(
                client, _route_with_contract(), repository="o/r"
            )

        assert client.upsert_issue_comment.await_count >= 1
        assert result["assembly_comment_id"] == 999
        assert len(result["writeback_failures"]) >= 1
        assert result["writeback_failures"][0]["error_class"] == "RuntimeError"
        assert result["writeback_failures"][0]["operation"] == "adapter.execute"

    @pytest.mark.asyncio
    async def test_generic_adapter_exception_no_branch_pr(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 1})

        failing_adapter = Mock()
        failing_adapter.name = "failing"
        failing_adapter.execute = AsyncMock(side_effect=ValueError("broken"))

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=failing_adapter,
            ),
        ):
            await dispatch_assembly_writeback(client, _route_with_contract(), repository="o/r")

        client.create_branch_ref.assert_not_awaited()
        client.create_pull_request.assert_not_awaited()


# ---------------------------------------------------------------------------
# (b) Branch create raises HTTPStatusError → progress still upserts
# ---------------------------------------------------------------------------


class TestProgressCommentBranchCreateFails:
    @pytest.mark.asyncio
    async def test_branch_create_http_error_still_upserts_progress(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=False)
        client.branch_protected = AsyncMock(return_value=False)
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        response = Mock(spec=httpx.Response)
        response.status_code = 422
        client.create_branch_ref = AsyncMock(
            side_effect=httpx.HTTPStatusError("exists", request=Mock(), response=response)
        )

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            result = await dispatch_assembly_writeback(
                client, _route_with_contract(), repository="o/r"
            )

        assert client.upsert_issue_comment.await_count >= 1
        assert result["assembly_comment_id"] == 999
        branch_failures = [
            f
            for f in result["writeback_failures"]
            if "branch" in str(f.get("operation", "")).lower()
            or "Branch" in str(f.get("operation", ""))
        ]
        assert len(branch_failures) >= 1

    @pytest.mark.asyncio
    async def test_branch_create_fails_no_pr_attempted(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=False)
        client.branch_protected = AsyncMock(return_value=False)
        client.upsert_issue_comment = AsyncMock(return_value={"id": 1})

        response = Mock(spec=httpx.Response)
        response.status_code = 422
        client.create_branch_ref = AsyncMock(
            side_effect=httpx.HTTPStatusError("fail", request=Mock(), response=response)
        )

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, _route_with_contract(), repository="o/r")

        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()


# ---------------------------------------------------------------------------
# (c) PR open raises HTTPStatusError → progress still upserts
# ---------------------------------------------------------------------------


class TestProgressCommentPROpenFails:
    @pytest.mark.asyncio
    async def test_pr_open_http_error_still_upserts_progress(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=True)
        client.branch_protected = AsyncMock(return_value=False)
        client.find_pull_request_by_head = AsyncMock(return_value=None)
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        response = Mock(spec=httpx.Response)
        response.status_code = 422
        client.create_pull_request = AsyncMock(
            side_effect=httpx.HTTPStatusError("fail", request=Mock(), response=response)
        )

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            result = await dispatch_assembly_writeback(
                client, _route_with_contract(), repository="o/r"
            )

        assert client.upsert_issue_comment.await_count >= 1
        assert result["assembly_comment_id"] == 999
        pr_failures = [
            f for f in result["writeback_failures"] if "pull" in str(f.get("operation", "")).lower()
        ]
        assert len(pr_failures) >= 1

    @pytest.mark.asyncio
    async def test_pr_open_fails_no_codex_trigger(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=True)
        client.branch_protected = AsyncMock(return_value=False)
        client.find_pull_request_by_head = AsyncMock(return_value=None)
        client.upsert_issue_comment = AsyncMock(return_value={"id": 1})

        response = Mock(spec=httpx.Response)
        response.status_code = 422
        client.create_pull_request = AsyncMock(
            side_effect=httpx.HTTPStatusError("fail", request=Mock(), response=response)
        )

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, _route_with_contract(), repository="o/r")

        client.create_issue_comment.assert_not_awaited()


# ---------------------------------------------------------------------------
# (d) Codex trigger raises HTTPStatusError → progress still upserts
# ---------------------------------------------------------------------------


class TestProgressCommentCodexTriggerFails:
    @pytest.mark.asyncio
    async def test_codex_trigger_http_error_still_upserts_progress(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=True)
        client.branch_protected = AsyncMock(return_value=False)
        client.find_pull_request_by_head = AsyncMock(return_value=None)
        client.create_pull_request = AsyncMock(
            return_value={
                "number": 42,
                "html_url": "http://pr",
                "head": {"repo": {"full_name": "o/r"}},
                "base": {"repo": {"full_name": "o/r"}},
            }
        )
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        response = Mock(spec=httpx.Response)
        response.status_code = 403
        client.create_issue_comment = AsyncMock(
            side_effect=httpx.HTTPStatusError("forbidden", request=Mock(), response=response)
        )

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            result = await dispatch_assembly_writeback(
                client, _route_with_contract(), repository="o/r"
            )

        assert client.upsert_issue_comment.await_count >= 1
        assert result["assembly_comment_id"] == 999
        codex_failures = [
            f
            for f in result["writeback_failures"]
            if "codex" in str(f.get("operation", "")).lower()
            or "Codex" in str(f.get("operation", ""))
        ]
        assert len(codex_failures) >= 1
