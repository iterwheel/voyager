"""Cross-test for Assembly D11 idempotency — independent angle.

When branch_ref_exists returns True, create_branch_ref must NOT be called.
When find_pull_request_by_head returns an existing PR, create_pull_request
must NOT be called and update_pull_request MUST be called.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

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
# Branch idempotency: branch_ref_exists True → no create_branch_ref
# ---------------------------------------------------------------------------


class TestBranchIdempotency:
    @pytest.mark.asyncio
    async def test_existing_branch_skips_create(self) -> None:
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
        client.create_issue_comment = AsyncMock(return_value={"id": 100})
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, _route_with_contract(), repository="o/r")

        client.branch_ref_exists.assert_awaited()
        client.create_branch_ref.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_new_branch_calls_create(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=False)
        client.branch_protected = AsyncMock(return_value=False)
        client.create_branch_ref = AsyncMock(
            return_value={"ref": "refs/heads/1-test", "object": {"sha": "abc"}}
        )
        client.find_pull_request_by_head = AsyncMock(return_value=None)
        client.create_pull_request = AsyncMock(
            return_value={
                "number": 42,
                "html_url": "http://pr",
                "head": {"repo": {"full_name": "o/r"}},
                "base": {"repo": {"full_name": "o/r"}},
            }
        )
        client.create_issue_comment = AsyncMock(return_value={"id": 100})
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, _route_with_contract(), repository="o/r")

        client.create_branch_ref.assert_awaited()

    @pytest.mark.asyncio
    async def test_existing_branch_proceeds_to_pr(self) -> None:
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
        client.create_issue_comment = AsyncMock(return_value={"id": 100})
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

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

        client.create_pull_request.assert_awaited()
        assert result["pull_request"]["number"] == 42


# ---------------------------------------------------------------------------
# PR idempotency: existing PR → update, not create
# ---------------------------------------------------------------------------


class TestPRIdempotency:
    @pytest.mark.asyncio
    async def test_existing_pr_skips_create(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=True)
        client.branch_protected = AsyncMock(return_value=False)
        client.find_pull_request_by_head = AsyncMock(
            return_value={
                "number": 55,
                "html_url": "http://existing-pr",
                "head": {"repo": {"full_name": "o/r"}},
                "base": {"repo": {"full_name": "o/r"}},
            }
        )
        client.update_pull_request = AsyncMock(return_value={"number": 55, "html_url": "http://pr"})
        client.create_issue_comment = AsyncMock(return_value={"id": 100})
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, _route_with_contract(), repository="o/r")

        client.create_pull_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_pr_calls_update(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=True)
        client.branch_protected = AsyncMock(return_value=False)
        client.find_pull_request_by_head = AsyncMock(
            return_value={
                "number": 55,
                "html_url": "http://existing-pr",
                "head": {"repo": {"full_name": "o/r"}},
                "base": {"repo": {"full_name": "o/r"}},
            }
        )
        client.update_pull_request = AsyncMock(return_value={"number": 55, "html_url": "http://pr"})
        client.create_issue_comment = AsyncMock(return_value={"id": 100})
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

        with (
            patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
            patch(
                "voyager.bots.assembly.writeback.select_execution_adapter",
                return_value=_make_executing_adapter(),
            ),
        ):
            await dispatch_assembly_writeback(client, _route_with_contract(), repository="o/r")

        client.update_pull_request.assert_awaited()

    @pytest.mark.asyncio
    async def test_existing_pr_result_shows_updated(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=True)
        client.branch_protected = AsyncMock(return_value=False)
        client.find_pull_request_by_head = AsyncMock(
            return_value={
                "number": 55,
                "html_url": "http://existing-pr",
                "head": {"repo": {"full_name": "o/r"}},
                "base": {"repo": {"full_name": "o/r"}},
            }
        )
        client.update_pull_request = AsyncMock(return_value={"number": 55, "html_url": "http://pr"})
        client.create_issue_comment = AsyncMock(return_value={"id": 100})
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

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

        assert result["pull_request"]["number"] == 55
        assert result["pull_request"]["action"] == "updated"

    @pytest.mark.asyncio
    async def test_no_existing_pr_creates_new(self) -> None:
        client = AsyncMock()
        client.branch_ref_exists = AsyncMock(return_value=True)
        client.branch_protected = AsyncMock(return_value=False)
        client.find_pull_request_by_head = AsyncMock(return_value=None)
        client.create_pull_request = AsyncMock(
            return_value={
                "number": 42,
                "html_url": "http://new-pr",
                "head": {"repo": {"full_name": "o/r"}},
                "base": {"repo": {"full_name": "o/r"}},
            }
        )
        client.create_issue_comment = AsyncMock(return_value={"id": 100})
        client.upsert_issue_comment = AsyncMock(return_value={"id": 999})

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

        client.create_pull_request.assert_awaited()
        client.update_pull_request.assert_not_awaited()
        assert result["pull_request"]["number"] == 42
        assert result["pull_request"]["action"] == "opened"
