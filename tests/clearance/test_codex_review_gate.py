"""Tests for the codex-review gate on clearance_ready_for_approval (Stage 3).

Operator-reported defect: Clearance requested the operator's review 9 seconds
after PR creation (order_system_django #71) — before Codex had reviewed
anything. The classifier treated "zero Codex review threads" as "review
clear" and jumped straight to clearance_ready_for_approval.

Decided rule: Stage 3 (and the review request it triggers) additionally
requires codex-reviewed-current-head evidence — at least one of:
  (a) a Codex REVIEW submission whose commit_id == head_sha;
  (b) a Codex clean-verdict PR comment whose "Reviewed commit:" value
      prefix-matches the current head;
  (c) >=1 Codex inline review thread exists on the PR.
Absent all three, status stays clearance_pending with a reason, and no
review request is made.
"""

from __future__ import annotations

from typing import Any

import pytest

from voyager.bots.clearance.constants import reset_review_request_users_cache
from voyager.bots.clearance.evaluation import (
    codex_reviewed_current_head,
    enforce_codex_review_gate,
    evaluate_clearance_snapshot,
)

HEAD_SHA = "abc1234567890def"
OLD_SHA = "111111a2222222b3"


class _StubClient:
    """Minimal GitHubAppClient stub for enrich_clearance_route integration tests."""

    def __init__(
        self,
        *,
        pull_request_data: dict | None = None,
        reviews: list | None = None,
        review_threads: list | None = None,
        issue_comments: list | None = None,
    ) -> None:
        self._pr = pull_request_data or {
            "number": 71,
            "state": "open",
            "draft": False,
            "html_url": "https://github.test/pull/71",
            "head": {"sha": HEAD_SHA},
            "user": {"login": "pr-author"},
            "requested_reviewers": [],
        }
        self._reviews = reviews or []
        self._review_threads = review_threads or []
        self._issue_comments = issue_comments or []
        self.request_reviewers_calls: list[dict[str, Any]] = []

    async def pull_request(self, app_slug: str, repo: str, pr_number: int) -> dict:
        return self._pr

    async def pull_request_reviews(self, app_slug: str, repo: str, pr_number: int) -> list:
        return self._reviews

    async def pull_request_review_threads(self, app_slug: str, repo: str, pr_number: int) -> list:
        return self._review_threads

    async def issue_comments(self, app_slug: str, repo: str, issue_number: int) -> list:
        return self._issue_comments

    async def request_pull_request_reviewers(
        self, app_slug: str, repo: str, pull_number: int, reviewers: list[str]
    ) -> Any:
        self.request_reviewers_calls.append({"repo": repo, "reviewers": reviewers})
        return {"reviewers": reviewers}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _open_pr(*, head: str = HEAD_SHA) -> dict:
    return {
        "number": 71,
        "state": "open",
        "draft": False,
        "html_url": "https://github.test/pull/71",
        "head": {"sha": head},
        "user": {"login": "pr-author"},
    }


def _approval(*, commit_id: str = HEAD_SHA, login: str = "someone-else") -> dict:
    return {
        "state": "APPROVED",
        "commit_id": commit_id,
        "submitted_at": "2026-05-01T10:00:00Z",
        "user": {"login": login},
    }


