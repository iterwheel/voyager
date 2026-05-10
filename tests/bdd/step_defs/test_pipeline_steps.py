"""Step definitions for Pipeline orchestration BDD scenarios."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _pytest.stash import StashKey
from pytest_bdd import given, parsers, scenarios, then, when
from pytest_bdd.scenario import inject_fixture

_multi_states_key: StashKey[list[dict]] = StashKey()

# CRITICAL: do NOT import from voyager.* at module top level — those modules
# don't have implementations yet, so top-level imports would crash pytest
# collection. Import lazily INSIDE step functions instead.

scenarios("../features/pipeline.feature")

_PIPELINE_FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "pipeline"


# ---------------------------------------------------------------------------
# Local fixture loader — pipeline snapshots live under fixtures/pipeline/
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_fixture():
    """Load a pipeline state or signal fixture by name (without .json suffix)."""

    def _load(name: str) -> dict:
        path = _PIPELINE_FIXTURES_DIR / f"{name}.json"
        return json.loads(path.read_text())

    return _load


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("the pipeline module is available", target_fixture="pipeline_available")
def pipeline_available() -> bool:
    # Deferred import — module is empty at BDD-spec time.
    # Collection succeeds even before any production code is written.
    return True


# ---------------------------------------------------------------------------
# Given — state and signal loading
# ---------------------------------------------------------------------------


@given(parsers.parse('a pipeline state "{name}"'), target_fixture="state")
def load_pipeline_state(pipeline_fixture, name: str) -> dict:
    return pipeline_fixture(name)


@given(parsers.parse('a pipeline signal "{name}"'), target_fixture="signal")
def load_pipeline_signal(pipeline_fixture, name: str) -> dict:
    return pipeline_fixture(name)


@given(parsers.parse('a fresh pipeline target "{target_id}"'), target_fixture="state")
def fresh_pipeline_state(target_id: str) -> dict:
    from voyager.pipeline.state_machine import Stage  # lazy import

    return {
        "target_kind": "issue",
        "target_id": target_id,
        "stage": Stage.BLUEPRINT_PENDING.value,
        "history": [],
    }


@given(
    parsers.parse('no existing pipeline state for target "{target_id}"'),
    target_fixture="missing_target_id",
)
def no_existing_state(target_id: str) -> str:
    return target_id


@given(
    parsers.parse('a pipeline state for target "{target_id}" at stage "{stage}"'),
    target_fixture="multi_state_a",
)
def pipeline_state_for_target(request: pytest.FixtureRequest, target_id: str, stage: str) -> dict:
    # pytest-bdd matches this step for BOTH "Given" and "And" occurrences of the
    # same text (Bug 2): the second invocation would also set multi_state_a.
    # We use a stash-backed accumulator so the second call injects multi_state_b
    # imperatively and returns the first state unchanged.
    state = {"target_kind": "issue", "target_id": target_id, "stage": stage, "history": []}
    states: list[dict] = request.node.stash.setdefault(_multi_states_key, [])
    states.append(state)
    if len(states) == 2:
        inject_fixture(request, "multi_state_b", states[1])
        return states[0]
    return state


# ---------------------------------------------------------------------------
# When — single advance
# ---------------------------------------------------------------------------


@when("the signal is applied to the pipeline state", target_fixture="result")
def apply_signal(state: dict, signal: dict) -> dict:
    from voyager.pipeline.state_machine import (  # lazy import
        PipelineState,
        Signal,
        advance_pipeline,
    )

    ps = PipelineState(**state)
    sig = Signal(**signal)
    return advance_pipeline(ps, sig).__dict__


@when("the same signal is applied again", target_fixture="result")
def apply_signal_again(result: dict, signal: dict) -> dict:
    from voyager.pipeline.state_machine import (  # lazy import
        PipelineState,
        Signal,
        advance_pipeline,
    )

    ps = PipelineState(**result)
    sig = Signal(**signal)
    return advance_pipeline(ps, sig).__dict__


@when("the blueprint-ready signal is then applied", target_fixture="result")
def apply_blueprint_ready_after_revision(result: dict) -> dict:
    from voyager.pipeline.state_machine import (  # lazy import
        PipelineState,
        Signal,
        advance_pipeline,
    )

    ps = PipelineState(**result)
    sig = Signal(kind="blueprint-ready", target_id=ps.target_id, payload=None)
    return advance_pipeline(ps, sig).__dict__


@when(
    parsers.parse('blueprint-ready signal is applied to target "{target_id}"'),
    target_fixture="advance_result",
)
def apply_blueprint_ready_to_target(
    target_id: str, multi_state_a: dict, multi_state_b: dict
) -> dict[str, Any]:
    from voyager.pipeline.state_machine import (  # lazy import
        PipelineState,
        Signal,
        advance_pipeline,
    )

    sig = Signal(kind="blueprint-ready", target_id=target_id, payload=None)
    new_a = advance_pipeline(PipelineState(**multi_state_a), sig).__dict__
    return {"a": new_a, "b": multi_state_b}


@when("the signal is applied to the unknown target", target_fixture="result")
def apply_signal_unknown_target(missing_target_id: str, signal: dict) -> dict:
    from voyager.pipeline.state_machine import Signal, advance_pipeline_for_unknown  # lazy import

    sig = Signal(**signal)
    return advance_pipeline_for_unknown(missing_target_id, sig).__dict__


# ---------------------------------------------------------------------------
# When — ordered multi-signal walk
# ---------------------------------------------------------------------------


@when(
    "the following signals are applied in order:",
    target_fixture="result",
)
def apply_signal_sequence(state: dict, datatable: list[list[str]]) -> dict:
    # Bug 1 fix: pytest-bdd 8.x passes step.name WITHOUT data table rows.
    # parsers.parse("...\n{signals_table}") never matches. Use the `datatable`
    # parameter (populated from step.datatable.raw() by the framework).
    from voyager.pipeline.state_machine import (  # lazy import
        PipelineState,
        Signal,
        advance_pipeline,
    )

    # datatable[0] is the header row ["signal_kind"]; datatable[1:] are data rows.
    kinds = [row[0] for row in datatable[1:]]
    ps = PipelineState(**state)
    for kind in kinds:
        sig = Signal(kind=kind, target_id=ps.target_id, payload=None)
        ps = advance_pipeline(ps, sig)
    return ps.__dict__


# ---------------------------------------------------------------------------
# Then — stage assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('the new stage is "{expected_stage}"'))
def assert_new_stage(result: dict, expected_stage: str) -> None:
    assert result["stage"] == expected_stage


@then(parsers.parse('the final stage is "{expected_stage}"'))
def assert_final_stage(result: dict, expected_stage: str) -> None:
    assert result["stage"] == expected_stage


@then(parsers.parse('the stage is unchanged at "{expected_stage}"'))
def assert_stage_unchanged(result: dict, expected_stage: str) -> None:
    assert result["stage"] == expected_stage


# ---------------------------------------------------------------------------
# Then — history assertions
# ---------------------------------------------------------------------------


@then("the transition is recorded in history")
def assert_history_non_empty(result: dict) -> None:
    assert len(result["history"]) >= 1


@then(parsers.parse("history contains {count:d} transitions"))
def assert_history_count(result: dict, count: int) -> None:
    assert len(result["history"]) == count


@then(parsers.parse('the first history entry stage is "{expected_stage}"'))
def assert_first_history_stage(result: dict, expected_stage: str) -> None:
    assert result["history"][0][0] == expected_stage


@then(parsers.parse('the first history entry signal is "{expected_signal}"'))
def assert_first_history_signal(result: dict, expected_signal: str) -> None:
    assert result["history"][0][1] == expected_signal


# ---------------------------------------------------------------------------
# Then — stale / reject assertions
# ---------------------------------------------------------------------------


@then("the stale signal is rejected")
def assert_stale_rejected(result: dict) -> None:
    # Ambiguity: advance_pipeline on a stale signal may return the same state
    # object unchanged OR raise a StaleSignalError — we assert the stage is
    # unchanged (checked by a prior Then) and that no new history entry was
    # added for the stale transition.  Production code must preserve this
    # invariant without raising an unhandled exception.
    stage = result["stage"]
    for entry_stage, _entry_signal in result["history"]:
        assert entry_stage != stage, f"History must not record a self-transition at stage {stage!r}"


# ---------------------------------------------------------------------------
# Then — unknown target initialisation
# ---------------------------------------------------------------------------


@then(parsers.parse('a new pipeline state is created for "{target_id}"'))
def assert_new_state_for_target(result: dict, target_id: str) -> None:
    assert result["target_id"] == target_id


# ---------------------------------------------------------------------------
# Then — concurrent isolation
# ---------------------------------------------------------------------------


@then(parsers.parse('target "{target_id}" stage is "{expected_stage}"'))
def assert_target_a_stage(advance_result: dict, target_id: str, expected_stage: str) -> None:
    assert advance_result["a"]["target_id"] == target_id
    assert advance_result["a"]["stage"] == expected_stage


@then(parsers.parse('target "{target_id2}" stage is still "{expected_stage}"'))
def assert_target_b_stage_unchanged(
    advance_result: dict, target_id2: str, expected_stage: str
) -> None:
    assert advance_result["b"]["target_id"] == target_id2
    assert advance_result["b"]["stage"] == expected_stage
