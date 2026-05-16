"""State machine tests for numbered clearance labels (issue #25).

Verifies that new numbered signals (clearance-1-pending through clearance-4-ready-for-merge)
and the new clearance-3-ready-for-approval signal are wired correctly, AND that
the three legacy signals (clearance-pending, clearance-blocked, clearance-ready)
continue to work unchanged.

Signal → Stage expectations:
- clearance-1-pending: PR_OPEN → CLEARANCE_PENDING
- clearance-2-blocked: CLEARANCE_PENDING → CLEARANCE_BLOCKED, CLEARANCE_READY → CLEARANCE_BLOCKED
- clearance-3-ready-for-approval: PR_OPEN → CLEARANCE_PENDING (folded into PENDING)
  CLEARANCE_PENDING → CLEARANCE_PENDING (idempotent/self-loop treated as no-advance)
  CLEARANCE_BLOCKED → CLEARANCE_PENDING (recovery)
- clearance-4-ready-for-merge: CLEARANCE_PENDING → CLEARANCE_READY, CLEARANCE_BLOCKED → CLEARANCE_READY
- Legacy clearance-pending: PR_OPEN → CLEARANCE_PENDING (unchanged)
- Legacy clearance-ready: CLEARANCE_PENDING → CLEARANCE_READY (unchanged)
- Legacy clearance-blocked: CLEARANCE_PENDING → CLEARANCE_BLOCKED (unchanged)
"""

from __future__ import annotations

from voyager.pipeline.state_machine import PipelineState, Signal, Stage, advance_pipeline

TARGET = "iterwheel/voyager#42"


def _state(stage: Stage) -> PipelineState:
    return PipelineState(target_kind="issue", target_id=TARGET, stage=stage.value, history=[])


def _signal(kind: str) -> Signal:
    return Signal(kind=kind, target_id=TARGET, payload={})


# ---------------------------------------------------------------------------
# New numbered signals — forward transitions
# ---------------------------------------------------------------------------


def test_clearance_1_pending_from_pr_open() -> None:
    new_state = advance_pipeline(_state(Stage.PR_OPEN), _signal("clearance-1-pending"))
    assert new_state.stage == Stage.CLEARANCE_PENDING.value


def test_clearance_2_blocked_from_clearance_pending() -> None:
    new_state = advance_pipeline(_state(Stage.CLEARANCE_PENDING), _signal("clearance-2-blocked"))
    assert new_state.stage == Stage.CLEARANCE_BLOCKED.value


def test_clearance_2_blocked_from_clearance_ready() -> None:
    new_state = advance_pipeline(_state(Stage.CLEARANCE_READY), _signal("clearance-2-blocked"))
    assert new_state.stage == Stage.CLEARANCE_BLOCKED.value


def test_clearance_4_ready_for_merge_from_clearance_pending() -> None:
    new_state = advance_pipeline(
        _state(Stage.CLEARANCE_PENDING), _signal("clearance-4-ready-for-merge")
    )
    assert new_state.stage == Stage.CLEARANCE_READY.value


def test_clearance_4_ready_for_merge_from_clearance_blocked() -> None:
    new_state = advance_pipeline(
        _state(Stage.CLEARANCE_BLOCKED), _signal("clearance-4-ready-for-merge")
    )
    assert new_state.stage == Stage.CLEARANCE_READY.value


# ---------------------------------------------------------------------------
# clearance-3-ready-for-approval — folds into CLEARANCE_PENDING
# ---------------------------------------------------------------------------


def test_clearance_3_ready_for_approval_from_pr_open_goes_to_clearance_pending() -> None:
    new_state = advance_pipeline(_state(Stage.PR_OPEN), _signal("clearance-3-ready-for-approval"))
    assert new_state.stage == Stage.CLEARANCE_PENDING.value


def test_clearance_3_ready_for_approval_from_clearance_blocked_recovers_to_pending() -> None:
    new_state = advance_pipeline(
        _state(Stage.CLEARANCE_BLOCKED), _signal("clearance-3-ready-for-approval")
    )
    assert new_state.stage == Stage.CLEARANCE_PENDING.value


def test_clearance_3_ready_for_approval_from_clearance_pending_is_no_advance() -> None:
    # From PENDING, "ready for approval" is a sub-state — should not advance forward
    # (it either stays PENDING or is idempotent; it must NOT advance to CLEARANCE_READY)
    state = _state(Stage.CLEARANCE_PENDING)
    new_state = advance_pipeline(state, _signal("clearance-3-ready-for-approval"))
    assert new_state.stage != Stage.CLEARANCE_READY.value