def _codex_review(*, commit_id: str = HEAD_SHA, state: str = "COMMENTED") -> dict:
    return {
        "state": state,
        "commit_id": commit_id,
        "submitted_at": "2026-05-01T09:30:00Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }


def _clean_verdict_comment(
    *, reviewed_commit: str, login: str = "chatgpt-codex-connector[bot]"
) -> dict:
    return {
        "user": {"login": login},
        "created_at": "2026-05-01T09:45:00Z",
        "body": (
            f"Codex Review: didn't find any major issues.\n\nReviewed commit: `{reviewed_commit}`"
        ),
    }


def _codex_thread(*, resolved: bool = True) -> dict:
    return {
        "isResolved": resolved,
        "isOutdated": False,
        "comments": {
            "nodes": [
                {
                    "databaseId": 1,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": "nit: rename this variable",
                    "createdAt": "2026-05-01T09:00:00Z",
                }
            ]
        },
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()
    yield
    reset_review_request_users_cache()


# ---------------------------------------------------------------------------
# codex_reviewed_current_head — pure predicate
# ---------------------------------------------------------------------------


def test_no_codex_activity_is_not_reviewed() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_codex_review_submission_on_head_is_reviewed() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval(), _codex_review()],
        "review_threads": [],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is True


def test_codex_review_submission_on_old_head_is_not_reviewed() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval(), _codex_review(commit_id=OLD_SHA)],
        "review_threads": [],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_dismissed_codex_review_on_head_does_not_count() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval(), _codex_review(state="DISMISSED")],
        "review_threads": [],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_clean_verdict_comment_matching_head_is_reviewed() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [_clean_verdict_comment(reviewed_commit=HEAD_SHA)],
    }
    assert codex_reviewed_current_head(snapshot) is True


def test_clean_verdict_comment_for_old_head_is_not_reviewed() -> None:
    """Old-head clean verdict must NOT count as current-head evidence."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [_clean_verdict_comment(reviewed_commit=OLD_SHA)],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_clean_verdict_comment_from_non_codex_login_does_not_count() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [_clean_verdict_comment(reviewed_commit=HEAD_SHA, login="random-user")],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_codex_thread_existing_is_reviewed() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [_codex_thread(resolved=True)],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is True


def test_missing_head_sha_is_not_reviewed() -> None:
    snapshot = {
        "pull_request": {"number": 1, "head": {}},
        "reviews": [_codex_review(commit_id="")],
        "review_threads": [],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is False


# ---------------------------------------------------------------------------
# evaluate_clearance_snapshot — integration through the gate
# ---------------------------------------------------------------------------


def test_pr_open_no_codex_activity_stays_pending() -> None:
    """order_system_django #71 regression: no Codex activity ⇒ pending, not ready_for_approval."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_pending"
    assert ev["labels"]["add"] == ["clearance-1-pending"]
    reasons = ev["confidence"]["reasons"]
    assert any("codex" in r.lower() and "current head" in r.lower() for r in reasons), (
        f"Expected a Codex-current-head reason, got: {reasons}"
    )


def test_pr_open_no_codex_activity_reactions_are_eyes_not_plus_one() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
            "issue_comments": [],
        }
    )
    assert "+1" not in ev["reactions"]["add"]
    assert "eyes" in ev["reactions"]["add"]


def test_codex_review_on_head_reaches_stage_3() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(), _codex_review()],
            "review_threads": [],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_ready_for_approval"
    assert ev["labels"]["add"] == ["clearance-3-ready-for-approval"]


def test_clean_verdict_comment_reaches_stage_3() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
            "issue_comments": [_clean_verdict_comment(reviewed_commit=HEAD_SHA)],
        }
    )
    assert ev["status"] == "clearance_ready_for_approval"


def test_old_head_clean_verdict_stays_pending() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
            "issue_comments": [_clean_verdict_comment(reviewed_commit=OLD_SHA)],
        }
    )
    assert ev["status"] == "clearance_pending"


def test_resolved_codex_thread_reaches_stage_3() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [_codex_thread(resolved=True)],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_ready_for_approval"


def test_unresolved_codex_thread_stays_blocked_not_gated() -> None:
    """Existing blocked semantics win before the codex gate is even consulted."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [_codex_thread(resolved=False)],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_blocked"


def test_missing_snapshot_keys_default_to_no_evidence() -> None:
    """Snapshot without an issue_comments key (legacy caller) must not crash."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
        }
    )
    assert ev["status"] == "clearance_pending"


# ---------------------------------------------------------------------------
# enforce_codex_review_gate — no-op for other statuses
# ---------------------------------------------------------------------------


