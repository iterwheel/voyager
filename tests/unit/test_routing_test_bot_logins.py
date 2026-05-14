"""Unit tests for routing.py honoring VOYAGER_TEST_BOT_LOGINS.

PR #14 round-1 UNANIMOUS P1 (4/4 reviewers): the bypass was only applied to
voyager/bots/clearance/classify.py call sites, but webhook ingress in
voyager/bots/clearance/routing.py:48,59 filtered by the frozen
CODEX_REVIEW_BOT_LOGINS set — so test-bot webhooks were dropped before
classify could see them, making the bypass non-functional end-to-end.

Round 2 fix moves the identity helper to constants.py (is_codex_login) and
routes.py + classify.py both call through it. This file locks the fix at
the routing-layer boundary.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def reset_codex_login_cache(monkeypatch):
    """Clear is_codex_login's cache before/after each test."""
    from voyager.bots.clearance.constants import _extra_codex_logins

    monkeypatch.delenv("VOYAGER_TEST_BOT_LOGINS", raising=False)
    _extra_codex_logins.cache_clear()
    yield monkeypatch
    _extra_codex_logins.cache_clear()


# ---------------------------------------------------------------------------
# is_codex_review_result_comment
# ---------------------------------------------------------------------------


def _review_comment_payload(login: str, body: str = "Codex Review: foo") -> dict:
    return {"comment": {"user": {"login": login}, "body": body}}


def test_codex_review_result_comment_real_codex_login(reset_codex_login_cache) -> None:
    from voyager.bots.clearance.routing import is_codex_review_result_comment

    assert is_codex_review_result_comment(_review_comment_payload("chatgpt-codex-connector"))
    assert is_codex_review_result_comment(_review_comment_payload("chatgpt-codex-connector[bot]"))


def test_codex_review_result_comment_non_codex_rejected(reset_codex_login_cache) -> None:
    from voyager.bots.clearance.routing import is_codex_review_result_comment

    assert not is_codex_review_result_comment(_review_comment_payload("ryosaeba1985"))


def test_codex_review_result_comment_test_bot_accepted_when_env_set(
    reset_codex_login_cache,
) -> None:
    """Round-1 P1 regression — webhook ingress honors VOYAGER_TEST_BOT_LOGINS."""
    from voyager.bots.clearance.routing import is_codex_review_result_comment

    reset_codex_login_cache.setenv("VOYAGER_TEST_BOT_LOGINS", "voyager-e2e-bot")
    from voyager.bots.clearance.constants import _extra_codex_logins

    _extra_codex_logins.cache_clear()

    assert is_codex_review_result_comment(_review_comment_payload("voyager-e2e-bot"))


def test_codex_review_result_comment_test_bot_rejected_when_env_unset(
    reset_codex_login_cache,
) -> None:
    """Regression guard: without env, test-bot login behaves exactly as pre-PR."""
    from voyager.bots.clearance.routing import is_codex_review_result_comment

    assert not is_codex_review_result_comment(_review_comment_payload("voyager-e2e-bot"))


def test_codex_review_result_comment_body_prefix_still_required(reset_codex_login_cache) -> None:
    """Codex Review: body prefix gate is independent of identity bypass."""
    from voyager.bots.clearance.routing import is_codex_review_result_comment

    reset_codex_login_cache.setenv("VOYAGER_TEST_BOT_LOGINS", "voyager-e2e-bot")
    from voyager.bots.clearance.constants import _extra_codex_logins

    _extra_codex_logins.cache_clear()

    payload = _review_comment_payload("voyager-e2e-bot", body="not a Codex Review comment")
    assert not is_codex_review_result_comment(payload)


# ---------------------------------------------------------------------------
# is_codex_pr_body_reaction
# ---------------------------------------------------------------------------


def _reaction_payload(login: str, content: str = "+1") -> dict:
    return {
        "issue": {"pull_request": {"url": "https://api.github.com/.../pulls/1"}},
        "reaction": {"user": {"login": login}, "content": content},
    }


def test_codex_pr_body_reaction_real_codex_login(reset_codex_login_cache) -> None:
    from voyager.bots.clearance.routing import is_codex_pr_body_reaction

    assert is_codex_pr_body_reaction(_reaction_payload("chatgpt-codex-connector"))
    assert is_codex_pr_body_reaction(_reaction_payload("chatgpt-codex-connector[bot]", "eyes"))


def test_codex_pr_body_reaction_non_codex_rejected(reset_codex_login_cache) -> None:
    from voyager.bots.clearance.routing import is_codex_pr_body_reaction

    assert not is_codex_pr_body_reaction(_reaction_payload("ryosaeba1985"))


def test_codex_pr_body_reaction_test_bot_accepted_when_env_set(reset_codex_login_cache) -> None:
    """Round-1 P1 regression — reaction-event ingress honors VOYAGER_TEST_BOT_LOGINS."""
    from voyager.bots.clearance.routing import is_codex_pr_body_reaction

    reset_codex_login_cache.setenv("VOYAGER_TEST_BOT_LOGINS", "voyager-e2e-bot")
    from voyager.bots.clearance.constants import _extra_codex_logins

    _extra_codex_logins.cache_clear()

    assert is_codex_pr_body_reaction(_reaction_payload("voyager-e2e-bot"))


def test_codex_pr_body_reaction_test_bot_rejected_when_env_unset(reset_codex_login_cache) -> None:
    from voyager.bots.clearance.routing import is_codex_pr_body_reaction

    assert not is_codex_pr_body_reaction(_reaction_payload("voyager-e2e-bot"))


def test_codex_pr_body_reaction_content_filter_still_required(reset_codex_login_cache) -> None:
    """Bypass affects identity check only; reaction-content gate (+1 / eyes) survives."""
    from voyager.bots.clearance.routing import is_codex_pr_body_reaction

    reset_codex_login_cache.setenv("VOYAGER_TEST_BOT_LOGINS", "voyager-e2e-bot")
    from voyager.bots.clearance.constants import _extra_codex_logins

    _extra_codex_logins.cache_clear()

    payload = _reaction_payload("voyager-e2e-bot", content="laugh")
    assert not is_codex_pr_body_reaction(payload)


# ---------------------------------------------------------------------------
# is_codex_login direct (constants.py)
# ---------------------------------------------------------------------------


def test_is_codex_login_canonical_pair(reset_codex_login_cache) -> None:
    from voyager.bots.clearance.constants import is_codex_login

    assert is_codex_login("chatgpt-codex-connector")
    assert is_codex_login("chatgpt-codex-connector[bot]")
    assert not is_codex_login("ryosaeba1985")
    assert not is_codex_login(None)


def test_is_codex_login_emits_warning_when_env_set(reset_codex_login_cache, caplog) -> None:
    """Operational audit signal: env-set fires a logger.warning at first read."""
    import logging

    from voyager.bots.clearance.constants import _extra_codex_logins, is_codex_login

    reset_codex_login_cache.setenv("VOYAGER_TEST_BOT_LOGINS", "voyager-e2e-bot")
    _extra_codex_logins.cache_clear()

    with caplog.at_level(logging.WARNING, logger="voyager.bots.clearance.constants"):
        is_codex_login("voyager-e2e-bot")

    matches = [r for r in caplog.records if "VOYAGER_TEST_BOT_LOGINS is set" in r.message]
    assert matches, "expected logger.warning when VOYAGER_TEST_BOT_LOGINS is non-empty"