# ---------------------------------------------------------------------------
# Legacy signals — unchanged behavior (migration regression guard)
# ---------------------------------------------------------------------------


def test_legacy_clearance_pending_from_pr_open() -> None:
    new_state = advance_pipeline(_state(Stage.PR_OPEN), _signal("clearance-pending"))
    assert new_state.stage == Stage.CLEARANCE_PENDING.value


def test_legacy_clearance_ready_from_clearance_pending() -> None:
    new_state = advance_pipeline(_state(Stage.CLEARANCE_PENDING), _signal("clearance-ready"))
    assert new_state.stage == Stage.CLEARANCE_READY.value


def test_legacy_clearance_blocked_from_clearance_pending() -> None:
    new_state = advance_pipeline(_state(Stage.CLEARANCE_PENDING), _signal("clearance-blocked"))
    assert new_state.stage == Stage.CLEARANCE_BLOCKED.value


def test_legacy_clearance_ready_from_clearance_blocked() -> None:
    new_state = advance_pipeline(_state(Stage.CLEARANCE_BLOCKED), _signal("clearance-ready"))
    assert new_state.stage == Stage.CLEARANCE_READY.value


def test_legacy_clearance_blocked_from_clearance_ready() -> None:
    new_state = advance_pipeline(_state(Stage.CLEARANCE_READY), _signal("clearance-blocked"))
    assert new_state.stage == Stage.CLEARANCE_BLOCKED.value


# ---------------------------------------------------------------------------
# History recording — transitions are captured
# ---------------------------------------------------------------------------


def test_clearance_1_pending_records_history() -> None:
    state = _state(Stage.PR_OPEN)
    new_state = advance_pipeline(state, _signal("clearance-1-pending"))
    assert len(new_state.history) == 1
    assert new_state.history[0][0] == Stage.PR_OPEN.value
    assert new_state.history[0][1] == "clearance-1-pending"


def test_clearance_4_ready_records_history() -> None:
    state = _state(Stage.CLEARANCE_PENDING)
    new_state = advance_pipeline(state, _signal("clearance-4-ready-for-merge"))
    assert len(new_state.history) == 1
    assert new_state.history[0][1] == "clearance-4-ready-for-merge"


# ---------------------------------------------------------------------------
# Full forward walk with numbered signals reaches CLEARANCE_READY
# ---------------------------------------------------------------------------


def test_full_walk_with_numbered_labels() -> None:
    state = _state(Stage.PR_OPEN)
    state = advance_pipeline(state, _signal("clearance-1-pending"))
    assert state.stage == Stage.CLEARANCE_PENDING.value
    state = advance_pipeline(state, _signal("clearance-4-ready-for-merge"))
    assert state.stage == Stage.CLEARANCE_READY.value


def test_full_walk_with_numbered_labels_block_then_recover() -> None:
    state = _state(Stage.PR_OPEN)
    state = advance_pipeline(state, _signal("clearance-1-pending"))
    state = advance_pipeline(state, _signal("clearance-2-blocked"))
    assert state.stage == Stage.CLEARANCE_BLOCKED.value
    state = advance_pipeline(state, _signal("clearance-4-ready-for-merge"))
    assert state.stage == Stage.CLEARANCE_READY.value


def test_ready_for_approval_then_full_approval_walk() -> None:
    """clearance-3-ready-for-approval keeps PR in PENDING, then clearance-4 advances."""
    state = _state(Stage.PR_OPEN)
    state = advance_pipeline(state, _signal("clearance-3-ready-for-approval"))
    assert state.stage == Stage.CLEARANCE_PENDING.value
    state = advance_pipeline(state, _signal("clearance-4-ready-for-merge"))
    assert state.stage == Stage.CLEARANCE_READY.value


# ---------------------------------------------------------------------------
# Trinity round-2 findings — missing transitions (item #5)
# ---------------------------------------------------------------------------


def test_clearance_ready_can_downgrade_to_ready_for_approval() -> None:
    """From CLEARANCE_READY, signal clearance-3-ready-for-approval → CLEARANCE_PENDING (downgrade)."""
    new_state = advance_pipeline(
        _state(Stage.CLEARANCE_READY), _signal("clearance-3-ready-for-approval")
    )
    assert new_state.stage == Stage.CLEARANCE_PENDING.value


def test_pr_open_first_eval_block_transition() -> None:
    """From PR_OPEN, signal clearance-2-blocked → CLEARANCE_BLOCKED (first-eval-block path)."""
    new_state = advance_pipeline(_state(Stage.PR_OPEN), _signal("clearance-2-blocked"))
    assert new_state.stage == Stage.CLEARANCE_BLOCKED.value
