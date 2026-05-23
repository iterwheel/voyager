"""Cross-test for Assembly writeback dispatcher — Gate Corner Table from property angle.

Re-walks all 5 Gate Corner Table rows (VOY-1817), asserting for each row
exactly which client methods were and were not awaited and the precise
result-dict shape per the Writeback Result Schema.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from voyager.bots.assembly.adapters import PiOhMyPiDeepSeekAdapter
from voyager.bots.assembly.writeback import dispatch_assembly_writeback


def _make_route(*, refusal: dict | None = None, contract: dict | None = None, **overrides) -> dict:
    """Build a minimal route dict for dispatcher testing."""
    wb: dict = {
        "dynamic": "assembly_implementation",
        "refusal": refusal,
        "contract": contract,
        "command_flags": {},
        "comment_marker": "<!-- iterwheel:assembly-implementation -->",
        "issue_labels": ["blueprint-ready", "stack-type-feature"],
        "issue_state": "open",
        "branch_name": "1-test",
    }
    validation: dict = {
        "status": "assembly_ready",
        "conclusion": "success",
        "issue_number": 1,
        "issue_url": "https://github.com/o/r/issues/1",
        "issue_labels": ["blueprint-ready", "stack-type-feature"],
        "issue_state": "open",
        "command": "/assembly",
        "command_flags": {"dry_run": False, "allow_missing_stack": False},
    }
    route: dict = {
        "agent": "iterwheel-assembly",
        "agent_id": "github-assembly-agent",
        "kind": "assembly_implementation",
        "event": "issue_comment",
        "action": "created",
        "validation": validation,
        "writeback": wb,
        "delivery_id": "delivery-1",
    }
    route.update(overrides)
    return route


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


# ---------------------------------------------------------------------------
# Row 1: AL- (allow-list deny) -> refusal at router, no mutations
# ---------------------------------------------------------------------------


class TestGateCornerRow1AllowListDeny:
    @pytest.mark.asyncio
    async def test_refusal_route_returns_no_mutations(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        route = _make_route(
            refusal={
                "reason": "repository_not_allowed",
                "missing_labels": [],
                "outside_allow_list": True,
            },
            contract=None,
        )

        with patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=True):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert result["applied"] is False
        assert result["refusal"] is not None
        assert result["refusal"]["reason"] == "repository_not_allowed"
        # No branch/PR operations
        assert result["branch"] is None
        assert result["pull_request"] is None

    @pytest.mark.asyncio
    async def test_refusal_route_client_not_called_for_mutations(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        route = _make_route(
            refusal={
                "reason": "repository_not_allowed",
                "missing_labels": [],
                "outside_allow_list": True,
            },
            contract=None,
        )

        with patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False):
            await dispatch_assembly_writeback(client, route, repository="o/r")

        # None of the mutation methods should be called
        client.create_branch_ref.assert_not_awaited()
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()


# ---------------------------------------------------------------------------
# Row 2: AL+, DR+, BE=dry
# ---------------------------------------------------------------------------


class TestGateCornerRow2DryRunDryAdapter:
    @pytest.mark.asyncio
    async def test_dry_run_returns_planned_shape(self) -> None:
        client = AsyncMock()
        route = _make_route(contract=_contract_dict())

        with patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=True):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert result["applied"] is False
        assert result["dry_run"] is True
        assert result["execution_backend"] == "dry-run"
        assert result["adapter_result"] is not None
        assert result["adapter_result"]["status"] == "dry_run"
        assert result["adapter_result"]["commit_shas"] == []
        assert result["pull_request"] == {
            "number": None,
            "url": None,
            "action": "dry_run_skipped",
        }

    @pytest.mark.asyncio
    async def test_dry_run_no_github_writes(self) -> None:
        client = AsyncMock()
        route = _make_route(contract=_contract_dict())

        with patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=True):
            await dispatch_assembly_writeback(client, route, repository="o/r")

        client.create_branch_ref.assert_not_awaited()
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()
        client.upsert_issue_comment.assert_not_awaited()
        client.create_issue_comment.assert_not_awaited()


# ---------------------------------------------------------------------------
# Row 3: AL+, DR+, BE=pi → adapter raises NotImplementedError
# ---------------------------------------------------------------------------


class TestGateCornerRow3DryRunPiAdapter:
    @pytest.mark.asyncio
    async def test_pi_adapter_not_implemented_under_dry_run(self) -> None:
        client = AsyncMock()
        route = _make_route(contract=_contract_dict())

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=True),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=PiOhMyPiDeepSeekAdapter(),
            ),
        ):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert result["applied"] is False
        assert result["dry_run"] is True
        assert result["execution_backend"] == "pi-oh-my-pi-deepseek"
        assert result["adapter_result"] is not None
        assert result["adapter_result"]["status"] == "failed"
        assert "execution backend deferred" in result["adapter_result"]["summary"]
        assert len(result["writeback_failures"]) >= 1
        assert result["writeback_failures"][0]["operation"] == "adapter.execute"

    @pytest.mark.asyncio
    async def test_pi_adapter_dry_run_no_github_writes(self) -> None:
        client = AsyncMock()
        route = _make_route(contract=_contract_dict())

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=True),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=PiOhMyPiDeepSeekAdapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, route, repository="o/r")

        client.create_branch_ref.assert_not_awaited()
        client.create_pull_request.assert_not_awaited()


# ---------------------------------------------------------------------------
# Row 4: AL+, DR- (live), BE=dry -> comment-only, no commits
# ---------------------------------------------------------------------------


class TestGateCornerRow4LiveDryAdapter:
    @pytest.mark.asyncio
    async def test_dry_adapter_no_commits_skips_branch_pr(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})
        route = _make_route(contract=_contract_dict())

        with patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert result["applied"] is True
        assert result["dry_run"] is False
        assert result["branch"] is None
        assert result["pull_request"] == {
            "number": None,
            "url": None,
            "action": "skipped_no_changes",
        }
        # Progress comment was posted
        assert result["assembly_comment_id"] == 999

    @pytest.mark.asyncio
    async def test_dry_adapter_no_branch_or_pr_calls(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})
        route = _make_route(contract=_contract_dict())

        with patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False):
            await dispatch_assembly_writeback(client, route, repository="o/r")

        client.create_branch_ref.assert_not_awaited()
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_progress_comment_called_for_issue(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 42})
        route = _make_route(contract=_contract_dict())

        with patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert result["assembly_comment_id"] == 42
        assert client.upsert_issue_comment.await_count >= 1


# ---------------------------------------------------------------------------
# Row 5: AL+, DR- (live), BE=pi -> adapter raises, progress comment only
# ---------------------------------------------------------------------------


class TestGateCornerRow5LivePiAdapter:
    @pytest.mark.asyncio
    async def test_pi_adapter_fails_but_progress_comment_still_upserted(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 777})
        route = _make_route(contract=_contract_dict())

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=PiOhMyPiDeepSeekAdapter(),
            ),
        ):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert result["applied"] is True
        assert result["adapter_result"]["status"] == "failed"
        assert result["assembly_comment_id"] == 777
        assert result["branch"] is None
        # Progress comment was always upserted despite adapter failure

    @pytest.mark.asyncio
    async def test_pi_adapter_no_branch_pr_codex_writes(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
        route = _make_route(contract=_contract_dict())

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=PiOhMyPiDeepSeekAdapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, route, repository="o/r")

        client.create_branch_ref.assert_not_awaited()
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_writeback_failure_recorded_for_adapter_error(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
        route = _make_route(contract=_contract_dict())

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=PiOhMyPiDeepSeekAdapter(),
            ),
        ):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert len(result["writeback_failures"]) >= 1
        assert result["writeback_failures"][0]["error_class"] == "NotImplementedError"