def test_gate_is_noop_for_clearance_ready() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="required-approver")],
            "review_threads": [],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_ready"


def test_gate_is_noop_for_clearance_blocked() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [
                {
                    "state": "CHANGES_REQUESTED",
                    "commit_id": HEAD_SHA,
                    "submitted_at": "2026-05-01T09:00:00Z",
                    "user": {"login": "alice"},
                }
            ],
            "review_threads": [],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_blocked"


def test_enforce_codex_review_gate_direct_demotion() -> None:
    base = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(), _codex_review()],
            "review_threads": [],
            "issue_comments": [],
        }
    )
    assert base["status"] == "clearance_ready_for_approval"

    no_evidence_snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
    }
    demoted = enforce_codex_review_gate(base, no_evidence_snapshot)
    assert demoted["status"] == "clearance_pending"
    assert demoted["labels"]["add"] == ["clearance-1-pending"]
    # The "awaiting configured reviewer" reason must not survive demotion —
    # it implies an operator review request is imminent, which is false here.
    assert not any(
        "Awaiting approval from configured reviewer" in r for r in demoted["confidence"]["reasons"]
    )


# ---------------------------------------------------------------------------
# enrich_clearance_route — end-to-end: no review request until Codex reviews
# ---------------------------------------------------------------------------


def _base_route() -> dict:
    return {
        "agent": "iterwheel-clearance",
        "kind": "clearance_readiness",
        "validation": {
            "pr_number": 71,
            "issue_number": 71,
            "status": "clearance_pending",
            "conclusion": "neutral",
            "base_ref": "main",
        },
        "writeback": {"dynamic": "clearance_readiness"},
    }


async def test_pr_open_scenario_no_review_request_dispatched() -> None:
    """order_system_django #71: PR just opened, zero Codex activity.

    Must stay clearance_pending and must NOT request the operator's review.
    """
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[_approval()])
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_pending"
    assert client.request_reviewers_calls == []
    comment_body = result["writeback"]["comment_body"]
    assert "codex" in comment_body.lower()


async def test_codex_evidence_present_dispatches_review_request(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[_approval(), _codex_review()])
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_ready_for_approval"
    assert len(client.request_reviewers_calls) >= 1


async def test_swm_overlay_ready_status_without_codex_evidence_does_not_dispatch() -> None:
    """Reproduces the exact production bug path.

    ``automation["status"] == "ready"`` with reason "no Codex review threads
    on PR" is exactly what the SWM per-thread pipeline reports when Codex
    has not reviewed at all yet (zero Codex threads). The overlay
    (apply_swm_overlay) independently promotes to clearance_ready_for_approval
    from this automation status — enforce_codex_review_gate must catch and
    demote it even though evaluate_clearance_snapshot never ran that branch.
    """
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[])
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "no Codex review threads on PR",
        "sync_actions": [],
        "sync_actions_count": 0,
        "dry_run": False,
        "head_sha": HEAD_SHA,
    }
    result = await enrich_clearance_route(
        client, _base_route(), repository="iterwheel/voyager", automation=automation
    )
    assert result["validation"]["status"] == "clearance_pending", (
        f"Expected clearance_pending (demoted) but got {result['validation']['status']!r}"
    )
    assert client.request_reviewers_calls == []


async def test_swm_overlay_ready_status_with_codex_evidence_does_dispatch(monkeypatch) -> None:
    """Same SWM-ready overlay path, but Codex actually reviewed the head — should proceed."""
    monkeypatch.setenv("DRY_RUN", "false")
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[_codex_review()])
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [],
        "sync_actions_count": 0,
        "dry_run": False,
        "head_sha": HEAD_SHA,
    }
    result = await enrich_clearance_route(
        client, _base_route(), repository="iterwheel/voyager", automation=automation
    )
    assert result["validation"]["status"] == "clearance_ready_for_approval"
    assert len(client.request_reviewers_calls) >= 1
