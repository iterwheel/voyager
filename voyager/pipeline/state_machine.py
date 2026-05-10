"""Rocket factory pipeline state machine — Blueprint → Stack → Clearance → Liftoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    """Pipeline stages for the 4-bot subset (W3D scope)."""

    BLUEPRINT_PENDING = "blueprint_pending"
    BLUEPRINT_REVISION = "blueprint_revision"
    BLUEPRINT_READY = "blueprint_ready"
    STACK_PENDING = "stack_pending"
    STACK_CLASSIFIED = "stack_classified"
    PR_OPEN = "pr_open"
    CLEARANCE_PENDING = "clearance_pending"
    CLEARANCE_READY = "clearance_ready"
    CLEARANCE_BLOCKED = "clearance_blocked"
    LIFTOFF_DONE = "liftoff_done"


# Canonical forward order — used to decide whether a signal is stale.
# Revision and blocked are side-states; they sit at the same rank as their
# "pending" counterpart for ordering purposes.
_STAGE_ORDER: dict[str, int] = {
    Stage.BLUEPRINT_PENDING.value: 0,
    Stage.BLUEPRINT_REVISION.value: 0,
    Stage.BLUEPRINT_READY.value: 1,
    Stage.STACK_PENDING.value: 2,
    Stage.STACK_CLASSIFIED.value: 3,
    Stage.PR_OPEN.value: 4,
    Stage.CLEARANCE_PENDING.value: 5,
    Stage.CLEARANCE_READY.value: 6,
    Stage.CLEARANCE_BLOCKED.value: 6,
    Stage.LIFTOFF_DONE.value: 7,
}

# Maps (current_stage_value, signal_kind) → next_stage_value.
# force-restart is handled separately (any stage → payload.restart_to).
# no-blueprint-needed is handled separately (jump to stack_pending from blueprint stages).
_TRANSITIONS: dict[tuple[str, str], str] = {
    # Blueprint stage
    (Stage.BLUEPRINT_PENDING.value, "blueprint-ready"): Stage.BLUEPRINT_READY.value,
    (Stage.BLUEPRINT_PENDING.value, "blueprint-revision"): Stage.BLUEPRINT_REVISION.value,
    (Stage.BLUEPRINT_REVISION.value, "blueprint-ready"): Stage.BLUEPRINT_READY.value,
    (Stage.BLUEPRINT_REVISION.value, "blueprint-revision"): Stage.BLUEPRINT_REVISION.value,
    # Stack stage
    (Stage.BLUEPRINT_READY.value, "stack-pending"): Stage.STACK_PENDING.value,
    (Stage.BLUEPRINT_READY.value, "stack-classified"): Stage.STACK_CLASSIFIED.value,
    (Stage.STACK_PENDING.value, "stack-classified"): Stage.STACK_CLASSIFIED.value,
    # PR stage
    (Stage.STACK_CLASSIFIED.value, "pr-opened"): Stage.PR_OPEN.value,
    # Clearance stage
    (Stage.PR_OPEN.value, "clearance-pending"): Stage.CLEARANCE_PENDING.value,
    (Stage.CLEARANCE_PENDING.value, "clearance-ready"): Stage.CLEARANCE_READY.value,
    (Stage.CLEARANCE_READY.value, "clearance-blocked"): Stage.CLEARANCE_BLOCKED.value,
    (Stage.CLEARANCE_BLOCKED.value, "clearance-ready"): Stage.CLEARANCE_READY.value,
    # Liftoff
    (Stage.CLEARANCE_READY.value, "liftoff-done"): Stage.LIFTOFF_DONE.value,
}

# Signal kinds that originate from a specific source stage rank.
# Used to detect stale signals: if the signal's source rank < current rank, it is stale.
_SIGNAL_SOURCE_RANK: dict[str, int] = {
    "blueprint-ready": _STAGE_ORDER[Stage.BLUEPRINT_PENDING.value],
    "blueprint-revision": _STAGE_ORDER[Stage.BLUEPRINT_PENDING.value],
    "stack-pending": _STAGE_ORDER[Stage.BLUEPRINT_READY.value],
    "stack-classified": _STAGE_ORDER[Stage.BLUEPRINT_READY.value],
    "pr-opened": _STAGE_ORDER[Stage.STACK_CLASSIFIED.value],
    "clearance-pending": _STAGE_ORDER[Stage.PR_OPEN.value],
    "clearance-ready": _STAGE_ORDER[Stage.CLEARANCE_PENDING.value],
    "clearance-blocked": _STAGE_ORDER[Stage.CLEARANCE_READY.value],
    "liftoff-done": _STAGE_ORDER[Stage.CLEARANCE_READY.value],
    "no-blueprint-needed": _STAGE_ORDER[Stage.BLUEPRINT_PENDING.value],
    "force-restart": -1,  # always applicable
}


@dataclass
class Signal:
    kind: str
    target_id: str
    payload: Any


@dataclass
class PipelineState:
    target_kind: str
    target_id: str
    stage: str
    history: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # JSON round-trip delivers lists; normalise to tuples.
        self.history = [
            (entry[0], entry[1]) if not isinstance(entry, tuple) else entry
            for entry in self.history
        ]


def advance_pipeline(state: PipelineState, signal: Signal) -> PipelineState:
    """Apply *signal* to *state* and return the new state.

    Stale signals (source rank < current rank) and already-at-destination
    signals are no-ops — same state object is returned unchanged.
    """
    current = state.stage

    # force-restart: always accepted; target stage from payload or default
    if signal.kind == "force-restart":
        payload = signal.payload or {}
        restart_to = payload.get("restart_to", Stage.BLUEPRINT_PENDING.value)
        new_history = [*list(state.history), (current, signal.kind)]
        return PipelineState(
            target_kind=state.target_kind,
            target_id=state.target_id,
            stage=restart_to,
            history=new_history,
        )

    # no-blueprint-needed: skip directly to stack_pending from any blueprint stage
    if signal.kind == "no-blueprint-needed":
        current_rank = _STAGE_ORDER.get(current, -1)
        blueprint_rank = _STAGE_ORDER[Stage.BLUEPRINT_PENDING.value]
        if current_rank > blueprint_rank:
            # already past blueprint — stale, no-op
            return state
        new_history = [*list(state.history), (current, signal.kind)]
        return PipelineState(
            target_kind=state.target_kind,
            target_id=state.target_id,
            stage=Stage.STACK_PENDING.value,
            history=new_history,
        )

    # Stale signal check: if signal source rank is strictly less than current rank, no-op
    source_rank = _SIGNAL_SOURCE_RANK.get(signal.kind, -1)
    current_rank = _STAGE_ORDER.get(current, -1)
    if source_rank < current_rank:
        return state

    # Look up explicit transition
    next_stage = _TRANSITIONS.get((current, signal.kind))
    if next_stage is None:
        # No valid transition for this (stage, signal) pair — no-op
        return state

    # Idempotency: if already at destination, no-op
    if next_stage == current:
        return state

    new_history = [*list(state.history), (current, signal.kind)]
    return PipelineState(
        target_kind=state.target_kind,
        target_id=state.target_id,
        stage=next_stage,
        history=new_history,
    )


def advance_pipeline_for_unknown(target_id: str, signal: Signal) -> PipelineState:
    """Initialise a fresh pipeline state for an unseen *target_id* and apply *signal*."""
    initial = PipelineState(
        target_kind="issue",
        target_id=target_id,
        stage=Stage.BLUEPRINT_PENDING.value,
        history=[],
    )
    return advance_pipeline(initial, signal)
