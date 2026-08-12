"""Tests for evaluate_clearance_snapshot with numbered labels (issue #25).

Covers all 4 states:
- clearance_pending → clearance-1-pending
- clearance_blocked → clearance-2-blocked
- clearance_ready_for_approval → clearance-3-ready-for-approval (new)
- clearance_ready → clearance-4-ready-for-merge

Also covers:
- labels.remove includes ALL_CLEARANCE_LABELS except the one being added (migration)
- env-unset legacy path: any approval → clearance_ready
- env-set + configured user approved → clearance_ready
- env-set + configured user NOT approved, someone else approved → clearance_ready_for_approval
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _open_pr(*, draft: bool = False, state: str = "open", author: str = "pr-author") -> dict:
    return {
        "number": 42,
        "state": state,
        "draft": draft,
        "html_url": "https://github.test/pull/42",
        "head": {"sha": "head123"},
        "user": {"login": author},
    }


def _approval(*, commit_id: str = "head123", login: str = "reviewer") -> dict:
    return {
        "state": "APPROVED",
        "commit_id": commit_id,
        "submitted_at": "2026-05-01T10:00:00Z",
        "user": {"login": login},
    }


def _changes_requested(*, login: str = "reviewer") -> dict:
    return {
        "state": "CHANGES_REQUESTED",
        "commit_id": "head123",
        "submitted_at": "2026-05-01T09:00:00Z",
        "user": {"login": login},
    }


def _codex_review(*, commit_id: str = "head123") -> dict:
    """A Codex PR review on the given head — satisfies the codex-review gate."""
    return {
        "state": "COMMENTED",
        "commit_id": commit_id,
        "submitted_at": "2026-05-01T09:30:00Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.delenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", raising=False)
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    reset_review_request_users_cache()
    yield
    reset_review_request_users_cache()


# ---------------------------------------------------------------------------
# Helper: call evaluate_clearance_snapshot
# ---------------------------------------------------------------------------


def _evaluate(snapshot: dict) -> dict:
    from voyager.bots.clearance.evaluation import evaluate_clearance_snapshot

    return evaluate_clearance_snapshot(snapshot)


# ---------------------------------------------------------------------------
# Numbered labels — basic status / label / conclusion checks
# ---------------------------------------------------------------------------


def test_pending_status_uses_numbered_label() -> None:
    ev = _evaluate({"pull_request": _open_pr(), "reviews": [], "review_threads": []})
    assert ev["status"] == "clearance_pending"
    assert ev["labels"]["add"] == ["clearance-1-pending"]


def test_blocked_status_uses_numbered_label() -> None:
    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_changes_requested()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_blocked"
    assert ev["labels"]["add"] == ["clearance-2-blocked"]


def test_ready_no_env_uses_numbered_label() -> None:
    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready"
    assert ev["labels"]["add"] == ["clearance-4-ready-for-merge"]


# ---------------------------------------------------------------------------
# Migration: labels.remove always includes ALL_CLEARANCE_LABELS minus added label
# ---------------------------------------------------------------------------


def test_pending_remove_includes_all_clearance_labels_except_pending() -> None:
    from voyager.bots.clearance.constants import ALL_CLEARANCE_LABELS

    ev = _evaluate({"pull_request": _open_pr(), "reviews": [], "review_threads": []})
    remove = ev["labels"]["remove"]
    added = ev["labels"]["add"][0]
    expected_removed = [label for label in ALL_CLEARANCE_LABELS if label != added]
    for label in expected_removed:
        assert label in remove, f"Expected {label!r} in labels['remove'], got {remove!r}"


def test_blocked_remove_includes_all_clearance_labels_except_blocked() -> None:
    from voyager.bots.clearance.constants import ALL_CLEARANCE_LABELS

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_changes_requested()],
            "review_threads": [],
        }
    )
    remove = ev["labels"]["remove"]
    added = ev["labels"]["add"][0]
    for label in ALL_CLEARANCE_LABELS:
        if label != added:
            assert label in remove, f"Expected legacy/new label {label!r} in remove, got {remove!r}"


def test_ready_remove_includes_legacy_labels() -> None:
    from voyager.bots.clearance.constants import LEGACY_CLEARANCE_LABELS

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
        }
    )
    remove = ev["labels"]["remove"]
    for label in LEGACY_CLEARANCE_LABELS:
        assert label in remove, f"Legacy label {label!r} should be in remove for migration"


# ---------------------------------------------------------------------------
# Legacy path (env unset): any current-head approval → clearance_ready
# ---------------------------------------------------------------------------


def test_legacy_path_any_approval_gives_clearance_ready() -> None:
    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="random-approver"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready"
    assert ev["labels"]["add"] == ["clearance-4-ready-for-merge"]


def test_legacy_path_ready_reaction_is_plus_one() -> None:
    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(), _codex_review()],
            "review_threads": [],
        }
    )
    assert "+1" in ev["reactions"]["add"]
    assert "eyes" not in ev["reactions"]["add"]


def test_legacy_path_ready_summary() -> None:
    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["summary"] == "Clearance is ready for Countdown."


# ---------------------------------------------------------------------------
# New state: clearance_ready_for_approval
# Fires when: blocking_reviewers empty, unresolved_threads empty,
# current-head approval exists, env var non-empty, configured user NOT in current_approvals
# ---------------------------------------------------------------------------


def test_ready_for_approval_status(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="someone-else"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready_for_approval"


def test_ready_for_approval_label(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="someone-else"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["labels"]["add"] == ["clearance-3-ready-for-approval"]


def test_ready_for_approval_reaction_is_eyes(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="someone-else"), _codex_review()],
            "review_threads": [],
        }
    )
    assert "eyes" in ev["reactions"]["add"]
    assert "+1" not in ev["reactions"]["add"]


def test_ready_for_approval_conclusion_is_neutral(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="someone-else"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["conclusion"] == "neutral"


def test_ready_for_approval_summary(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="someone-else"), _codex_review()],
            "review_threads": [],
        }
    )
    assert "ready for human approval" in ev["summary"].lower()


def test_ready_for_approval_reason_mentions_configured_user(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="someone-else"), _codex_review()],
            "review_threads": [],
        }
    )
    reasons = ev["confidence"]["reasons"]
    assert any("required-approver" in r for r in reasons), (
        f"Expected configured user in reasons: {reasons}"
    )


def test_ready_for_approval_remove_includes_all_other_labels(monkeypatch) -> None:
    from voyager.bots.clearance.constants import (
        ALL_CLEARANCE_LABELS,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="someone-else"), _codex_review()],
            "review_threads": [],
        }
    )
    remove = ev["labels"]["remove"]
    added = ev["labels"]["add"][0]
    for label in ALL_CLEARANCE_LABELS:
        if label != added:
            assert label in remove, f"Expected {label!r} in labels['remove']"


# ---------------------------------------------------------------------------
# Configured approver HAS approved → clearance_ready (not ready_for_approval)
# ---------------------------------------------------------------------------


def test_configured_approver_approved_gives_clearance_ready(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="required-approver"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready"
    assert ev["labels"]["add"] == ["clearance-4-ready-for-merge"]


def test_configured_approver_approved_summary(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="required-approver"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["summary"] == "Clearance is ready for Countdown."


# ---------------------------------------------------------------------------
# Regression: env-set but blockers still present → clearance_blocked / clearance_pending
# ---------------------------------------------------------------------------


def test_env_set_with_blocking_reviewer_gives_clearance_blocked(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_changes_requested(login="alice")],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_blocked"


# ---------------------------------------------------------------------------
# Case-insensitive configured-approver match (Trinity round-2 findings)
# ---------------------------------------------------------------------------


def test_configured_approver_match_is_case_insensitive(monkeypatch) -> None:
    """Env 'Frankyxhl' (mixed) + approval by 'frankyxhl' (lower) → clearance_ready."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "Frankyxhl")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="frankyxhl"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready"
    assert ev["labels"]["add"] == ["clearance-4-ready-for-merge"]


