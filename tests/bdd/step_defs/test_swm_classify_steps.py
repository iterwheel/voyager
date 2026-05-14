"""Step definitions for SWM classify BDD scenarios."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/swm_classify.feature")

CODEX_LOGIN = "chatgpt-codex-connector"
# Both prefixes must be filtered from "author replies": SWM is the legacy
# prefix from sweeping-monk (PRs still in flight at rename time), Clearance
# is what voyager writes now. Tests assert both branches of
# _RECOGNIZED_CONCLUSION_PREFIXES in classify.py.
LEGACY_SWM_MARKER = "<!-- swm-thread-conclusion:PRRT_abc:deadbeef1234 -->\nLegacy conclusion..."
CLEARANCE_MARKER = (
    "<!-- clearance-thread-conclusion:PRRT_abc:deadbeef1234 -->\nClearance conclusion..."
)


def _comment(login: str, body: str = "...", *, db_id: int = 1) -> dict:
    return {"databaseId": db_id, "author": {"login": login}, "body": body}


def _thread(*, is_outdated: bool = False, comments: list[dict] | None = None) -> dict:
    return {
        "isOutdated": is_outdated,
        "comments": {"nodes": comments or []},
    }


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("the classify module is available")
def classify_available() -> None:
    from voyager.bots.clearance import classify  # noqa: F401


# ---------------------------------------------------------------------------
# classify_thread fixtures
# ---------------------------------------------------------------------------


@given("a thread with no author reply and not outdated", target_fixture="thread")
def thread_state_a() -> dict:
    return _thread(comments=[_comment(CODEX_LOGIN, "P2: fix this")])


@given("a thread that is outdated with an author reply", target_fixture="thread")
def thread_state_b_with_reply() -> dict:
    return _thread(
        is_outdated=True,
        comments=[_comment(CODEX_LOGIN, "P2: ..."), _comment("ryosaeba1985", "fixed", db_id=2)],
    )


@given("a thread with an author reply and not outdated", target_fixture="thread")
def thread_state_c() -> dict:
    return _thread(
        comments=[_comment(CODEX_LOGIN, "P2: ..."), _comment("ryosaeba1985", "fixed", db_id=2)]
    )


@when("the thread is classified", target_fixture="thread_state")
def classify_the_thread(thread: dict) -> str:
    from voyager.bots.clearance.classify import classify_thread

    return classify_thread(thread)


@then(parsers.parse('the thread state is "{state}"'))
def thread_state_equals(thread_state: str, state: str) -> None:
    assert thread_state == state


# ---------------------------------------------------------------------------
# is_codex_thread fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('a thread whose first comment author is "{login}"'), target_fixture="thread")
def thread_first_comment_login(login: str) -> dict:
    return _thread(comments=[_comment(login, "some comment")])


@given("a thread with no comments", target_fixture="thread")
def thread_no_comments() -> dict:
    return _thread()


@when("is_codex_thread is called", target_fixture="is_codex")
def call_is_codex_thread(thread: dict) -> bool:
    from voyager.bots.clearance.classify import is_codex_thread

    return is_codex_thread(thread)


@then("the result is true")
def result_true(is_codex: bool) -> None:
    assert is_codex is True


@then("the result is false")
def result_false(is_codex: bool) -> None:
    assert is_codex is False


# ---------------------------------------------------------------------------
# codex_comment_id fixtures
# ---------------------------------------------------------------------------


@given("a thread whose first comment has databaseId 42", target_fixture="thread")
def thread_first_comment_id_42() -> dict:
    return _thread(comments=[_comment(CODEX_LOGIN, "...", db_id=42)])


@when("codex_comment_id is called", target_fixture="comment_id")
def call_codex_comment_id(thread: dict):
    from voyager.bots.clearance.classify import codex_comment_id

    return codex_comment_id(thread)


@then(parsers.parse("the comment id is {cid:d}"))
def comment_id_equals(comment_id, cid: int) -> None:
    assert comment_id == cid


# ---------------------------------------------------------------------------
# latest_author_reply fixtures
# ---------------------------------------------------------------------------


@given("a thread with a human reply followed by a Codex follow-up", target_fixture="thread")
def thread_human_then_codex_followup() -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "initial", db_id=1),
            _comment("ryosaeba1985", "my fix", db_id=2),
            _comment(CODEX_LOGIN, "looks good", db_id=3),
        ]
    )


@given("a thread with a SWM marker comment followed by a human reply", target_fixture="thread")
def thread_swm_marker_then_human() -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "P1: serious issue", db_id=1),
            _comment("iterwheel-clearance[bot]", LEGACY_SWM_MARKER, db_id=9),
            _comment("ryosaeba1985", "fixed in abc123", db_id=10),
        ]
    )


@given(
    "a thread with a Clearance marker comment followed by a human reply",
    target_fixture="thread",
)
def thread_clearance_marker_then_human() -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "P1: serious issue", db_id=1),
            _comment("iterwheel-clearance[bot]", CLEARANCE_MARKER, db_id=9),
            _comment("ryosaeba1985", "fixed in abc123", db_id=10),
        ]
    )


@given("a thread with only a SWM marker comment and no human reply", target_fixture="thread")
def thread_only_swm_marker() -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "P1: serious issue", db_id=1),
            _comment("iterwheel-clearance[bot]", LEGACY_SWM_MARKER, db_id=7),
        ]
    )


@given("a thread with only a Clearance marker comment and no human reply", target_fixture="thread")
def thread_only_clearance_marker() -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "P1: serious issue", db_id=1),
            _comment("iterwheel-clearance[bot]", CLEARANCE_MARKER, db_id=7),
        ]
    )


@when("latest_author_reply is called", target_fixture="latest_reply")
def call_latest_author_reply(thread: dict):
    from voyager.bots.clearance.classify import latest_author_reply

    return latest_author_reply(thread)


@then(parsers.parse("the latest reply databaseId is {db_id:d}"))
def latest_reply_db_id(latest_reply, db_id: int) -> None:
    assert latest_reply is not None
    assert latest_reply["databaseId"] == db_id


@then("the latest author reply is None")
def latest_reply_is_none(latest_reply) -> None:
    assert latest_reply is None


# ---------------------------------------------------------------------------
# latest_codex_followup fixtures
# ---------------------------------------------------------------------------


@given("a thread with two Codex follow-up comments with ids 3 and 4", target_fixture="thread")
def thread_two_codex_followups() -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "initial", db_id=1),
            _comment("ryosaeba1985", "fix", db_id=2),
            _comment(CODEX_LOGIN, "first followup", db_id=3),
            _comment(CODEX_LOGIN, "second followup", db_id=4),
        ]
    )


@when("latest_codex_followup is called", target_fixture="codex_followup")
def call_latest_codex_followup(thread: dict):
    from voyager.bots.clearance.classify import latest_codex_followup

    return latest_codex_followup(thread)


@then(parsers.parse("the followup databaseId is {db_id:d}"))
def followup_db_id(codex_followup, db_id: int) -> None:
    assert codex_followup is not None
    assert codex_followup["databaseId"] == db_id


# ---------------------------------------------------------------------------
# codex_pr_body_signal fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('PR body reactions with THUMBS_UP from "{login}"'), target_fixture="reactions")
def reactions_thumbs_up(login: str) -> list:
    return [{"content": "THUMBS_UP", "user": {"login": login}}]


@given(parsers.parse('PR body reactions with EYES from "{login}"'), target_fixture="reactions")
def reactions_eyes(login: str) -> list:
    return [{"content": "EYES", "user": {"login": login}}]


@given(
    parsers.parse('PR body reactions with both EYES and THUMBS_UP from "{login}"'),
    target_fixture="reactions",
)
def reactions_both(login: str) -> list:
    return [
        {"content": "EYES", "user": {"login": login}},
        {"content": "THUMBS_UP", "user": {"login": login}},
    ]


@given("an empty PR body reactions list", target_fixture="reactions")
def reactions_empty() -> list:
    return []


@when("codex_pr_body_signal is called", target_fixture="signal")
def call_codex_pr_body_signal(reactions: list):
    from voyager.bots.clearance.classify import codex_pr_body_signal

    return codex_pr_body_signal(reactions)


@then(parsers.parse('the signal is "{expected}"'))
def signal_equals(signal, expected: str) -> None:
    assert signal == expected


@then("the signal is None")
def signal_is_none(signal) -> None:
    assert signal is None


# ---------------------------------------------------------------------------
# VOYAGER_TEST_BOT_LOGINS bypass fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _restore_test_bot_logins(monkeypatch):
    """Ensure VOYAGER_TEST_BOT_LOGINS is removed and cache is cleared per scenario.

    monkeypatch.setenv/delenv auto-restores on teardown. The lru_cache on
    ``_extra_codex_logins`` is process-lifetime by design (one-shot read at
    production startup), so tests that flip the env between scenarios call
    the public ``reset_test_bot_login_cache()`` helper (which also resets
    the one-shot warning guard, per Gemini r2 P3).
    """
    from voyager.bots.clearance.constants import reset_test_bot_login_cache

    monkeypatch.delenv("VOYAGER_TEST_BOT_LOGINS", raising=False)
    reset_test_bot_login_cache()
    yield monkeypatch
    reset_test_bot_login_cache()


@given(parsers.parse('VOYAGER_TEST_BOT_LOGINS env is set to "{value}"'))
def given_test_bot_env(_restore_test_bot_logins, value: str) -> None:
    from voyager.bots.clearance.constants import reset_test_bot_login_cache

    _restore_test_bot_logins.setenv("VOYAGER_TEST_BOT_LOGINS", value)
    reset_test_bot_login_cache()


@given('VOYAGER_TEST_BOT_LOGINS env is set to ""')
def given_test_bot_env_empty(_restore_test_bot_logins) -> None:
    from voyager.bots.clearance.constants import reset_test_bot_login_cache

    _restore_test_bot_logins.setenv("VOYAGER_TEST_BOT_LOGINS", "")
    reset_test_bot_login_cache()


@given("VOYAGER_TEST_BOT_LOGINS env is not set")
def given_test_bot_env_unset(_restore_test_bot_logins) -> None:
    # The fixture already cleared env + cache; this step exists for readability.
    pass


@given(
    parsers.parse("a thread with a test-bot follow-up comment id {db_id:d}"),
    target_fixture="thread",
)
def thread_with_test_bot_followup(db_id: int) -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "initial", db_id=1),
            _comment("ryosaeba1985", "fix", db_id=2),
            _comment("voyager-e2e-bot", "looks good", db_id=db_id),
        ]
    )


@given(
    parsers.parse("a thread with a test-bot reply followed by a human reply id {db_id:d}"),
    target_fixture="thread",
)
def thread_test_bot_then_human(db_id: int) -> dict:
    return _thread(
        comments=[
            _comment(CODEX_LOGIN, "initial", db_id=1),
            _comment("voyager-e2e-bot", "auto-comment from test bot", db_id=99),
            _comment("ryosaeba1985", "my real fix", db_id=db_id),
        ]
    )
