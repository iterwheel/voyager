"""Unit tests for the D4 live-issue refetch added in response to the
Codex round-1 P1 review finding on PR #74.

The dispatcher's D4 re-validation must run against the live GitHub issue
snapshot, not the cached webhook payload — otherwise an issue edit
(label removal, close) between webhook ingestion and background dispatch
would let Assembly proceed against a no-longer-eligible issue.

These tests target ``voyager/bots/assembly/writeback.py`` directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx

from voyager.bots.assembly.adapters import AdapterResult
from voyager.bots.assembly.writeback import dispatch_assembly_writeback


def _route_with_cached_blueprint_ready() -> dict:
    """Webhook snapshot says blueprint-ready + stack-type-feature."""
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
    return {
        "agent": "iterwheel-assembly",
        "kind": "assembly_implementation",
        "delivery_id": "delivery-id-xyz",
        "validation": {
            "status": "assembly_ready",
            "issue_number": 69,
            "issue_labels": ["blueprint-ready", "stack-type-feature"],
        },
        "writeback": {
            "dynamic": "assembly_implementation",
            "command": "/assembly",
            "command_flags": {"dry_run": False, "allow_missing_stack": False},
            "contract": contract,
            "branch_name": "69-implement-assembly-bot-mvp",
            "refusal": None,
            "comment_marker": "<!-- iterwheel:assembly-implementation -->",
            "issue_labels": ["blueprint-ready", "stack-type-feature"],
            "issue_state": "open",
        },
    }


def _executing_adapter() -> Mock:
    adapter = Mock()
    adapter.name = "dry-run"
    adapter.execute = AsyncMock(
        return_value=AdapterResult(
            status="executed",
            commit_shas=["headsha"],
            summary="ok",
        )
    )
    return adapter


def test_live_refetch_blocks_when_label_was_removed_between_routing_and_dispatch(
    monkeypatch,
) -> None:
    """If GitHub's live issue lacks blueprint-ready, the dispatcher refuses
    even when the cached webhook snapshot still has the label."""
    monkeypatch.setenv("DRY_RUN", "false")
    client = AsyncMock()
    # Live state: label has been removed since the webhook fired
    client.get_issue = AsyncMock(
        return_value={
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "body": "",
            "html_url": "https://example/issues/69",
            "labels": [{"name": "stack-type-feature"}],  # blueprint-ready GONE
            "state": "open",
        }
    )
    client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock(return_value={"object": {"sha": "newsha"}})
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(return_value={"number": 1, "html_url": "x"})
    client.create_issue_comment = AsyncMock(return_value={"id": 2})

    with patch(
        "voyager.bots.assembly.writeback.select_execution_adapter",
        return_value=_executing_adapter(),
    ):
        result = asyncio.run(
            dispatch_assembly_writeback(
                client, _route_with_cached_blueprint_ready(), repository="iterwheel/voyager-sandbox"
            )
        )

    assert client.get_issue.await_count == 1
    assert result["refusal"] is not None
    assert "blueprint" in result["refusal"]["reason"]
    # No branch/PR/codex writes happened despite the cached snapshot
    # claiming the issue was ready.
    assert client.branch_ref_exists.await_count == 0
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0


def test_live_refetch_blocks_when_issue_was_closed_between_routing_and_dispatch(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    client = AsyncMock()
    client.get_issue = AsyncMock(
        return_value={
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "body": "",
            "html_url": "https://example/issues/69",
            "labels": [
                {"name": "blueprint-ready"},
                {"name": "stack-type-feature"},
            ],
            "state": "closed",
        }
    )
    client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock()

    with patch(
        "voyager.bots.assembly.writeback.select_execution_adapter",
        return_value=_executing_adapter(),
    ):
        result = asyncio.run(
            dispatch_assembly_writeback(
                client, _route_with_cached_blueprint_ready(), repository="iterwheel/voyager-sandbox"
            )
        )

    assert result["refusal"] is not None
    assert result["refusal"]["reason"] == "issue_closed"
    assert client.create_branch_ref.await_count == 0


def test_live_empty_label_list_is_authoritative_not_cached_fallback(
    monkeypatch,
) -> None:
    """Codex round-2 P1: when GitHub returns the issue with zero labels,
    that is authoritative — the dispatcher must NOT fall back to the
    cached webhook label snapshot, which would let Assembly proceed even
    after both gating labels were removed."""
    monkeypatch.setenv("DRY_RUN", "false")
    client = AsyncMock()
    # Live state: both gating labels have been removed
    client.get_issue = AsyncMock(
        return_value={
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "body": "",
            "html_url": "https://example/issues/69",
            "labels": [],  # ← empty live label list
            "state": "open",
        }
    )
    client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock()

    with patch(
        "voyager.bots.assembly.writeback.select_execution_adapter",
        return_value=_executing_adapter(),
    ):
        result = asyncio.run(
            dispatch_assembly_writeback(
                client, _route_with_cached_blueprint_ready(), repository="iterwheel/voyager-sandbox"
            )
        )

    assert client.get_issue.await_count == 1
    assert result["refusal"] is not None
    assert "blueprint" in result["refusal"]["reason"]
    # Cached snapshot would have passed the gate; live snapshot must override
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0


def test_per_command_dry_run_flag_blocks_mutations_when_env_dry_run_false(
    monkeypatch,
) -> None:
    """Codex round-2 P1: ``/assembly --dry-run`` must skip ALL GitHub
    mutations even when DRY_RUN=false. Previously the per-command flag
    was parsed but never gated the mutation path."""
    monkeypatch.setenv("DRY_RUN", "false")
    client = AsyncMock()
    client.get_issue = AsyncMock(
        return_value={
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "body": "",
            "html_url": "https://example/issues/69",
            "labels": [
                {"name": "blueprint-ready"},
                {"name": "stack-type-feature"},
            ],
            "state": "open",
        }
    )
    client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock()
    client.create_issue_comment = AsyncMock(return_value={"id": 2})

    route = _route_with_cached_blueprint_ready()
    route["writeback"]["command_flags"]["dry_run"] = True

    with patch(
        "voyager.bots.assembly.writeback.select_execution_adapter",
        return_value=_executing_adapter(),
    ):
        result = asyncio.run(
            dispatch_assembly_writeback(client, route, repository="iterwheel/voyager-sandbox")
        )

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["pull_request"] == {
        "number": None,
        "url": None,
        "action": "dry_run_skipped",
    }
    # Adapter runs (dry-run records the plan) but no GitHub mutations
    assert client.branch_ref_exists.await_count == 0
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert client.upsert_issue_comment.await_count == 0
    assert client.create_issue_comment.await_count == 0


def test_live_empty_body_is_authoritative_not_cached_fallback(
    monkeypatch,
) -> None:
    """Codex round-4 P2: when GitHub returns the issue with empty body
    (operator cleared the requirements between routing and dispatch), the
    live empty value must be authoritative — the dispatcher must NOT use
    the stale cached body to rebuild the job contract."""
    monkeypatch.setenv("DRY_RUN", "false")
    client = AsyncMock()
    client.get_issue = AsyncMock(
        return_value={
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "body": "",  # ← intentionally cleared
            "html_url": "https://example/issues/69",
            "labels": [
                {"name": "blueprint-ready"},
                {"name": "stack-type-feature"},
            ],
            "state": "open",
        }
    )
    client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock(return_value={"object": {"sha": "newsha"}})
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(return_value={"number": 100, "html_url": "x"})
    client.create_issue_comment = AsyncMock(return_value={"id": 2})

    with patch(
        "voyager.bots.assembly.writeback.select_execution_adapter",
        return_value=_executing_adapter(),
    ):
        result = asyncio.run(
            dispatch_assembly_writeback(
                client, _route_with_cached_blueprint_ready(), repository="iterwheel/voyager-sandbox"
            )
        )

    # Contract rebuilt with the (empty) live body, not the cached body
    contract = result["contract"]
    assert contract is not None
    assert contract["issue_body"] == ""
    # Falls back to title-fallback for both summary and AC since body empty
    assert contract["task_summary_source"] == "title_fallback"
    assert contract["acceptance_criteria_source"] == "title_fallback"


def test_live_refetch_http_error_falls_back_to_cached_and_records_failure(
    monkeypatch,
) -> None:
    """When GitHub refetch raises, dispatcher uses cached snapshot AND records
    the failure in writeback_failures (CHG-1813 surfacing)."""
    monkeypatch.setenv("DRY_RUN", "false")
    client = AsyncMock()
    request = httpx.Request("GET", "https://example/api")
    response = httpx.Response(503, request=request)
    client.get_issue = AsyncMock(
        side_effect=httpx.HTTPStatusError("server", request=request, response=response)
    )
    client.upsert_issue_comment = AsyncMock(return_value={"id": 1})
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock(return_value={"object": {"sha": "newsha"}})
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(return_value={"number": 100, "html_url": "x"})
    client.create_issue_comment = AsyncMock(return_value={"id": 2})

    with patch(
        "voyager.bots.assembly.writeback.select_execution_adapter",
        return_value=_executing_adapter(),
    ):
        result = asyncio.run(
            dispatch_assembly_writeback(
                client, _route_with_cached_blueprint_ready(), repository="iterwheel/voyager-sandbox"
            )
        )

    # Cached snapshot had the labels, so the gate passes and branch/PR run
    assert client.create_branch_ref.await_count == 1
    assert client.create_pull_request.await_count == 1
    # ... but the get_issue failure is captured
    failures = result["writeback_failures"]
    assert any(f["operation"] == "getIssue" for f in failures)