def test_configured_approver_reverse_case(monkeypatch) -> None:
    """Env 'frankyxhl' (lower) + approval by 'Frankyxhl' (mixed) → clearance_ready."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="Frankyxhl"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready"
    assert ev["labels"]["add"] == ["clearance-4-ready-for-merge"]


# ---------------------------------------------------------------------------
# Bootstrap routing: env set + no approvals yet → clearance_ready_for_approval
# These tests are RED until the evaluation.py branch-chain is fixed so that
# "No approval on the current PR head." does NOT fire the `elif reasons:` branch
# when env is set and the PR is open with no blockers.
# ---------------------------------------------------------------------------


def test_ready_for_approval_when_env_set_and_no_approvals(monkeypatch) -> None:
    """Open PR, env set, no reviews at all → clearance_ready_for_approval (not clearance_pending)."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready_for_approval", (
        f"Expected clearance_ready_for_approval but got {ev['status']!r}"
    )
    assert ev["labels"]["add"] == ["clearance-3-ready-for-approval"], (
        f"Expected ['clearance-3-ready-for-approval'] but got {ev['labels']['add']!r}"
    )


def test_ready_for_approval_when_env_set_and_stale_approval_only(monkeypatch) -> None:
    """Open PR, env set, one stale approval only → clearance_ready_for_approval (not clearance_pending)."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(commit_id="stale-sha", login="alice"), _codex_review()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_ready_for_approval", (
        f"Expected clearance_ready_for_approval but got {ev['status']!r}"
    )
    assert ev["labels"]["add"] == ["clearance-3-ready-for-approval"], (
        f"Expected ['clearance-3-ready-for-approval'] but got {ev['labels']['add']!r}"
    )


def test_pending_when_env_set_but_pr_draft(monkeypatch) -> None:
    """Draft PR + env set + no approvals → clearance_pending (draft is a hard preempt)."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(draft=True),
            "reviews": [],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_pending", (
        f"Expected clearance_pending for draft PR but got {ev['status']!r}"
    )
    assert ev["labels"]["add"] == ["clearance-1-pending"], (
        f"Expected ['clearance-1-pending'] but got {ev['labels']['add']!r}"
    )


def test_pending_when_env_set_but_pr_closed(monkeypatch) -> None:
    """Closed PR + env set + no approvals → clearance_pending (closed is a hard preempt)."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(state="closed"),
            "reviews": [],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_pending", (
        f"Expected clearance_pending for closed PR but got {ev['status']!r}"
    )
    assert ev["labels"]["add"] == ["clearance-1-pending"], (
        f"Expected ['clearance-1-pending'] but got {ev['labels']['add']!r}"
    )


def test_blocked_still_wins_over_ready_for_approval(monkeypatch) -> None:
    """Open PR, env set, no current approvals, but one unresolved thread → clearance_blocked."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    ev = _evaluate(
        {
            "pull_request": _open_pr(),
            "reviews": [],
            "review_threads": [{"isResolved": False, "comments": {"nodes": []}}],
        }
    )
    assert ev["status"] == "clearance_blocked", (
        f"Expected clearance_blocked but got {ev['status']!r}"
    )
    assert ev["labels"]["add"] == ["clearance-2-blocked"], (
        f"Expected ['clearance-2-blocked'] but got {ev['labels']['add']!r}"
    )
