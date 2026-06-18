"""Unit tests for Assembly circuit breaker (issue #157).

Covers:
- ``_read_current_fix_round`` — parsing the round count from labels
- ``_max_fix_rounds_threshold`` — env > config > default precedence
- ``_apply_circuit_breaker`` — label + comment idempotency
- Full dispatch flow: simulating threshold+1 rounds halts the loop
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from voyager.bots.assembly.constants import (
    ASSEMBLY_COMMENT_MARKER,
    ASSEMBLY_FIX_ROUND_LABEL_PREFIX,
    ASSEMBLY_MAX_FIX_ROUNDS_ENV,
    LOOP_CIRCUIT_BROKEN_LABEL,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REPO = "iterwheel/voyager-sandbox"
_ISSUE_NUMBER = 69
_CIRCUIT_BREAKER_MARKER = "<!-- iterwheel:assembly-circuit-breaker -->"


def _mock_client(*, get_issue_labels: list[str] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.get_issue = AsyncMock(
        return_value={
            "number": _ISSUE_NUMBER,
            "title": "[Task]: Circuit breaker — cap automated fix rounds per PR",
            "labels": [
                {"name": label, "id": 1}
                for label in (get_issue_labels or ["blueprint-ready", "stack-type-feature"])
            ],
            "state": "open",
        }
    )
    client.branch_ref_exists = AsyncMock(return_value=True)
    client.find_pull_request_by_head = AsyncMock(
        return_value={
            "number": 1234,
            "html_url": "https://example/pr/1234",
            "head": {"repo": {"full_name": _REPO}, "sha": "approvedsha"},
            "base": {"repo": {"full_name": _REPO}},
        }
    )
    client.create_pull_request = AsyncMock(
        return_value={
            "number": 1234,
            "html_url": "https://example/pr/1234",
            "head": {"repo": {"full_name": _REPO}, "sha": "approvedsha"},
            "base": {"repo": {"full_name": _REPO}},
        }
    )
    client.pull_request_reviews = AsyncMock(return_value=[])
    client.update_pull_request = AsyncMock(return_value={"html_url": "https://example/pr/1234"})
    client.create_issue_comment = AsyncMock(return_value={"id": 999})
    client.upsert_issue_comment = AsyncMock(return_value={"id": 777})
    client.ensure_label = AsyncMock(return_value=None)
    client.add_labels = AsyncMock(return_value=None)
    client.installation_token = AsyncMock(return_value="")
    return client


def _route(**overrides: Any) -> dict[str, Any]:
    route: dict[str, Any] = {
        "agent": "iterwheel-assembly",
        "delivery_id": "test-delivery",
        "kind": "issue_comment_created",
        "validation": {
            "issue_number": _ISSUE_NUMBER,
            "issue_labels": ["blueprint-ready", "stack-type-feature"],
        },
        "writeback": {
            "contract": {
                "issue_number": _ISSUE_NUMBER,
                "title": "[Task]: Circuit breaker",
            },
        },
        "command_flags": {},
    }
    route.update(overrides)
    return route


# ---------------------------------------------------------------------------
# _read_current_fix_round
# ---------------------------------------------------------------------------


def _make_round_label(n: int) -> str:
    return f"{ASSEMBLY_FIX_ROUND_LABEL_PREFIX}{n}"


def test_read_fix_round_no_labels() -> None:
    from voyager.bots.assembly.writeback import _read_current_fix_round

    assert _read_current_fix_round([]) == 0
    assert _read_current_fix_round(["blueprint-ready", "stack-type-feature"]) == 0


def test_read_fix_round_single_digit() -> None:
    from voyager.bots.assembly.writeback import _read_current_fix_round

    labels = [_make_round_label(3)]
    assert _read_current_fix_round(labels) == 3


def test_read_fix_round_max_is_returned() -> None:
    from voyager.bots.assembly.writeback import _read_current_fix_round

    labels = [_make_round_label(1), _make_round_label(5), _make_round_label(3)]
    assert _read_current_fix_round(labels) == 5


def test_read_fix_round_ignores_unrelated_labels() -> None:
    from voyager.bots.assembly.writeback import _read_current_fix_round

    labels = ["blueprint-ready", _make_round_label(7), "stack-type-feature"]
    assert _read_current_fix_round(labels) == 7


def test_read_fix_round_multidigit() -> None:
    from voyager.bots.assembly.writeback import _read_current_fix_round

    labels = [_make_round_label(42)]
    assert _read_current_fix_round(labels) == 42


# ---------------------------------------------------------------------------
# _max_fix_rounds_threshold
# ---------------------------------------------------------------------------


def test_max_fix_rounds_default() -> None:
    from voyager.bots.assembly.writeback import _max_fix_rounds_threshold

    assert _max_fix_rounds_threshold(None) == 8


def test_max_fix_rounds_from_config(monkeypatch) -> None:
    from voyager.bots.assembly.writeback import _max_fix_rounds_threshold
    from voyager.core.config import AssemblyConfig

    monkeypatch.delenv(ASSEMBLY_MAX_FIX_ROUNDS_ENV, raising=False)
    cfg = Mock(assembly=AssemblyConfig(max_fix_rounds=3))
    assert _max_fix_rounds_threshold(cfg) == 3


def test_max_fix_rounds_env_overrides_config(monkeypatch) -> None:
    from voyager.bots.assembly.writeback import _max_fix_rounds_threshold
    from voyager.core.config import AssemblyConfig

    monkeypatch.setenv(ASSEMBLY_MAX_FIX_ROUNDS_ENV, "5")
    cfg = Mock(assembly=AssemblyConfig(max_fix_rounds=10))
    assert _max_fix_rounds_threshold(cfg) == 5


def test_max_fix_rounds_env_invalid_falls_through(monkeypatch) -> None:
    from voyager.bots.assembly.writeback import _max_fix_rounds_threshold
    from voyager.core.config import AssemblyConfig

    monkeypatch.setenv(ASSEMBLY_MAX_FIX_ROUNDS_ENV, "not-a-number")
    cfg = Mock(assembly=AssemblyConfig(max_fix_rounds=4))
    assert _max_fix_rounds_threshold(cfg) == 4


def test_max_fix_rounds_env_empty_falls_through(monkeypatch) -> None:
    from voyager.bots.assembly.writeback import _max_fix_rounds_threshold
    from voyager.core.config import AssemblyConfig

    monkeypatch.setenv(ASSEMBLY_MAX_FIX_ROUNDS_ENV, "")
    cfg = Mock(assembly=AssemblyConfig(max_fix_rounds=6))
    assert _max_fix_rounds_threshold(cfg) == 6


# ---------------------------------------------------------------------------
# _apply_circuit_breaker
# ---------------------------------------------------------------------------


async def test_apply_circuit_breaker_adds_label_and_comment() -> None:
    from voyager.bots.assembly.writeback import _apply_circuit_breaker

    client = _mock_client()
    result: dict[str, Any] = {"writeback_failures": []}

    returned = await _apply_circuit_breaker(client, _REPO, _ISSUE_NUMBER, result)

    client.ensure_label.assert_awaited_once_with(
        "iterwheel-assembly",
        _REPO,
        LOOP_CIRCUIT_BROKEN_LABEL,
        color="d73a4a",
        description="Assembly automated fix loop halted pending human review.",
    )
    # Label was applied
    client.add_labels.assert_awaited_once_with(
        "iterwheel-assembly", _REPO, _ISSUE_NUMBER, [LOOP_CIRCUIT_BROKEN_LABEL]
    )
    # Comment was upserted with the circuit breaker marker
    call_kwargs = client.upsert_issue_comment.await_args
    assert call_kwargs is not None
    assert call_kwargs.kwargs["marker"] == _CIRCUIT_BREAKER_MARKER
    assert _CIRCUIT_BREAKER_MARKER in call_kwargs.kwargs["body"]
    assert returned is result


async def test_apply_circuit_breaker_http_error_is_recorded() -> None:
    from voyager.bots.assembly.writeback import _apply_circuit_breaker

    client = _mock_client()
    client.add_labels = AsyncMock(side_effect=TimeoutError("label timeout"))
    result: dict[str, Any] = {"writeback_failures": []}

    returned = await _apply_circuit_breaker(client, _REPO, _ISSUE_NUMBER, result)

    assert len(returned["writeback_failures"]) >= 1
    assert returned["writeback_failures"][0]["operation"] == "applyCircuitBreakerLabel"


async def test_apply_circuit_breaker_label_provisioning_failure_is_recorded() -> None:
    from voyager.bots.assembly.writeback import _apply_circuit_breaker

    client = _mock_client()
    client.ensure_label = AsyncMock(side_effect=TimeoutError("label create timeout"))
    result: dict[str, Any] = {"writeback_failures": []}

    returned = await _apply_circuit_breaker(client, _REPO, _ISSUE_NUMBER, result)

    assert returned["writeback_failures"][0]["operation"] == "ensureCircuitBreakerLabel"
    client.add_labels.assert_not_awaited()
    client.upsert_issue_comment.assert_awaited()


# ---------------------------------------------------------------------------
# Full dispatch flow — circuit breaker halts
# ---------------------------------------------------------------------------


def _adapter_executed() -> Mock:
    executed = Mock()
    executed.name = "dry-run"
    executed.execute = AsyncMock(
        return_value=Mock(
            status="executed",
            commit_shas=["a" * 40],
            summary="fix: something",
            details={},
        )
    )
    return executed


def test_dispatch_circuit_broken_label_halts_immediately() -> None:
    """When ``loop-circuit-broken`` label is already present, stop."""
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    labels = ["blueprint-ready", "stack-type-feature", LOOP_CIRCUIT_BROKEN_LABEL]
    client = _mock_client(get_issue_labels=labels)
    adapter = _adapter_executed()
    with (
        patch("voyager.bots.assembly.writeback.select_execution_adapter", return_value=adapter),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    assert result.get("applied") is False
    pr_action = (result.get("pull_request") or {}).get("action")
    assert pr_action == "circuit_broken_already"
    # Adapter should never have been called
    adapter.execute.assert_not_called()


def test_dispatch_threshold_exceeded_halts_with_label_and_comment() -> None:
    """When fix rounds reach the max threshold, the circuit breaker fires."""
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    # Simulate threshold+1 rounds (max=8, labels show round-8 → current_round=8)
    round_labels = [f"{ASSEMBLY_FIX_ROUND_LABEL_PREFIX}8"]
    client = _mock_client(get_issue_labels=["blueprint-ready", "stack-type-feature", *round_labels])
    adapter = _adapter_executed()
    with (
        patch("voyager.bots.assembly.writeback.select_execution_adapter", return_value=adapter),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
        patch("voyager.bots.assembly.writeback._max_fix_rounds_threshold", return_value=8),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    # The loop was halted
    assert result.get("applied") is False
    pr_action = (result.get("pull_request") or {}).get("action")
    assert pr_action == "circuit_broken"
    assert (result.get("pull_request") or {}).get("number") == 1234

    # The adapter should never have been called
    adapter.execute.assert_not_called()

    # The circuit breaker label was applied
    client.add_labels.assert_any_call(
        "iterwheel-assembly", _REPO, _ISSUE_NUMBER, [LOOP_CIRCUIT_BROKEN_LABEL]
    )

    # The circuit breaker comment was upserted
    comment_calls = [
        call
        for call in client.upsert_issue_comment.await_args_list
        if call.kwargs.get("marker") == _CIRCUIT_BREAKER_MARKER
    ]
    assert len(comment_calls) == 2, "Expected issue and PR escalation comments"
    assert [call.args[2] for call in comment_calls] == [_ISSUE_NUMBER, 1234]
    progress_calls = [
        call
        for call in client.upsert_issue_comment.await_args_list
        if call.kwargs.get("marker") == ASSEMBLY_COMMENT_MARKER
    ]
    assert len(progress_calls) == 2
    assert [call.args[2] for call in progress_calls] == [_ISSUE_NUMBER, 1234]
    assert "status: `blocked`" in progress_calls[0].kwargs["body"]
    assert "Circuit breaker threshold reached" in progress_calls[0].kwargs["body"]


def test_dispatch_threshold_with_current_human_approval_proceeds_normally() -> None:
    """Human approval on the current PR head satisfies the stop-rule gate."""
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    round_labels = [f"{ASSEMBLY_FIX_ROUND_LABEL_PREFIX}8"]
    client = _mock_client(get_issue_labels=["blueprint-ready", "stack-type-feature", *round_labels])
    client.pull_request_reviews.return_value = [
        {
            "id": 100,
            "state": "APPROVED",
            "commit_id": "approvedsha",
            "submitted_at": "2026-06-18T00:00:00Z",
            "user": {"login": "human-reviewer"},
        },
        {
            "id": 101,
            "state": "COMMENTED",
            "commit_id": "approvedsha",
            "submitted_at": "2026-06-18T00:05:00Z",
            "user": {"login": "human-reviewer"},
        },
    ]
    adapter = _adapter_executed()
    with (
        patch("voyager.bots.assembly.writeback.select_execution_adapter", return_value=adapter),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
        patch("voyager.bots.assembly.writeback._max_fix_rounds_threshold", return_value=8),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    adapter.execute.assert_called()
    assert result.get("applied") is True
    assert (result.get("circuit_breaker") or {}).get("human_approval_bypass") is True
    assert not any(
        call.args
        == (
            "iterwheel-assembly",
            _REPO,
            _ISSUE_NUMBER,
            [LOOP_CIRCUIT_BROKEN_LABEL],
        )
        for call in client.add_labels.await_args_list
    )
    escalation_calls = [
        call
        for call in client.upsert_issue_comment.await_args_list
        if call.kwargs.get("marker") == _CIRCUIT_BREAKER_MARKER
    ]
    assert escalation_calls == []


def test_dispatch_threshold_not_exceeded_proceeds_normally() -> None:
    """When fix rounds are below the threshold, execution proceeds."""
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    client = _mock_client(get_issue_labels=["blueprint-ready", "stack-type-feature"])
    adapter = _adapter_executed()
    with (
        patch(
            "voyager.bots.assembly.writeback.select_execution_adapter",
            return_value=adapter,
        ),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    # The adapter should have been called (normal flow)
    adapter.execute.assert_called()
    assert result.get("applied") is True

    # No circuit breaker comment
    comment_calls = [
        call
        for call in client.upsert_issue_comment.await_args_list
        if call.kwargs.get("marker") == _CIRCUIT_BREAKER_MARKER
    ]
    assert len(comment_calls) == 0, "No escalation comment expected"


def test_dispatch_successful_push_provisions_fix_round_label() -> None:
    """A successful push creates the next dynamic round label before attaching it."""
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    client = _mock_client(get_issue_labels=["blueprint-ready", "stack-type-feature"])
    adapter = _adapter_executed()
    with (
        patch(
            "voyager.bots.assembly.writeback.select_execution_adapter",
            return_value=adapter,
        ),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    assert result.get("applied") is True
    next_round_label = f"{ASSEMBLY_FIX_ROUND_LABEL_PREFIX}1"
    client.ensure_label.assert_any_await(
        "iterwheel-assembly",
        _REPO,
        next_round_label,
        color="cfd3d7",
        description="Assembly automated fix round marker.",
    )
    client.add_labels.assert_any_await(
        "iterwheel-assembly", _REPO, _ISSUE_NUMBER, [next_round_label]
    )


def test_dispatch_fix_round_label_provisioning_failure_records_failure() -> None:
    """If the dynamic counter label cannot be created, do not attach a
    possibly missing label and report the counter writeback failure.
    """
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    client = _mock_client(get_issue_labels=["blueprint-ready", "stack-type-feature"])
    client.ensure_label = AsyncMock(side_effect=TimeoutError("label create timeout"))
    adapter = _adapter_executed()
    with (
        patch(
            "voyager.bots.assembly.writeback.select_execution_adapter",
            return_value=adapter,
        ),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    assert any(
        failure["operation"] == "ensureFixRoundLabel" for failure in result["writeback_failures"]
    )
    client.add_labels.assert_not_awaited()


def test_dispatch_threshold_dry_run_makes_no_mutations() -> None:
    """A threshold-hit breaker on a dry-run invocation performs NO GitHub
    mutations: no label, no escalation comment, no progress comment (Codex P2).
    """
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    round_labels = [f"{ASSEMBLY_FIX_ROUND_LABEL_PREFIX}8"]
    client = _mock_client(get_issue_labels=["blueprint-ready", "stack-type-feature", *round_labels])
    adapter = _adapter_executed()
    with (
        patch("voyager.bots.assembly.writeback.select_execution_adapter", return_value=adapter),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=True),
        patch("voyager.bots.assembly.writeback._max_fix_rounds_threshold", return_value=8),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    assert result.get("applied") is False
    assert (result.get("pull_request") or {}).get("action") == "circuit_broken_dry_run"
    adapter.execute.assert_not_called()
    client.add_labels.assert_not_called()
    client.upsert_issue_comment.assert_not_called()


def test_dispatch_circuit_broken_already_retries_escalation() -> None:
    """On the already-broken path (non-dry-run), the escalation comment is
    retried because the label is idempotent but the comment is not (Codex P2).
    """
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    labels = ["blueprint-ready", "stack-type-feature", LOOP_CIRCUIT_BROKEN_LABEL]
    client = _mock_client(get_issue_labels=labels)
    adapter = _adapter_executed()
    with (
        patch("voyager.bots.assembly.writeback.select_execution_adapter", return_value=adapter),
        patch("voyager.bots.assembly.writeback.dry_run_enabled", return_value=False),
    ):
        result = asyncio.run(dispatch_assembly_writeback(client, _route(), repository=_REPO))

    assert (result.get("pull_request") or {}).get("action") == "circuit_broken_already"
    adapter.execute.assert_not_called()
    escalation_calls = [
        call
        for call in client.upsert_issue_comment.await_args_list
        if call.kwargs.get("marker") == _CIRCUIT_BREAKER_MARKER
    ]
    assert len(escalation_calls) == 2, "issue and PR escalation comments should be retried"
    assert [call.args[2] for call in escalation_calls] == [_ISSUE_NUMBER, 1234]
    progress_calls = [
        call
        for call in client.upsert_issue_comment.await_args_list
        if call.kwargs.get("marker") == ASSEMBLY_COMMENT_MARKER
    ]
    assert len(progress_calls) == 2
    assert [call.args[2] for call in progress_calls] == [_ISSUE_NUMBER, 1234]
    assert "status: `blocked`" in progress_calls[0].kwargs["body"]
    assert "already active" in progress_calls[0].kwargs["body"]


async def test_circuit_breaker_escalation_mentions_round_labels() -> None:
    """The escalation comment tells operators to remove the fix-round labels
    too, so the documented resume path actually clears the breaker (Codex P2).
    """
    from voyager.bots.assembly.writeback import _apply_circuit_breaker

    client = _mock_client()
    result: dict[str, Any] = {"writeback_failures": []}
    await _apply_circuit_breaker(client, _REPO, _ISSUE_NUMBER, result)

    body = client.upsert_issue_comment.await_args.kwargs["body"]
    assert "assembly-fix-round-" in body
    assert LOOP_CIRCUIT_BROKEN_LABEL in body
