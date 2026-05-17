"""Step definitions for SWM close_reason BDD scenarios."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/swm_close_reason.feature")


def _make_thread(
    *,
    thread_id: str = "PRRT_test",
    verdict: str = "RESOLVED",
    verdict_reason: str | None = None,
    llm_reason: str | None = None,
    llm_confidence: float | None = None,
):
    from voyager.bots.clearance.models import Severity, Thread, Verdict

    return Thread(
        id=thread_id,
        comment_id=1001,
        path="app.py",
        line=10,
        codex_severity=Severity.P2,
        effective_severity=Severity.P2,
        verdict=Verdict(verdict),
        verdict_reason=verdict_reason,
        llm_reason=llm_reason,
        llm_confidence=llm_confidence,
    )


# ---------------------------------------------------------------------------
# Given — marker helper scenarios
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a thread with id "{tid}" and head sha "{sha}"'),
    target_fixture="thread_and_sha",
)
def thread_with_id_and_sha(tid: str, sha: str) -> dict:
    return {"thread": _make_thread(thread_id=tid), "sha": sha}


@given(
    parsers.parse('a RESOLVED thread with id "{tid}" and head sha "{sha}"'),
    target_fixture="thread_and_sha",
)
def resolved_thread_with_id_sha(tid: str, sha: str) -> dict:
    return {"thread": _make_thread(thread_id=tid, verdict="RESOLVED"), "sha": sha}


@given(
    parsers.parse('an OPEN thread with id "{tid}" and head sha "{sha}"'),
    target_fixture="thread_and_sha",
)
def open_thread_with_id_sha(tid: str, sha: str) -> dict:
    return {"thread": _make_thread(thread_id=tid, verdict="OPEN"), "sha": sha}


# ---------------------------------------------------------------------------
# When — marker helpers
# ---------------------------------------------------------------------------


@when("conclusion_marker is called", target_fixture="marker")
def call_conclusion_marker(thread_and_sha: dict) -> str:
    from voyager.bots.clearance.close_reason import conclusion_marker

    return conclusion_marker(thread_and_sha["thread"], head_sha=thread_and_sha["sha"])


@when("close_reason_marker is called", target_fixture="close_marker")
def call_close_reason_marker(thread_and_sha: dict) -> str:
    from voyager.bots.clearance.close_reason import close_reason_marker

    return close_reason_marker(thread_and_sha["thread"], head_sha=thread_and_sha["sha"])


@when("existing_conclusion_markers is called", target_fixture="markers")
def call_existing_conclusion_markers(thread_and_sha: dict) -> list:
    from voyager.bots.clearance.close_reason import existing_conclusion_markers

    return existing_conclusion_markers(thread_and_sha["thread"], head_sha=thread_and_sha["sha"])


# ---------------------------------------------------------------------------
# Then — marker assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('the marker starts with "{prefix}"'))
def marker_starts_with(marker: str, prefix: str) -> None:
    assert marker.startswith(prefix), f"marker={marker!r} does not start with {prefix!r}"


@then(parsers.parse('the close reason marker starts with "{prefix}"'))
def close_marker_starts_with(close_marker: str, prefix: str) -> None:
    assert close_marker.startswith(prefix), (
        f"marker={close_marker!r} does not start with {prefix!r}"
    )


@then("the markers list contains the close-reason marker")
def markers_has_close_reason(thread_and_sha: dict, markers: list) -> None:
    from voyager.bots.clearance.close_reason import close_reason_marker

    expected = close_reason_marker(thread_and_sha["thread"], head_sha=thread_and_sha["sha"])
    assert expected in markers


@then("the markers list contains the conclusion marker")
def markers_has_conclusion(thread_and_sha: dict, markers: list) -> None:
    from voyager.bots.clearance.close_reason import conclusion_marker

    expected = conclusion_marker(thread_and_sha["thread"], head_sha=thread_and_sha["sha"])
    assert expected in markers


# ---------------------------------------------------------------------------
# Given — has_llm_close_reason scenarios
# ---------------------------------------------------------------------------


@given('a thread with llm_reason "diff removes token logging"', target_fixture="llm_thread")
def thread_with_llm_reason() -> dict:
    return {"thread": _make_thread(llm_reason="diff removes token logging"), "snapshot": None}


@given("a thread with no llm_reason", target_fixture="llm_thread")
def thread_no_llm_reason() -> dict:
    return {"thread": _make_thread(), "snapshot": None}


@when("has_llm_close_reason is called with no snapshot", target_fixture="llm_result")
def call_has_llm(llm_thread: dict) -> bool:
    from voyager.bots.clearance.close_reason import has_llm_close_reason

    return has_llm_close_reason(llm_thread["thread"], llm_thread["snapshot"])


@then("the llm close reason result is true")
def llm_result_true(llm_result: bool) -> None:
    assert llm_result is True


@then("the llm close reason result is false")
def llm_result_false(llm_result: bool) -> None:
    assert llm_result is False


# ---------------------------------------------------------------------------
# Given — build_thread_conclusion_comment scenarios
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a RESOLVED thread with a verdict_reason "{reason}"'),
    target_fixture="comment_thread",
)
def resolved_thread_with_reason(reason: str) -> dict:
    return {"thread": _make_thread(verdict="RESOLVED", verdict_reason=reason), "snapshot": None}


@given(
    parsers.parse('an OPEN thread with verdict_reason "{reason}"'),
    target_fixture="comment_thread",
)
def open_thread_with_reason(reason: str) -> dict:
    return {"thread": _make_thread(verdict="OPEN", verdict_reason=reason), "snapshot": None}


@given(
    parsers.parse('a RESOLVED thread with llm_reason "{llm_reason}" and llm_confidence {conf:f}'),
    target_fixture="comment_thread",
)
def resolved_thread_with_llm(llm_reason: str, conf: float) -> dict:
    return {
        "thread": _make_thread(verdict="RESOLVED", llm_reason=llm_reason, llm_confidence=conf),
        "snapshot": None,
    }


@given(
    parsers.parse(
        'a NEEDS_HUMAN_JUDGMENT thread with llm_reason "{llm_reason}" and llm_confidence {conf:f}'
    ),
    target_fixture="comment_thread",
)
def needs_human_judgment_thread_with_llm(llm_reason: str, conf: float) -> dict:
    return {
        "thread": _make_thread(
            verdict="NEEDS_HUMAN_JUDGMENT", llm_reason=llm_reason, llm_confidence=conf
        ),
        "snapshot": None,
    }


# ---------------------------------------------------------------------------
# When — build_thread_conclusion_comment
# ---------------------------------------------------------------------------


@when(
    parsers.parse('build_thread_conclusion_comment is called with head_sha "{sha}"'),
    target_fixture="conclusion_comment",
)
def call_build_conclusion_comment(comment_thread: dict, sha: str) -> str:
    from voyager.bots.clearance.close_reason import build_thread_conclusion_comment

    return build_thread_conclusion_comment(
        comment_thread["thread"], comment_thread["snapshot"], head_sha=sha
    )


@when(
    parsers.parse(
        'build_thread_conclusion_comment is called with head_sha "{sha}" and model "{model}"'
    ),
    target_fixture="conclusion_comment",
)
def call_build_conclusion_with_model(comment_thread: dict, sha: str, model: str) -> str:
    from voyager.bots.clearance.close_reason import build_thread_conclusion_comment

    return build_thread_conclusion_comment(
        comment_thread["thread"], comment_thread["snapshot"], head_sha=sha, model=model
    )


# ---------------------------------------------------------------------------
# Then — comment content
# ---------------------------------------------------------------------------


@then(parsers.parse('the comment contains "{text}"'))
def comment_contains(conclusion_comment: str, text: str) -> None:
    assert text in conclusion_comment, f"{text!r} not found in comment:\n{conclusion_comment}"


@then(parsers.parse('the comment does not contain "{text}"'))
def comment_does_not_contain(conclusion_comment: str, text: str) -> None:
    assert text not in conclusion_comment, f"{text!r} unexpectedly found in:\n{conclusion_comment}"


# ---------------------------------------------------------------------------
# build_close_reason_comment == build_thread_conclusion_comment
# ---------------------------------------------------------------------------


@when(
    "both close_reason and conclusion comments are built with the same inputs",
    target_fixture="both_comments",
)
def build_both_comments(comment_thread: dict) -> dict:
    from voyager.bots.clearance.close_reason import (
        build_close_reason_comment,
        build_thread_conclusion_comment,
    )

    sha = "abc1234def56"
    conclusion = build_thread_conclusion_comment(
        comment_thread["thread"], comment_thread["snapshot"], head_sha=sha
    )
    close = build_close_reason_comment(
        comment_thread["thread"], comment_thread["snapshot"], head_sha=sha
    )
    return {"conclusion": conclusion, "close": close}


@then("the close_reason comment equals the conclusion comment")
def close_equals_conclusion(both_comments: dict) -> None:
    assert both_comments["close"] == both_comments["conclusion"]
