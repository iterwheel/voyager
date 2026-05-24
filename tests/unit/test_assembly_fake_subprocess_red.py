"""RED tests for VOY-1821 fake subprocess Assembly backend.

These tests intentionally specify production symbols and behavior that do
not exist yet. They should fail until the VOY-1821 implementation lands.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from voyager.bots.assembly.adapters import AdapterResult
from voyager.bots.assembly.constants import (
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    CODEX_REVIEW_TRIGGER_BODY,
)
from voyager.bots.assembly.job_contract import build_job_contract
from voyager.bots.assembly.writeback import dispatch_assembly_writeback

VALID_SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_VALID_SHA = "89abcdef0123456789abcdef0123456789abcdef"
INVALID_SHA = "refs/heads/main-not-a-commit-sha"
FAKE_ALLOW_ENV = "ASSEMBLY_FAKE_SUBPROCESS_ALLOW"
FAKE_OUTPUT_ENV = "ASSEMBLY_FAKE_SUBPROCESS_OUTPUT"


def _contract():
    return build_job_contract(
        issue={
            "number": 69,
            "title": "[Feature]: Implement fake subprocess backend",
            "body": "## Acceptance Criteria\n\n- [ ] fake backend creates a PR\n",
            "html_url": "https://example/issues/69",
            "labels": [{"name": "blueprint-ready"}, {"name": "stack-type-feature"}],
            "state": "open",
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="69-implement-fake-subprocess-backend",
        delivery_id="delivery-red",
    )


def _route() -> dict[str, Any]:
    contract = _contract().to_dict()
    return {
        "agent": ASSEMBLY_AGENT_SLUG,
        "agent_id": "github-assembly-agent",
        "kind": "assembly_implementation",
        "event": "issue_comment",
        "action": "created",
        "delivery_id": "delivery-red",
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
            "branch_name": contract["branch_name"],
            "issue_labels": ["blueprint-ready", "stack-type-feature"],
            "issue_state": "open",
            "refusal": None,
            "comment_marker": "<!-- iterwheel:assembly-implementation -->",
        },
    }


def _mock_client(*, token: str = "ghs_test_installation_token") -> AsyncMock:
    client = AsyncMock()
    client.get_issue = AsyncMock(
        return_value={
            "number": 69,
            "title": "[Feature]: Implement fake subprocess backend",
            "body": "## Acceptance Criteria\n\n- [ ] fake backend creates a PR\n",
            "html_url": "https://example/issues/69",
            "labels": [{"name": "blueprint-ready"}, {"name": "stack-type-feature"}],
            "state": "open",
        }
    )
    client.installation_token = AsyncMock(return_value=token)
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock(return_value={"object": {"sha": VALID_SHA}})
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
    return client


def _set_fake_output(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any] | str) -> None:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setenv(FAKE_OUTPUT_ENV, raw)


def _assert_not_contains(value: Any, forbidden: str) -> None:
    assert forbidden not in json.dumps(value, default=str, sort_keys=True)


def test_adapter_execution_context_repr_redacts_installation_token(tmp_path) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext

    secret = "ghs_red_repr_secret_123"
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=secret,
    )

    text = repr(context)
    assert secret not in text
    assert "ghs_" not in text
    if "installation_token" in text:
        assert "redact" in text.lower()


def test_adapter_execution_context_safe_dict_redacts_installation_token(tmp_path) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext

    secret = "ghs_red_safe_dict_secret_456"
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=secret,
    )

    safe = context.to_safe_dict()
    assert isinstance(safe, dict)
    _assert_not_contains(safe, secret)
    _assert_not_contains(safe, "ghs_")


def test_select_execution_adapter_wires_fake_subprocess_backend(monkeypatch) -> None:
    from voyager.bots.assembly.adapters import FakeSubprocessAdapter, select_execution_adapter
    from voyager.bots.assembly.constants import ASSEMBLY_BACKEND_FAKE_SUBPROCESS

    assert ASSEMBLY_BACKEND_FAKE_SUBPROCESS == "fake-subprocess"
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_FAKE_SUBPROCESS)
    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")

    adapter = select_execution_adapter()

    assert isinstance(adapter, FakeSubprocessAdapter)
    assert adapter.name == ASSEMBLY_BACKEND_FAKE_SUBPROCESS


@pytest.mark.asyncio
async def test_fake_subprocess_requires_explicit_allow_env(monkeypatch, tmp_path) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext, FakeSubprocessAdapter

    monkeypatch.delenv(FAKE_ALLOW_ENV, raising=False)
    _set_fake_output(
        monkeypatch,
        {"status": "executed", "commit_shas": [VALID_SHA], "summary": "one commit"},
    )
    adapter = FakeSubprocessAdapter()
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=None,
    )

    result = await adapter.execute(_contract(), context)

    assert result.status == "failed"
    assert result.commit_shas == []
    assert FAKE_ALLOW_ENV in result.summary


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["no_changes", "failed"])
async def test_fake_subprocess_allow_env_gate_applies_to_non_executed_statuses(
    monkeypatch,
    tmp_path,
    status: str,
) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext, FakeSubprocessAdapter

    monkeypatch.delenv(FAKE_ALLOW_ENV, raising=False)
    _set_fake_output(
        monkeypatch,
        {"status": status, "summary": f"fake subprocess reported {status}"},
    )
    adapter = FakeSubprocessAdapter()
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=None,
    )

    result = await adapter.execute(_contract(), context)

    assert result.status == "failed"
    assert result.commit_shas == []
    assert FAKE_ALLOW_ENV in result.summary


@pytest.mark.asyncio
async def test_fake_subprocess_execute_with_valid_sha(monkeypatch, tmp_path) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext, FakeSubprocessAdapter

    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(
        monkeypatch,
        {"status": "executed", "commit_shas": [VALID_SHA], "summary": "one commit"},
    )
    adapter = FakeSubprocessAdapter()
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=None,
    )

    result = await adapter.execute(_contract(), context)

    assert result.status == "executed"
    assert result.commit_shas == [VALID_SHA]
    assert "one commit" in result.summary


@pytest.mark.asyncio
async def test_fake_subprocess_no_changes(monkeypatch, tmp_path) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext, FakeSubprocessAdapter

    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(monkeypatch, {"status": "no_changes", "summary": "working tree clean"})
    adapter = FakeSubprocessAdapter()
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=None,
    )

    result = await adapter.execute(_contract(), context)

    assert result.status == "no_changes"
    assert result.commit_shas == []
    assert "clean" in result.summary


@pytest.mark.asyncio
async def test_fake_subprocess_failed_output_discards_commit_shas(monkeypatch, tmp_path) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext, FakeSubprocessAdapter

    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(
        monkeypatch,
        {"status": "failed", "commit_shas": [VALID_SHA], "summary": "fixture failure"},
    )
    adapter = FakeSubprocessAdapter()
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=None,
    )

    result = await adapter.execute(_contract(), context)

    assert result.status == "failed"
    assert result.commit_shas == []
    assert "fixture failure" in result.summary


@pytest.mark.asyncio
async def test_fake_subprocess_malformed_output_fails_without_commits(
    monkeypatch, tmp_path
) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext, FakeSubprocessAdapter

    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(monkeypatch, "{not json")
    adapter = FakeSubprocessAdapter()
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=None,
    )

    result = await adapter.execute(_contract(), context)

    assert result.status == "failed"
    assert result.commit_shas == []
    assert "malformed" in result.summary.lower()


@pytest.mark.asyncio
async def test_fake_subprocess_invalid_sha_fails_safely_without_commit_shas(
    monkeypatch, tmp_path
) -> None:
    from voyager.bots.assembly.adapters import AdapterExecutionContext, FakeSubprocessAdapter

    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(
        monkeypatch,
        {"status": "executed", "commit_shas": [INVALID_SHA], "summary": "bad fixture"},
    )
    adapter = FakeSubprocessAdapter()
    context = AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=60,
        command_path="/usr/local/bin/pi",
        installation_token=None,
    )

    result = await adapter.execute(_contract(), context)

    assert result.status == "failed"
    assert result.commit_shas == []
    _assert_not_contains(result.__dict__, INVALID_SHA)


def test_fake_subprocess_dispatcher_runs_existing_branch_pr_codex_progress_flow(
    monkeypatch,
) -> None:
    from voyager.bots.assembly.constants import ASSEMBLY_BACKEND_FAKE_SUBPROCESS

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_FAKE_SUBPROCESS)
    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(
        monkeypatch,
        {
            "status": "executed",
            "commit_shas": [VALID_SHA, OTHER_VALID_SHA],
            "summary": "two fake commits",
        },
    )
    client = _mock_client()

    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )

    assert result["execution_backend"] == ASSEMBLY_BACKEND_FAKE_SUBPROCESS
    assert result["adapter_result"]["status"] == "executed"
    assert result["adapter_result"]["commit_shas"] == [VALID_SHA, OTHER_VALID_SHA]
    client.create_branch_ref.assert_awaited_once_with(
        ASSEMBLY_AGENT_SLUG,
        "iterwheel/voyager-sandbox",
        "69-implement-fake-subprocess-backend",
        OTHER_VALID_SHA,
    )
    assert result["branch"]["created"] is True
    assert result["pull_request"]["action"] == "opened"
    assert result["pull_request"]["number"] == 1234
    client.create_issue_comment.assert_awaited_once()
    assert client.create_issue_comment.await_args.kwargs["body"] == CODEX_REVIEW_TRIGGER_BODY
    assert client.upsert_issue_comment.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_fake_subprocess_dispatches_serialize_without_duplicate_branch_or_pr(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, "fake-subprocess")
    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(
        monkeypatch,
        {"status": "executed", "commit_shas": [VALID_SHA], "summary": "one fake commit"},
    )

    client = _mock_client()
    branch_created = False
    pr_created = False
    pr_payload = {
        "number": 1234,
        "html_url": "https://example/pr/1234",
        "head": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
        "base": {"repo": {"full_name": "iterwheel/voyager-sandbox"}},
    }

    branch_entered = asyncio.Event()
    branch_release = asyncio.Event()
    branch_create_entries = {"value": 0}
    pr_entered = asyncio.Event()
    pr_release = asyncio.Event()
    pr_create_entries = {"value": 0}

    async def branch_ref_exists(*args, **kwargs):
        return branch_created

    async def create_branch_ref(*args, **kwargs):
        nonlocal branch_created
        branch_create_entries["value"] += 1
        branch_entered.set()
        await branch_release.wait()
        if branch_created:
            raise AssertionError("duplicate branch creation")
        branch_created = True
        return {"object": {"sha": VALID_SHA}}

    async def find_pull_request_by_head(*args, **kwargs):
        return pr_payload if pr_created else None

    async def create_pull_request(*args, **kwargs):
        nonlocal pr_created
        pr_create_entries["value"] += 1
        pr_entered.set()
        await pr_release.wait()
        if pr_created:
            raise AssertionError("duplicate pull request creation")
        pr_created = True
        return pr_payload

    client.branch_ref_exists = AsyncMock(side_effect=branch_ref_exists)
    client.create_branch_ref = AsyncMock(side_effect=create_branch_ref)
    client.find_pull_request_by_head = AsyncMock(side_effect=find_pull_request_by_head)
    client.create_pull_request = AsyncMock(side_effect=create_pull_request)

    task_a = asyncio.create_task(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    task_b = asyncio.create_task(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )
    tasks = (task_a, task_b)

    try:
        await asyncio.wait_for(branch_entered.wait(), timeout=1.0)
        await asyncio.sleep(0.05)
        assert branch_create_entries["value"] == 1

        branch_release.set()
        await asyncio.wait_for(pr_entered.wait(), timeout=1.0)
        await asyncio.sleep(0.05)
        assert pr_create_entries["value"] == 1

        pr_release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)
    finally:
        branch_release.set()
        pr_release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert all(result["adapter_result"]["status"] == "executed" for result in results)
    assert all(result["applied"] is True for result in results)
    assert sorted(result["branch"]["created"] for result in results) == [False, True]
    assert sorted(result["pull_request"]["action"] for result in results) == [
        "opened",
        "updated",
    ]
    assert client.branch_ref_exists.await_count == 2
    assert client.create_branch_ref.await_count == 1
    assert client.find_pull_request_by_head.await_count == 2
    assert client.create_pull_request.await_count == 1
    assert client.update_pull_request.await_count == 1


def test_invalid_fake_sha_dispatcher_fails_without_branch_pr_or_raw_sha_leak(
    monkeypatch,
) -> None:
    from voyager.bots.assembly.constants import ASSEMBLY_BACKEND_FAKE_SUBPROCESS

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_FAKE_SUBPROCESS)
    monkeypatch.setenv(FAKE_ALLOW_ENV, "1")
    _set_fake_output(
        monkeypatch,
        {"status": "executed", "commit_shas": [INVALID_SHA], "summary": "bad fixture"},
    )
    client = _mock_client()

    result = asyncio.run(
        dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
    )

    assert result["execution_backend"] == ASSEMBLY_BACKEND_FAKE_SUBPROCESS
    assert result["adapter_result"]["status"] == "failed"
    assert result["adapter_result"]["commit_shas"] == []
    assert result["branch"] is None
    assert result["pull_request"]["action"] == "skipped_no_changes"
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert client.create_issue_comment.await_count == 0
    assert client.upsert_issue_comment.await_count == 1
    body = client.upsert_issue_comment.await_args.kwargs["body"]
    assert "status: `failed`" in body
    assert INVALID_SHA not in body
    _assert_not_contains(result, INVALID_SHA)


def test_token_requiring_adapter_receives_installation_token_context_without_leaking(
    monkeypatch,
) -> None:
    token = "ghs_dispatch_secret_token_red_789"
    seen_contexts: list[Any] = []

    class _NeedsInstallationTokenAdapter:
        name = "needs-installation-token"
        requires_installation_token = True

        async def execute(self, contract, context=None):
            seen_contexts.append(context)
            if context is None or context.installation_token != token:
                raise AssertionError("missing installation token context")
            return AdapterResult(
                status="executed",
                commit_shas=[VALID_SHA],
                summary="token was available without being rendered",
            )

    monkeypatch.setenv("DRY_RUN", "false")
    client = _mock_client(token=token)

    with patch(
        "voyager.bots.assembly.writeback.select_execution_adapter",
        return_value=_NeedsInstallationTokenAdapter(),
    ):
        result = asyncio.run(
            dispatch_assembly_writeback(client, _route(), repository="iterwheel/voyager-sandbox")
        )

    _assert_not_contains(result, token)
    for call in client.upsert_issue_comment.await_args_list:
        assert token not in call.kwargs["body"]
    for call in client.create_issue_comment.await_args_list:
        assert token not in call.kwargs["body"]

    client.installation_token.assert_awaited_once_with(
        ASSEMBLY_AGENT_SLUG,
        repository="iterwheel/voyager-sandbox",
    )
    assert seen_contexts
    assert seen_contexts[0].installation_token == token
    assert result["adapter_result"]["status"] == "executed"
