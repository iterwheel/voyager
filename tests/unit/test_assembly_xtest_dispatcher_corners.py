"""Cross-test for Assembly writeback dispatcher — Gate Corner Table from property angle.

Re-walks all 5 Gate Corner Table rows (VOY-1817), asserting for each row
exactly which client methods were and were not awaited and the precise
result-dict shape per the Writeback Result Schema.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from voyager.bots.assembly.adapters import AdapterResult, PiOhMyPiDeepSeekAdapter
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
# Row 3: AL+, DR+, BE=pi -> adapter fails without dry-run token
# ---------------------------------------------------------------------------


class TestGateCornerRow3DryRunPiAdapter:
    @pytest.mark.asyncio
    async def test_pi_adapter_fails_without_token_under_dry_run(self) -> None:
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
        assert "installation token" in result["adapter_result"]["summary"].lower()
        assert result["writeback_failures"] == []
        client.installation_token.assert_not_awaited()

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
# Row 5: AL+, DR- (live), BE=pi -> adapter failure, progress comment only
# ---------------------------------------------------------------------------


class TestGateCornerRow5LivePiAdapter:
    @pytest.mark.asyncio
    async def test_pi_adapter_fails_but_progress_comment_still_upserted(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 777})
        client.installation_token = AsyncMock(return_value="")
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
        assert "installation token" in result["adapter_result"]["summary"].lower()
        assert result["assembly_comment_id"] == 777
        assert result["branch"] is None
        # Progress comment was always upserted despite adapter failure

    @pytest.mark.asyncio
    async def test_pi_adapter_no_branch_pr_codex_writes(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
        client.installation_token = AsyncMock(return_value="")
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
    async def test_adapter_failure_result_does_not_record_writeback_failure(self) -> None:
        client = AsyncMock()
        client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
        client.installation_token = AsyncMock(return_value="")
        route = _make_route(contract=_contract_dict())

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=PiOhMyPiDeepSeekAdapter(),
            ),
        ):
            result = await dispatch_assembly_writeback(client, route, repository="o/r")

        assert result["adapter_result"]["status"] == "failed"
        assert result["writeback_failures"] == []
        client.installation_token.assert_awaited_once()


# ---------------------------------------------------------------------------
# CHG-1819 Surface 10 (F3 cross-test) — concurrent-delivery serialization
# from the package-surface angle.
# ---------------------------------------------------------------------------
#
# Independent author / second pair of eyes on Surface 7's invariant.
# Constraints (per CHG-1819 §Surface 10 + §Testing):
#   - Exercise only the public ``dispatch_assembly_writeback`` symbol.
#   - DO NOT reach into ``voyager.bots.assembly.writeback._get_lock`` or the
#     ``_assembly_writeback_locks`` module-level dict.
#   - DO NOT patch private writeback helpers (e.g. ``_ensure_branch``).
#   - Gate concurrency via a PUBLIC client method (``create_branch_ref``):
#     if the per-(repo, branch) lock holds across the branch -> PR -> codex
#     sequence, only one of two concurrent dispatches can reach
#     ``client.create_branch_ref`` while the gate is held.
#
# RED-phase note: this test only exists because Surface 7 already gates the
# private-helper view of the same invariant.  Both must pass after the impl
# worker wires the lock; either failing surfaces a regression.


class TestConcurrentDeliverySerialization:
    @pytest.mark.asyncio
    async def test_xtest_concurrent_deliveries_serialized(self) -> None:
        """Two dispatches for the same (repo, branch) must serialize.

        Observable-from-outside proof: gate ``client.create_branch_ref`` on
        an ``asyncio.Event``.  If the lock holds, the second task cannot
        invoke ``create_branch_ref`` until the first releases the gate.
        """
        entered_event = asyncio.Event()
        released_event = asyncio.Event()
        entered_count = {"value": 0}

        async def gated_create_branch_ref(*args, **kwargs):
            entered_count["value"] += 1
            entered_event.set()
            await released_event.wait()
            return {"object": {"sha": "newsha"}}

        def _make_client():
            client = AsyncMock()
            client.branch_ref_exists = AsyncMock(return_value=False)
            client.create_branch_ref = AsyncMock(side_effect=gated_create_branch_ref)
            client.find_pull_request_by_head = AsyncMock(return_value=None)
            client.create_pull_request = AsyncMock(
                return_value={"number": 1234, "html_url": "https://example/pr/1234"}
            )
            client.update_pull_request = AsyncMock(return_value={})
            client.create_issue_comment = AsyncMock(return_value={"id": 999})
            client.upsert_issue_comment = AsyncMock(return_value={"id": 777})
            return client

        class _CommitAdapter:
            name = "fake-commit-adapter"

            async def execute(self, contract):
                return AdapterResult(status="executed", commit_shas=["sha"], summary="")

        client_a = _make_client()
        client_b = _make_client()
        route_a = _make_route(contract=_contract_dict())
        route_b = _make_route(contract=_contract_dict())

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_CommitAdapter(),
            ),
        ):
            task_a = asyncio.create_task(
                dispatch_assembly_writeback(client_a, route_a, repository="o/r")
            )
            task_b = asyncio.create_task(
                dispatch_assembly_writeback(client_b, route_b, repository="o/r")
            )

            # First task reaches the gated create_branch_ref.
            await entered_event.wait()
            # Give the second task ample scheduler ticks to try to enter.
            await asyncio.sleep(0.05)
            # While task A holds the gate, exactly one create_branch_ref
            # call should be in flight if the per-(repo, branch) lock is
            # serializing the branch -> PR -> codex sequence.  Without the
            # lock, both tasks reach create_branch_ref before either is
            # released and entered_count == 2.
            assert entered_count["value"] == 1, (
                "expected exactly 1 dispatch in create_branch_ref while "
                f"lock held, got {entered_count['value']}"
            )

            released_event.set()
            await asyncio.gather(task_a, task_b)

        # Both tasks must eventually complete the branch step — the lock
        # serializes, it does not drop the duplicate delivery.
        assert entered_count["value"] == 2
        assert client_a.create_branch_ref.await_count == 1
        assert client_b.create_branch_ref.await_count == 1
