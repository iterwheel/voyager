"""Tests for new constants in voyager.bots.clearance.constants (issue #25).

Covers:
- CLEARANCE_PENDING_LABEL, CLEARANCE_BLOCKED_LABEL, CLEARANCE_READY_FOR_APPROVAL_LABEL,
  CLEARANCE_READY_LABEL — new numbered values
- CLEARANCE_LABELS — 4-item tuple in stage order
- LEGACY_CLEARANCE_LABELS — 3-item tuple of old unnumbered names
- ALL_CLEARANCE_LABELS — union of both
- configured_review_request_users() — env parsing
- reset_review_request_users_cache() — test helper
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.delenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", raising=False)
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    reset_review_request_users_cache()
    yield
    reset_review_request_users_cache()


# ---------------------------------------------------------------------------
# Label constants — numbered values
# ---------------------------------------------------------------------------


def test_clearance_pending_label_is_numbered() -> None:
    from voyager.bots.clearance.constants import CLEARANCE_PENDING_LABEL

    assert CLEARANCE_PENDING_LABEL == "clearance-1-pending"


def test_clearance_blocked_label_is_numbered() -> None:
    from voyager.bots.clearance.constants import CLEARANCE_BLOCKED_LABEL

    assert CLEARANCE_BLOCKED_LABEL == "clearance-2-blocked"


def test_clearance_ready_for_approval_label_exists() -> None:
    from voyager.bots.clearance.constants import CLEARANCE_READY_FOR_APPROVAL_LABEL

    assert CLEARANCE_READY_FOR_APPROVAL_LABEL == "clearance-3-ready-for-approval"


def test_clearance_ready_label_is_numbered() -> None:
    from voyager.bots.clearance.constants import CLEARANCE_READY_LABEL

    assert CLEARANCE_READY_LABEL == "clearance-4-ready-for-merge"


# ---------------------------------------------------------------------------
# CLEARANCE_LABELS — 4-item tuple in stage order
# ---------------------------------------------------------------------------


def test_clearance_labels_has_four_items() -> None:
    from voyager.bots.clearance.constants import CLEARANCE_LABELS

    assert len(CLEARANCE_LABELS) == 4


def test_clearance_labels_stage_order() -> None:
    from voyager.bots.clearance.constants import (
        CLEARANCE_BLOCKED_LABEL,
        CLEARANCE_LABELS,
        CLEARANCE_PENDING_LABEL,
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
        CLEARANCE_READY_LABEL,
    )

    assert CLEARANCE_LABELS == (
        CLEARANCE_PENDING_LABEL,
        CLEARANCE_BLOCKED_LABEL,
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
        CLEARANCE_READY_LABEL,
    )


# ---------------------------------------------------------------------------
# LEGACY_CLEARANCE_LABELS — old unnumbered names
# ---------------------------------------------------------------------------


def test_legacy_clearance_labels_exists() -> None:
    from voyager.bots.clearance.constants import LEGACY_CLEARANCE_LABELS

    assert LEGACY_CLEARANCE_LABELS == ("clearance-pending", "clearance-blocked", "clearance-ready")


def test_legacy_clearance_labels_has_three_items() -> None:
    from voyager.bots.clearance.constants import LEGACY_CLEARANCE_LABELS

    assert len(LEGACY_CLEARANCE_LABELS) == 3


# ---------------------------------------------------------------------------
# ALL_CLEARANCE_LABELS — union
# ---------------------------------------------------------------------------


def test_all_clearance_labels_is_clearance_plus_legacy() -> None:
    from voyager.bots.clearance.constants import (
        ALL_CLEARANCE_LABELS,
        CLEARANCE_LABELS,
        LEGACY_CLEARANCE_LABELS,
    )

    assert ALL_CLEARANCE_LABELS == CLEARANCE_LABELS + LEGACY_CLEARANCE_LABELS


def test_all_clearance_labels_has_seven_items() -> None:
    from voyager.bots.clearance.constants import ALL_CLEARANCE_LABELS

    assert len(ALL_CLEARANCE_LABELS) == 7


# ---------------------------------------------------------------------------
# configured_review_request_users() — env parsing
# ---------------------------------------------------------------------------


def test_configured_review_request_users_empty_when_env_unset() -> None:
    from voyager.bots.clearance.constants import configured_review_request_users

    assert configured_review_request_users() == ()


def test_configured_review_request_users_single_user(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        configured_review_request_users,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()
    result = configured_review_request_users()
    assert result == ("frankyxhl",)


def test_configured_review_request_users_comma_separated(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        configured_review_request_users,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "alice,bob,carol")
    reset_review_request_users_cache()
    result = configured_review_request_users()
    assert set(result) == {"alice", "bob", "carol"}
    assert len(result) == 3


def test_configured_review_request_users_strips_whitespace(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        configured_review_request_users,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "  alice , bob  ")
    reset_review_request_users_cache()
    result = configured_review_request_users()
    assert set(result) == {"alice", "bob"}


def test_configured_review_request_users_drops_empty_parts(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        configured_review_request_users,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "alice,,bob,")
    reset_review_request_users_cache()
    result = configured_review_request_users()
    assert set(result) == {"alice", "bob"}
    assert len(result) == 2


def test_configured_review_request_users_all_whitespace_returns_empty(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        configured_review_request_users,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "  , ,  ")
    reset_review_request_users_cache()
    result = configured_review_request_users()
    assert result == ()


def test_configured_review_request_users_returns_tuple(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        configured_review_request_users,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "alice")
    reset_review_request_users_cache()
    result = configured_review_request_users()
    assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# reset_review_request_users_cache() — cache invalidation
# ---------------------------------------------------------------------------


def test_reset_review_request_users_cache_invalidates_cache(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        configured_review_request_users,
        reset_review_request_users_cache,
    )

    # First call with env unset → empty
    assert configured_review_request_users() == ()
    # Set env, clear cache
    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "newuser")
    reset_review_request_users_cache()
    # Now should pick up the new value
    assert "newuser" in configured_review_request_users()
