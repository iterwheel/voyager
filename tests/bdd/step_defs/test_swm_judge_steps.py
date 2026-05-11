"""Step definitions for SWM judge BDD scenarios."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/swm_judge.feature")

_SUBSTANTIVE_BODY = (
    "Verified + documented in c476c877. The foot-gun is real but not active today. "
    "Branch protection state on `main` checked via gh api repos/.../branches/main/protection."
)
_VAGUE_BODY = "yeah I think you're probably right about this one but I'm not totally sure honestly let me look more later"


# ---------------------------------------------------------------------------
# Given — judge inputs
# ---------------------------------------------------------------------------


@given("a state B thread with code_changed true", target_fixture="judge_inputs")
def state_b_code_changed() -> dict:
    return {
        "classification": "B",
        "author_reply_body": None,
        "code_changed": True,
        "codex_followup_body": None,
    }


@given("a state B thread with code_changed false", target_fixture="judge_inputs")
def state_b_no_code_change() -> dict:
    return {
        "classification": "B",
        "author_reply_body": None,
        "code_changed": False,
        "codex_followup_body": None,
    }


@given("a state C thread with a substantive author reply", target_fixture="judge_inputs")
def state_c_substantive() -> dict:
    return {
        "classification": "C",
        "author_reply_body": _SUBSTANTIVE_BODY,
        "code_changed": False,
        "codex_followup_body": None,
    }


@given(
    parsers.parse('a state C thread with a short ack reply "{body}"'), target_fixture="judge_inputs"
)
def state_c_short_ack(body: str) -> dict:
    return {
        "classification": "C",
        "author_reply_body": body,
        "code_changed": False,
        "codex_followup_body": None,
    }


@given("a state C thread with a long vague reply", target_fixture="judge_inputs")
def state_c_vague() -> dict:
    return {
        "classification": "C",
        "author_reply_body": _VAGUE_BODY,
        "code_changed": False,
        "codex_followup_body": None,
    }


@given("a state A thread with no author reply and no code change", target_fixture="judge_inputs")
def state_a_no_response() -> dict:
    return {
        "classification": "A",
        "author_reply_body": None,
        "code_changed": False,
        "codex_followup_body": None,
    }


@given(
    "a state C thread with a short reply and a positive Codex follow-up",
    target_fixture="judge_inputs",
)
def state_c_positive_followup() -> dict:
    return {
        "classification": "C",
        "author_reply_body": "thanks",
        "code_changed": False,
        "codex_followup_body": "Looks good, no new issues.",
    }


@given(
    "a state C thread with a substantive reply and a negative Codex follow-up",
    target_fixture="judge_inputs",
)
def state_c_negative_followup() -> dict:
    return {
        "classification": "C",
        "author_reply_body": _SUBSTANTIVE_BODY,
        "code_changed": False,
        "codex_followup_body": "Concern remains: the migration path is still missing.",
    }


@given(
    "a state A thread with no response but github_isResolved true", target_fixture="judge_inputs"
)
def state_a_github_resolved() -> dict:
    return {
        "classification": "A",
        "author_reply_body": None,
        "code_changed": False,
        "codex_followup_body": None,
        "github_isResolved": True,
    }


@given(
    "a state A thread with no response and github_isResolved false", target_fixture="judge_inputs"
)
def state_a_github_not_resolved() -> dict:
    return {
        "classification": "A",
        "author_reply_body": None,
        "code_changed": False,
        "codex_followup_body": None,
        "github_isResolved": False,
    }


# ---------------------------------------------------------------------------
# When — judge
# ---------------------------------------------------------------------------


@when("the thread is judged", target_fixture="decision")
def judge_thread(judge_inputs: dict):
    from voyager.bots.clearance.judge import judge

    return judge(**judge_inputs)


# ---------------------------------------------------------------------------
# Then — judge results
# ---------------------------------------------------------------------------


@then(parsers.parse('the verdict is "{verdict}"'))
def verdict_equals(decision, verdict: str) -> None:
    assert decision.verdict.value == verdict


@then(parsers.parse('the reason mentions "{text}"'))
def reason_mentions(decision, text: str) -> None:
    assert text.lower() in decision.reason.lower(), f"{text!r} not in {decision.reason!r}"


@then("the decision substantive flag is true")
def substantive_true(decision) -> None:
    assert decision.substantive is True


@then("the decision substantive flag is false")
def substantive_false(decision) -> None:
    assert decision.substantive is False


# ---------------------------------------------------------------------------
# is_substantive_reply helper scenarios
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a reply body with commit SHA "{sha}" and sufficient length'),
    target_fixture="reply_body",
)
def reply_with_sha(sha: str) -> str:
    return f"Verified + documented in {sha}. The foot-gun is real but not active today. Checked via gh api."


@given(parsers.parse('a short reply body "{body}"'), target_fixture="reply_body")
def short_reply(body: str) -> str:
    return body


@given("a None reply body", target_fixture="reply_body")
def none_reply() -> None:
    return None


@when("is_substantive_reply is called", target_fixture="substantive_result")
def call_is_substantive_reply(reply_body) -> bool:
    from voyager.bots.clearance.judge import is_substantive_reply

    return is_substantive_reply(reply_body)


@then("the substantive result is true")
def substantive_result_true(substantive_result: bool) -> None:
    assert substantive_result is True


@then("the substantive result is false")
def substantive_result_false(substantive_result: bool) -> None:
    assert substantive_result is False


# ---------------------------------------------------------------------------
# codex_followup_reaction helper scenarios
# ---------------------------------------------------------------------------


@given(parsers.parse('a Codex follow-up body "{body}"'), target_fixture="followup_body")
def followup_body_str(body: str) -> str:
    return body


@given("a None Codex follow-up body", target_fixture="followup_body")
def followup_body_none() -> None:
    return None


@when("codex_followup_reaction is called", target_fixture="followup_reaction")
def call_codex_followup_reaction(followup_body):
    from voyager.bots.clearance.judge import codex_followup_reaction

    return codex_followup_reaction(followup_body)


@then(parsers.parse('the followup reaction is "{reaction}"'))
def followup_reaction_equals(followup_reaction, reaction: str) -> None:
    assert followup_reaction == reaction


@then("the followup reaction is None")
def followup_reaction_is_none(followup_reaction) -> None:
    assert followup_reaction is None
