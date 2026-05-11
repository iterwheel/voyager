"""Step definitions for Clearance BDD scenarios."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — those modules
# don't have implementations yet, so top-level imports would crash pytest
# collection. Import lazily INSIDE step functions instead.

scenarios("../features/clearance.feature")


# ---------------------------------------------------------------------------
# Background / shared fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('the Clearance agent slug is "{slug}"'), target_fixture="clearance_agent_slug")
def clearance_agent_slug(slug: str) -> str:
    return slug


@given(parsers.parse('a webhook payload "{name}"'), target_fixture="payload")
def webhook_payload(webhook_fixture, name: str) -> dict:
    return webhook_fixture(name)


# ---------------------------------------------------------------------------
# When — event routing
# ---------------------------------------------------------------------------


@when(parsers.parse('Clearance receives the "{event}" event'), target_fixture="clearance_routes")
def receive_clearance_event(payload: dict, event: str) -> list:
    from voyager.bots.clearance import route_clearance_event  # lazy import — module empty

    return route_clearance_event(event, payload)


# ---------------------------------------------------------------------------
# Then — cardinality
# ---------------------------------------------------------------------------


@then("exactly one clearance route is produced")
def one_clearance_route(clearance_routes: list) -> None:
    assert len(clearance_routes) == 1


@then("no clearance routes are produced")
def no_clearance_routes(clearance_routes: list) -> None:
    assert clearance_routes == []


@then("exactly two clearance routes are produced")
def two_clearance_routes(clearance_routes: list) -> None:
    assert len(clearance_routes) == 2


# ---------------------------------------------------------------------------
# Then — route identity
# ---------------------------------------------------------------------------


@then("the clearance route targets the Clearance agent")
def clearance_route_targets_agent(clearance_routes: list, clearance_agent_slug: str) -> None:
    assert clearance_routes[0]["agent"] == clearance_agent_slug


@then('the clearance route kind is "clearance_readiness"')
def clearance_route_kind(clearance_routes: list) -> None:
    assert clearance_routes[0]["kind"] == "clearance_readiness"


@then(parsers.parse('the clearance route agent id is "{agent_id}"'))
def clearance_route_agent_id(clearance_routes: list, agent_id: str) -> None:
    assert clearance_routes[0]["agent_id"] == agent_id


@then(parsers.parse('the clearance route event is "{event}"'))
def clearance_route_event(clearance_routes: list, event: str) -> None:
    assert clearance_routes[0]["event"] == event


@then(parsers.parse('the clearance route action is "{action}"'))
def clearance_route_action(clearance_routes: list, action: str) -> None:
    assert clearance_routes[0]["action"] == action


# ---------------------------------------------------------------------------
# Then — route validation fields
# ---------------------------------------------------------------------------


@then("the clearance validation includes the PR number")
def clearance_validation_has_pr_number(clearance_routes: list) -> None:
    assert clearance_routes[0]["validation"]["pr_number"] is not None


@then("the clearance validation includes the base ref")
def clearance_validation_has_base_ref(clearance_routes: list) -> None:
    assert clearance_routes[0]["validation"]["base_ref"] is not None


@then(parsers.parse('the clearance validation status is "{status}"'))
def clearance_validation_status(clearance_routes: list, status: str) -> None:
    assert clearance_routes[0]["validation"]["status"] == status


@then(parsers.parse('the clearance validation conclusion is "{conclusion}"'))
def clearance_validation_conclusion(clearance_routes: list, conclusion: str) -> None:
    assert clearance_routes[0]["validation"]["conclusion"] == conclusion


@then(parsers.parse('the clearance route writeback is dynamic "{kind}"'))
def clearance_route_writeback_dynamic(clearance_routes: list, kind: str) -> None:
    assert clearance_routes[0]["writeback"]["dynamic"] == kind


# ---------------------------------------------------------------------------
# Snapshot building helpers (used by evaluate_clearance_snapshot scenarios)
# ---------------------------------------------------------------------------


def _open_pr(*, draft: bool = False, state: str = "open") -> dict:
    return {
        "number": 102,
        "state": state,
        "draft": draft,
        "html_url": "https://github.test/pull/102",
        "head": {"sha": "abc1234"},
    }


def _approval(*, commit_id: str = "abc1234", login: str = "reviewer") -> dict:
    return {
        "state": "APPROVED",
        "commit_id": commit_id,
        "submitted_at": "2026-05-10T10:00:00Z",
        "user": {"login": login},
    }


def _changes_requested(*, login: str = "reviewer") -> dict:
    return {
        "state": "CHANGES_REQUESTED",
        "commit_id": "abc1234",
        "submitted_at": "2026-05-10T09:00:00Z",
        "user": {"login": login},
    }


def _dismissed(*, login: str = "reviewer") -> dict:
    return {
        "state": "DISMISSED",
        "commit_id": "abc1234",
        "submitted_at": "2026-05-10T09:00:00Z",
        "user": {"login": login},
    }


def _unresolved_thread() -> dict:
    return {"isResolved": False}


def _outdated_unresolved_thread() -> dict:
    """Unresolved review thread on code that has since become outdated.

    GraphQL's PullRequestReviewThread carries `isOutdated` precisely so callers
    can distinguish stale conversations from live ones. Codex round 5 P2.
    """
    return {"isResolved": False, "isOutdated": True}


# ---------------------------------------------------------------------------
# Given — evaluate_clearance_snapshot scenarios
# ---------------------------------------------------------------------------


@given(
    "a clearance snapshot with an approved review on the current head", target_fixture="snapshot"
)
def snapshot_approved_current_head() -> dict:
    return {"pull_request": _open_pr(), "reviews": [_approval()], "review_threads": []}


@given("a clearance snapshot for a draft PR with an approval", target_fixture="snapshot")
def snapshot_draft_pr_with_approval() -> dict:
    return {"pull_request": _open_pr(draft=True), "reviews": [_approval()], "review_threads": []}


@given("a clearance snapshot for a closed PR with an approval", target_fixture="snapshot")
def snapshot_closed_pr_with_approval() -> dict:
    return {
        "pull_request": _open_pr(state="closed"),
        "reviews": [_approval()],
        "review_threads": [],
    }


@given("a clearance snapshot with no reviews", target_fixture="snapshot")
def snapshot_no_reviews() -> dict:
    return {"pull_request": _open_pr(), "reviews": [], "review_threads": []}


@given("a clearance snapshot with only a stale approval", target_fixture="snapshot")
def snapshot_stale_approval() -> dict:
    return {
        "pull_request": _open_pr(),
        "reviews": [_approval(commit_id="old1234")],
        "review_threads": [],
    }


@given("a clearance snapshot with a changes-requested review", target_fixture="snapshot")
def snapshot_changes_requested() -> dict:
    return {
        "pull_request": _open_pr(),
        "reviews": [_changes_requested()],
        "review_threads": [],
    }


@given("a clearance snapshot with an unresolved review thread", target_fixture="snapshot")
def snapshot_unresolved_thread() -> dict:
    return {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [_unresolved_thread()],
    }


@given(
    "a clearance snapshot with only an outdated unresolved review thread and a current approval",
    target_fixture="snapshot",
)
def snapshot_only_outdated_unresolved_thread() -> dict:
    return {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [_outdated_unresolved_thread()],
    }


@given(
    "a clearance snapshot where a reviewer re-approved after requesting changes",
    target_fixture="snapshot",
)
def snapshot_reapproved_after_changes_requested() -> dict:
    return {
        "pull_request": _open_pr(),
        "reviews": [
            _changes_requested(login="reviewer"),
            {
                "state": "APPROVED",
                "commit_id": "abc1234",
                "submitted_at": "2026-05-10T11:00:00Z",
                "user": {"login": "reviewer"},
            },
        ],
        "review_threads": [],
    }


@given("a clearance snapshot with only a dismissed review", target_fixture="snapshot")
def snapshot_dismissed_review() -> dict:
    return {
        "pull_request": _open_pr(),
        "reviews": [_dismissed()],
        "review_threads": [],
    }


# ---------------------------------------------------------------------------
# When — evaluate_clearance_snapshot
# ---------------------------------------------------------------------------


@when("the clearance snapshot is evaluated", target_fixture="evaluation")
def evaluate_snapshot(snapshot: dict) -> dict:
    from voyager.bots.clearance import evaluate_clearance_snapshot  # lazy import

    return evaluate_clearance_snapshot(snapshot)


# ---------------------------------------------------------------------------
# Then — evaluation results
# ---------------------------------------------------------------------------


@then(parsers.parse('the evaluation status is "{status}"'))
def evaluation_status(evaluation: dict, status: str) -> None:
    assert evaluation["status"] == status


@then(parsers.parse('the evaluation conclusion is "{conclusion}"'))
def evaluation_conclusion(evaluation: dict, conclusion: str) -> None:
    assert evaluation["conclusion"] == conclusion


@then(parsers.parse('the evaluation summary is "{summary}"'))
def evaluation_summary(evaluation: dict, summary: str) -> None:
    assert evaluation["summary"] == summary


@then("the evaluation confidence has no reasons")
def evaluation_no_reasons(evaluation: dict) -> None:
    assert evaluation["confidence"]["reasons"] == []


@then(parsers.parse('the evaluation reasons include "{text}"'))
def evaluation_reasons_include(evaluation: dict, text: str) -> None:
    reasons = evaluation["confidence"]["reasons"]
    assert any(text in r for r in reasons), f"{text!r} not found in reasons: {reasons}"


@then(parsers.parse('the evaluation reasons exclude "{text}"'))
def evaluation_reasons_exclude(evaluation: dict, text: str) -> None:
    reasons = evaluation["confidence"]["reasons"]
    assert not any(text in r for r in reasons), f"{text!r} unexpectedly found in reasons: {reasons}"


@then(parsers.parse('the evaluation reactions add "{content}"'))
def evaluation_reactions_add(evaluation: dict, content: str) -> None:
    assert content in evaluation["reactions"]["add"]


@then(parsers.parse('the evaluation reactions remove "{content}"'))
def evaluation_reactions_remove(evaluation: dict, content: str) -> None:
    assert content in evaluation["reactions"]["remove"]


@then(parsers.parse('the evaluation labels add "{label}"'))
def evaluation_labels_add(evaluation: dict, label: str) -> None:
    assert label in evaluation["labels"]["add"]


@then(parsers.parse('the evaluation labels remove "{label}"'))
def evaluation_labels_remove(evaluation: dict, label: str) -> None:
    assert label in evaluation["labels"]["remove"]


@then("the evaluation review state has blocking reviewers")
def evaluation_has_blocking_reviewers(evaluation: dict) -> None:
    assert evaluation["review_state"]["blocking_reviewers"] != []


@then("the evaluation review state has no blocking reviewers")
def evaluation_no_blocking_reviewers(evaluation: dict) -> None:
    assert evaluation["review_state"]["blocking_reviewers"] == []


@then("the evaluation review state stale approvals is not empty")
def evaluation_stale_approvals_not_empty(evaluation: dict) -> None:
    assert evaluation["review_state"]["stale_approvals"] != []


# ---------------------------------------------------------------------------
# Given — apply_swm_overlay scenarios
# ---------------------------------------------------------------------------


def _ready_evaluation() -> dict:
    """Minimal clearance_ready evaluation for overlay tests."""
    from voyager.bots.clearance import evaluate_clearance_snapshot  # lazy import

    return evaluate_clearance_snapshot(
        {"pull_request": _open_pr(), "reviews": [_approval()], "review_threads": []}
    )


@given("a ready evaluation and no automation", target_fixture="overlay_inputs")
def overlay_inputs_no_automation() -> dict:
    return {"automation": None}


@given("a ready evaluation and automation with enabled false", target_fixture="overlay_inputs")
def overlay_inputs_disabled() -> dict:
    return {"automation": {"enabled": False, "status": "pending"}}


@given(
    parsers.parse('a ready evaluation and automation with status "{status}" and enabled true'),
    target_fixture="overlay_inputs",
)
def overlay_inputs_status(status: str) -> dict:
    return {"automation": {"enabled": True, "status": status}}


@given(
    parsers.parse(
        'a ready evaluation and automation with status "{status}" reason "{reason}" and enabled true'
    ),
    target_fixture="overlay_inputs",
)
def overlay_inputs_status_with_reason(status: str, reason: str) -> dict:
    return {"automation": {"enabled": True, "status": status, "reason": reason}}


@given(
    parsers.parse(
        'a ready evaluation and automation with status "{status}" error "{error}" and enabled true'
    ),
    target_fixture="overlay_inputs",
)
def overlay_inputs_status_with_error(status: str, error: str) -> dict:
    return {"automation": {"enabled": True, "status": status, "error": error}}


# ---------------------------------------------------------------------------
# When — apply_swm_overlay
# ---------------------------------------------------------------------------


@when("the swm overlay is applied", target_fixture="overlaid")
def apply_overlay(overlay_inputs: dict) -> dict:
    from voyager.bots.clearance import apply_swm_overlay  # lazy import

    base_eval = _ready_evaluation()
    return {
        "original": base_eval,
        "result": apply_swm_overlay(base_eval, overlay_inputs["automation"]),
    }


# ---------------------------------------------------------------------------
# Then — overlay results
# ---------------------------------------------------------------------------


@then("the overlaid evaluation is identical to the original")
def overlaid_identical(overlaid: dict) -> None:
    assert overlaid["result"] is overlaid["original"] or overlaid["result"] == overlaid["original"]


@then(parsers.parse('the overlaid evaluation status is still "{status}"'))
def overlaid_status_still(overlaid: dict, status: str) -> None:
    assert overlaid["result"]["status"] == status


@then(parsers.parse('the overlaid evaluation status is "{status}"'))
def overlaid_status(overlaid: dict, status: str) -> None:
    assert overlaid["result"]["status"] == status


@then(parsers.parse('the overlaid evaluation conclusion is "{conclusion}"'))
def overlaid_conclusion(overlaid: dict, conclusion: str) -> None:
    assert overlaid["result"]["conclusion"] == conclusion


@then(parsers.parse('the overlaid evaluation reactions add "{content}"'))
def overlaid_reactions_add(overlaid: dict, content: str) -> None:
    assert content in overlaid["result"]["reactions"]["add"]


@then(parsers.parse('the overlaid evaluation reactions remove "{content}"'))
def overlaid_reactions_remove(overlaid: dict, content: str) -> None:
    assert content in overlaid["result"]["reactions"]["remove"]


@then(parsers.parse('the overlaid evaluation labels add "{label}"'))
def overlaid_labels_add(overlaid: dict, label: str) -> None:
    assert label in overlaid["result"]["labels"]["add"]


@then("the overlaid evaluation confidence reasons include the automation engine reason")
def overlaid_reasons_include_automation(overlaid: dict) -> None:
    reasons = overlaid["result"]["confidence"]["reasons"]
    assert any("Clearance automation engine" in r for r in reasons), (
        f"automation engine reason not found: {reasons}"
    )


@then(parsers.parse('the overlaid evaluation confidence reasons include "{text}"'))
def overlaid_reasons_include_text(overlaid: dict, text: str) -> None:
    reasons = overlaid["result"]["confidence"]["reasons"]
    assert any(text in r for r in reasons), f"{text!r} not found in reasons: {reasons}"


# ---------------------------------------------------------------------------
# Given — Codex follow-up scheduling scenarios
# ---------------------------------------------------------------------------


def _base_pending_route(*, signal: str = "reviewing", pr_number: int = 145) -> dict:
    return {
        "agent": "iterwheel-clearance",
        "event": "check_suite",
        "action": "completed",
        "validation": {
            "status": "clearance_pending",
            "conclusion": "neutral",
            "pr_number": pr_number,
            "issue_number": pr_number,
            "base_ref": "main",
        },
        "automation": {
            "swm_clearance": {
                "enabled": True,
                "status": "pending",
                "poll": {"codex_pr_body_signal": signal},
            }
        },
    }


@given(
    parsers.parse('a clearance route with codex_pr_body_signal "{signal}" and status pending'),
    target_fixture="pending_route",
)
def pending_route_with_signal(signal: str) -> dict:
    return _base_pending_route(signal=signal)


@given(
    'a clearance route with wrong agent slug and codex_pr_body_signal "reviewing"',
    target_fixture="pending_route",
)
def pending_route_wrong_agent() -> dict:
    route = _base_pending_route()
    route["agent"] = "some-other-agent"
    return route


@given(
    'a clearance route with direct codex_pr_body_signal "reviewing"', target_fixture="pending_route"
)
def pending_route_direct_signal() -> dict:
    return {
        "agent": "iterwheel-clearance",
        "validation": {"status": "clearance_pending", "pr_number": 145},
        "automation": {
            "swm_clearance": {
                "enabled": True,
                "status": "pending",
                "codex_pr_body_signal": "reviewing",
            }
        },
    }


@given(
    'a clearance route with poll codex_pr_body_signal "approved"', target_fixture="pending_route"
)
def pending_route_poll_signal() -> dict:
    return {
        "agent": "iterwheel-clearance",
        "validation": {"status": "clearance_pending", "pr_number": 145},
        "automation": {
            "swm_clearance": {
                "enabled": True,
                "status": "pending",
                "poll": {"codex_pr_body_signal": "approved"},
            }
        },
    }


@given("a clearance route with no codex_pr_body_signal", target_fixture="pending_route")
def pending_route_no_signal() -> dict:
    return {
        "agent": "iterwheel-clearance",
        "validation": {"status": "clearance_pending", "pr_number": 145},
        "automation": {"swm_clearance": {"enabled": True, "status": "pending"}},
    }


# ---------------------------------------------------------------------------
# When — Codex follow-up scheduling
# ---------------------------------------------------------------------------


@when("the codex reaction wait state is checked", target_fixture="wait_state")
def check_wait_state(pending_route: dict) -> bool:
    from voyager.bots.clearance import clearance_waiting_on_codex_pr_body_reaction  # lazy import

    return clearance_waiting_on_codex_pr_body_reaction(pending_route)


@when(
    parsers.parse('the follow-up schedule decision is evaluated for event "{event}"'),
    target_fixture="schedule_decision",
)
def check_schedule_decision(pending_route: dict, event: str) -> bool:
    from voyager.bots.clearance import should_schedule_codex_reaction_follow_up  # lazy import

    return should_schedule_codex_reaction_follow_up(event, pending_route)


@when("the codex reaction follow-up route is built", target_fixture="follow_up_route")
def build_follow_up(pending_route: dict) -> dict:
    from voyager.bots.clearance import build_codex_reaction_follow_up_route  # lazy import

    return build_codex_reaction_follow_up_route(pending_route)


@when("the codex pr body signal is extracted", target_fixture="extracted_signal")
def extract_signal(pending_route: dict) -> str | None:
    from voyager.bots.clearance import clearance_swm_codex_pr_body_signal  # lazy import

    return clearance_swm_codex_pr_body_signal(pending_route)


# ---------------------------------------------------------------------------
# Then — Codex follow-up scheduling results
# ---------------------------------------------------------------------------


@then("the route is waiting on codex pr body reaction")
def route_is_waiting(wait_state: bool) -> None:
    assert wait_state is True


@then("the route is not waiting on codex pr body reaction")
def route_is_not_waiting(wait_state: bool) -> None:
    assert wait_state is False


@then("a codex reaction follow-up should be scheduled")
def follow_up_should_be_scheduled(schedule_decision: bool) -> None:
    assert schedule_decision is True


@then("a codex reaction follow-up should not be scheduled")
def follow_up_should_not_be_scheduled(schedule_decision: bool) -> None:
    assert schedule_decision is False


@then(parsers.parse('the follow-up route event is "{event}"'))
def follow_up_event(follow_up_route: dict, event: str) -> None:
    assert follow_up_route["event"] == event


@then(parsers.parse('the follow-up route action is "{action}"'))
def follow_up_action(follow_up_route: dict, action: str) -> None:
    assert follow_up_route["action"] == action


@then("the follow-up route preserves the PR number")
def follow_up_preserves_pr_number(pending_route: dict, follow_up_route: dict) -> None:
    assert follow_up_route["validation"]["pr_number"] == pending_route["validation"]["pr_number"]


@then("the follow-up route has no automation key")
def follow_up_no_automation(follow_up_route: dict) -> None:
    assert "automation" not in follow_up_route


@then(parsers.parse('the follow-up route writeback is dynamic "{kind}"'))
def follow_up_writeback_dynamic(follow_up_route: dict, kind: str) -> None:
    assert follow_up_route["writeback"]["dynamic"] == kind


# ---------------------------------------------------------------------------
# Then — signal extraction results
# ---------------------------------------------------------------------------


@then(parsers.parse('the extracted signal is "{signal}"'))
def extracted_signal_equals(extracted_signal: str | None, signal: str) -> None:
    assert extracted_signal == signal


@then("the extracted signal is None")
def extracted_signal_is_none(extracted_signal: str | None) -> None:
    assert extracted_signal is None
