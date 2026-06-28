"""Step definitions for Countdown resolve-loop BDD scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.bdd

scenarios("../features/countdown.feature")

SANDBOX_REPO = "iterwheel/voyager-sandbox"
DEFAULT_PR = 1


@dataclass
class CountdownScenario:
    threads: list[dict[str, Any]] = field(default_factory=list)
    live_comment_counts: dict[str, int | None] = field(default_factory=dict)
    resolver_identity: str = "iterwheel-countdown-user"
    gate: Any | None = None
    summary: Any | None = None
    raised: BaseException | None = None
    resolve_attempts: list[Any] = field(default_factory=list)


@pytest.fixture
def countdown_scenario() -> CountdownScenario:
    return CountdownScenario()


def _thread(*, tid: str, comments: int = 0, outdated: bool = False) -> dict[str, Any]:
    from tests.unit.test_countdown_loop import _thread as unit_thread

    return unit_thread(
        tid=tid,
        outdated=outdated,
        comments=[(f"reviewer-{i}", f"comment {i}") for i in range(comments)],
    )


def _gate(should_resolve: bool, reason: str) -> Any:
    from tests.unit.test_countdown_loop import FakeGate
    from voyager.core.countdown_loop import GateVerdict

    return FakeGate(GateVerdict(should_resolve, reason))


def _resolver_gql(identity: str):
    def _gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if "query viewer" in query.lower():
            return {"viewer": {"login": identity}}
        if "mutation" in query.lower():
            return {"resolveReviewThread": {"thread": {"isResolved": True}}}
        return {
            "node": {
                "id": variables.get("threadId"),
                "isResolved": False,
                "isOutdated": False,
                "viewerCanResolve": True,
                "viewerCanReply": True,
                "pullRequest": {"repository": {"nameWithOwner": SANDBOX_REPO}},
            }
        }

    return _gql


def _run_countdown_loop(
    scenario: CountdownScenario,
    *,
    dry_run: bool = False,
    max_resolves: int = 20,
    real_resolver_path: bool = False,
) -> None:
    from tests.unit.test_countdown_loop import FakeReadGql, _fake_resolver
    from voyager.core.countdown_loop import run_resolve_loop

    read_gql = FakeReadGql(
        {SANDBOX_REPO: [DEFAULT_PR]},
        {(SANDBOX_REPO, DEFAULT_PR): scenario.threads},
        comment_counts=scenario.live_comment_counts,
    )
    gate = scenario.gate or _gate(True, "ok")
    scenario.gate = gate

    resolve_fn = None
    if not real_resolver_path:
        resolve_fn, scenario.resolve_attempts = _fake_resolver()

    try:
        scenario.summary = run_resolve_loop(
            requested_repos=[SANDBOX_REPO],
            gate=gate,
            read_gql=read_gql,
            resolve_gql=_resolver_gql(scenario.resolver_identity),
            resolve_fn=resolve_fn,
            dry_run=dry_run,
            max_resolves=max_resolves,
            audit_path=None,
        )
    except BaseException as exc:
        scenario.raised = exc


def _summary(scenario: CountdownScenario) -> Any:
    assert scenario.raised is None
    assert scenario.summary is not None
    return scenario.summary


@given("a Countdown candidate thread")
def countdown_candidate_thread(countdown_scenario: CountdownScenario) -> None:
    countdown_scenario.threads = [_thread(tid="T1")]


@given("three Countdown candidate threads")
def three_countdown_candidate_threads(countdown_scenario: CountdownScenario) -> None:
    countdown_scenario.threads = [_thread(tid=f"T{i}") for i in range(1, 4)]


@given("an outdated Countdown candidate thread")
def outdated_countdown_candidate_thread(countdown_scenario: CountdownScenario) -> None:
    countdown_scenario.threads = [_thread(tid="T1", outdated=True)]


@given(parsers.parse("a Countdown candidate thread with {comment_count:d} fetched comment"))
def countdown_candidate_thread_with_comments(
    countdown_scenario: CountdownScenario, comment_count: int
) -> None:
    countdown_scenario.threads = [_thread(tid="T1", comments=comment_count)]


@given(parsers.parse('the Countdown gate vetoes with reason "{reason}"'))
def countdown_gate_vetoes(countdown_scenario: CountdownScenario, reason: str) -> None:
    countdown_scenario.gate = _gate(False, reason)


@given("the Countdown gate approves all candidates")
def countdown_gate_approves(countdown_scenario: CountdownScenario) -> None:
    countdown_scenario.gate = _gate(True, "ok")


@given(parsers.parse('the Countdown resolver identity is "{identity}"'))
def countdown_resolver_identity(countdown_scenario: CountdownScenario, identity: str) -> None:
    countdown_scenario.resolver_identity = identity


@given(parsers.parse("the Countdown live comment count is {comment_count:d}"))
def countdown_live_comment_count(countdown_scenario: CountdownScenario, comment_count: int) -> None:
    countdown_scenario.live_comment_counts["T1"] = comment_count


@when("the Countdown resolve loop runs")
def countdown_resolve_loop_runs(countdown_scenario: CountdownScenario) -> None:
    _run_countdown_loop(countdown_scenario)


@when(parsers.parse("the Countdown resolve loop runs with max_resolves {max_resolves:d}"))
def countdown_resolve_loop_runs_with_cap(
    countdown_scenario: CountdownScenario, max_resolves: int
) -> None:
    _run_countdown_loop(countdown_scenario, max_resolves=max_resolves)


@when("the Countdown resolve loop runs in dry-run mode")
def countdown_resolve_loop_runs_dry_run(countdown_scenario: CountdownScenario) -> None:
    _run_countdown_loop(countdown_scenario, dry_run=True)


@when("the Countdown resolve loop is attempted through the real resolver path")
def countdown_resolve_loop_real_path(countdown_scenario: CountdownScenario) -> None:
    _run_countdown_loop(countdown_scenario, real_resolver_path=True)


@then("no Countdown resolve mutation occurs")
def no_countdown_resolve_mutation(countdown_scenario: CountdownScenario) -> None:
    assert countdown_scenario.resolve_attempts == []


@then(parsers.parse("Countdown records {count:d} resolve mutation"))
@then(parsers.parse("Countdown records {count:d} resolve mutations"))
def countdown_records_resolve_mutations(countdown_scenario: CountdownScenario, count: int) -> None:
    assert len(countdown_scenario.resolve_attempts) == count


@then(parsers.parse('Countdown records decision action "{action}"'))
def countdown_records_decision_action(countdown_scenario: CountdownScenario, action: str) -> None:
    actions = [decision.action for decision in _summary(countdown_scenario).decisions]
    assert action in actions


@then("Countdown records the run as capped")
def countdown_records_run_as_capped(countdown_scenario: CountdownScenario) -> None:
    assert _summary(countdown_scenario).capped is True


@then(parsers.parse("the Countdown gate was called {count:d} times"))
def countdown_gate_was_called(countdown_scenario: CountdownScenario, count: int) -> None:
    assert countdown_scenario.gate is not None
    assert len(countdown_scenario.gate.seen) == count


@then("Countdown refuses the run before resolving")
def countdown_refuses_run_before_resolving(countdown_scenario: CountdownScenario) -> None:
    from voyager.core.resolve_conversation import ResolveConversationError

    assert isinstance(countdown_scenario.raised, ResolveConversationError)
    assert countdown_scenario.resolve_attempts == []
