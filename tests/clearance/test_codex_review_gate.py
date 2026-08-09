"""Tests for the codex-review gate on Stage 3 (clearance_ready_for_approval)
and Stage 4 (clearance_ready).

Operator-reported defect: Clearance requested the operator's review 9 seconds
after PR creation (order_system_django #71) — before Codex had reviewed
anything. The classifier treated "zero Codex review threads" as "review
clear" and jumped straight to clearance_ready_for_approval.

Decided rule: reaching Stage 3 (and the review request it triggers) OR
Stage 4 additionally requires codex-reviewed-current-head evidence — at
least one of:
  (a) a Codex REVIEW submission whose commit_id == head_sha;
  (b) a Codex clean-verdict PR comment whose "Reviewed commit:" value
      prefix-matches the current head;
  (c) a Codex "+1" reaction on the PR body whose created_at is later than
      the current head's arrival timestamp (time-anchored, not
      head-anchored — a reaction carries no commit id).
Absent all three, status stays clearance_pending with a reason, and no
review request is made.

Round-2 review finding: a third evidence type — "a Codex inline review
thread exists on the PR" — was removed. Thread state cannot be reliably
head-anchored: a thread resolved on an old head still reads as "resolved"
after a push with no new Codex activity, and GitHub can re-anchor an old,
untouched review comment to carry the *new* commit id (VOY-1832 / TRN-1209:
`created_at`, not `commit_id`, is the only reliable comment-freshness key).
Whenever Codex reviews and leaves inline findings it necessarily also
submits a PR review — so (a) already covers that case without trusting
thread state.

Round-3 review finding: the gate only covered Stage 3. An operator who
approves the current head *before* Codex has reviewed it could still reach
Stage 4 (clearance_ready) directly — via evaluate_clearance_snapshot's own
early-return branch, or via apply_swm_overlay's independent promotion from
automation["status"] in {"ready", "ready_with_low_priority"} — and a merge
loop gating on "stage >= 3" would auto-merge a head Codex never saw.
enforce_codex_review_gate now gates both clearance_ready_for_approval and
clearance_ready.

Round-4 review finding (P2): the gate ignored Codex's PR-body "+1" reaction
clean signal — the same signal codex_review_watch._detect_signal's "thumbs"
branch already treats as clean, and the same signal the reaction webhook
already routes Clearance for (routing.py). A thumbs-only clean PR could
never reach Stage 3/4 through this gate. Added as evidence type (c), above,
anchored by time (a reaction has no commit id) against
GitHubAppClient.pull_request_head_updated_at — the same "current head
arrival" timestamp pipeline.py already uses for the identical staleness
problem on Codex issue-comment clean signals. Fails closed: no
head_updated_at means no reaction evidence, full stop.
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
HEAD_UPDATED_AT = "2026-05-01T09:00:00Z"  # when the current head arrived on the PR


class _StubClient:
    """Minimal GitHubAppClient stub for enrich_clearance_route integration tests."""

    def __init__(
        self,
        *,
        pull_request_data: dict | None = None,
        reviews: list | None = None,
        review_threads: list | None = None,
        issue_comments: list | None = None,
        reactions: list | None = None,
        head_updated_at: str | None = None,
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
        self._reactions = reactions or []
        self._head_updated_at = head_updated_at
        self.request_reviewers_calls: list[dict[str, Any]] = []

    async def pull_request(self, app_slug: str, repo: str, pr_number: int) -> dict:
        return self._pr

    async def pull_request_reviews(self, app_slug: str, repo: str, pr_number: int) -> list:
        return self._reviews

    async def pull_request_review_threads(self, app_slug: str, repo: str, pr_number: int) -> list:
        return self._review_threads

    async def issue_comments(self, app_slug: str, repo: str, issue_number: int) -> list:
        return self._issue_comments

    async def issue_reactions(self, app_slug: str, repo: str, issue_number: int) -> list:
        return self._reactions

    async def pull_request_head_updated_at(
        self, app_slug: str, repo: str, pull_number: int
    ) -> str | None:
        return self._head_updated_at

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


def _codex_thumbs_reaction(
    *, created_at: str, content: str = "+1", login: str = "chatgpt-codex-connector[bot]"
) -> dict:
    """A PR-body reaction, matching codex_review_watch._detect_signal's 'thumbs' branch."""
    return {"user": {"login": login}, "content": content, "created_at": created_at}


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


def _codex_thread(*, resolved: bool = True, outdated: bool = False) -> dict:
    return {
        "isResolved": resolved,
        "isOutdated": outdated,
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


# ---------------------------------------------------------------------------
# Round-4 P2: Codex thumbs-up PR-body reaction (time-anchored, not head-anchored)
# ---------------------------------------------------------------------------


def test_thumbs_reaction_after_head_arrival_is_reviewed() -> None:
    """codex_review_watch's 'thumbs' clean signal, mirrored: a +1 reaction
    from Codex posted AFTER the current head arrived is sufficient evidence."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
        "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z")],
        "head_updated_at": HEAD_UPDATED_AT,
    }
    assert codex_reviewed_current_head(snapshot) is True


def test_thumbs_reaction_before_head_arrival_is_not_reviewed() -> None:
    """Stale thumbs from an OLD head: the reaction predates head_updated_at,
    so it is not evidence for the current head — Codex reacted before the
    latest push, never re-reviewed since."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
        "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T08:00:00Z")],
        "head_updated_at": HEAD_UPDATED_AT,
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_thumbs_reaction_without_head_updated_at_fails_closed() -> None:
    """FAIL CLOSED: no head_updated_at available at all -> reaction evidence
    never fires, even though the reaction's own timestamp would otherwise
    qualify."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
        "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z")],
        "head_updated_at": None,
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_thumbs_reaction_missing_snapshot_key_fails_closed() -> None:
    """Same fail-closed behavior when head_updated_at is entirely absent from
    the snapshot (legacy caller that hasn't been updated to fetch it)."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
        "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z")],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_thumbs_reaction_from_non_codex_login_does_not_count() -> None:
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
        "reactions": [
            _codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z", login="random-user")
        ],
        "head_updated_at": HEAD_UPDATED_AT,
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_eyes_reaction_does_not_count_as_clean_signal() -> None:
    """'eyes' means Codex is still reviewing, not a clean verdict — only '+1'
    counts, matching codex_review_watch._detect_signal's thumbs check."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [],
        "issue_comments": [],
        "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z", content="eyes")],
        "head_updated_at": HEAD_UPDATED_AT,
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_resolved_codex_thread_alone_is_not_reviewed() -> None:
    """Round-2 fix: thread evidence was removed entirely. A resolved Codex
    thread with no head-anchored Codex review/comment must NOT count — thread
    resolution proves the finding was addressed as of *some* head, not
    necessarily the current one (see module docstring)."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval()],
        "review_threads": [_codex_thread(resolved=True)],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_head_anchored_review_plus_resolved_thread_is_reviewed() -> None:
    """Positive case: a head-anchored Codex review submission is sufficient
    evidence on its own, regardless of thread state alongside it."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [_approval(), _codex_review()],
        "review_threads": [_codex_thread(resolved=True)],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is True


def test_outdated_unresolved_codex_thread_is_not_reviewed() -> None:
    """Thread evidence never counts (round-2), so this old Critical-fix repro
    still holds trivially: outdated+unresolved is exempt from BLOCKED (its
    anchor code changed) but is not current-head Codex review evidence."""
    snapshot = {
        "pull_request": _open_pr(),
        "reviews": [],
        "review_threads": [_codex_thread(resolved=False, outdated=True)],
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is False


def test_thread_resolved_on_old_head_then_pushed_is_not_reviewed() -> None:
    """Round-2 reproduction: Codex thread resolved while head was commit A;
    author pushes commit B with no new Codex activity at all. Thread state
    alone must not satisfy the gate for the new head B."""
    snapshot = {
        "pull_request": _open_pr(head=HEAD_SHA),  # current head is B (HEAD_SHA)
        "reviews": [],
        "review_threads": [_codex_thread(resolved=True)],  # resolved back on old head A
        "issue_comments": [],
    }
    assert codex_reviewed_current_head(snapshot) is False


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


def test_thumbs_reaction_after_head_arrival_reaches_stage_3() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
            "issue_comments": [],
            "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z")],
            "head_updated_at": HEAD_UPDATED_AT,
        }
    )
    assert ev["status"] == "clearance_ready_for_approval"
    assert ev["labels"]["add"] == ["clearance-3-ready-for-approval"]


def test_thumbs_reaction_before_head_arrival_stays_pending() -> None:
    """Stale thumbs from an old head must NOT reach Stage 3."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
            "issue_comments": [],
            "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T08:00:00Z")],
            "head_updated_at": HEAD_UPDATED_AT,
        }
    )
    assert ev["status"] == "clearance_pending"


def test_thumbs_reaction_missing_head_updated_at_stays_pending() -> None:
    """FAIL CLOSED at the classifier level too."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [],
            "issue_comments": [],
            "reactions": [_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z")],
            "head_updated_at": None,
        }
    )
    assert ev["status"] == "clearance_pending"


def test_resolved_codex_thread_alone_stays_pending() -> None:
    """Round-2: a resolved Codex thread with no head-anchored review/comment
    is not evidence — must NOT reach Stage 3."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval()],
            "review_threads": [_codex_thread(resolved=True)],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_pending"


def test_head_anchored_review_with_resolved_thread_reaches_stage_3() -> None:
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(), _codex_review()],
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


def test_outdated_unresolved_codex_thread_on_new_head_stays_pending() -> None:
    """Critical regression: zero reviews/comments + one outdated+unresolved
    Codex thread against a NEW head must NOT reach Stage 3. This thread is
    exempt from clearance_blocked (outdated anchor), so without the fix it
    would fall straight through to clearance_ready_for_approval with zero
    actual Codex review of the current head."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [],
            "review_threads": [_codex_thread(resolved=False, outdated=True)],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_pending"
    assert ev["labels"]["add"] == ["clearance-1-pending"]


def test_thread_resolved_on_old_head_then_pushed_stays_pending() -> None:
    """Round-2 reproduction: Codex thread resolved on commit A; author pushes
    commit B with zero new Codex activity. Must stay clearance_pending, not
    jump to Stage 3 on stale thread state."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(head=HEAD_SHA),  # now on commit B
            "reviews": [],
            "review_threads": [_codex_thread(resolved=True)],  # resolved back on commit A
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_pending"
    assert ev["labels"]["add"] == ["clearance-1-pending"]


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
# Round-3 P1: Stage 4 (clearance_ready) needs the same gate as Stage 3.
# An operator approving before Codex has reviewed the current head must not
# reach clearance_ready either — a merge loop gating on stage>=3 would
# otherwise auto-merge a head Codex never saw.
# ---------------------------------------------------------------------------


def test_approved_before_codex_stays_pending_not_ready() -> None:
    """The configured reviewer approves the current head, but Codex hasn't
    reviewed it — must stay clearance_pending, not jump to clearance_ready."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="required-approver")],
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


def test_approved_with_codex_review_reaches_clearance_ready() -> None:
    """Positive control: approval + a head-anchored Codex review ⇒ Stage 4,
    exactly as before this round's fix."""
    ev = evaluate_clearance_snapshot(
        {
            "pull_request": _open_pr(),
            "reviews": [_approval(login="required-approver"), _codex_review()],
            "review_threads": [],
            "issue_comments": [],
        }
    )
    assert ev["status"] == "clearance_ready"
    assert ev["labels"]["add"] == ["clearance-4-ready-for-merge"]


async def test_approved_before_codex_dispatches_no_review_request_end_to_end() -> None:
    """End-to-end: same scenario through enrich_clearance_route — stays
    pending, no review request (Stage 4 never dispatches one anyway, but the
    readiness comment/labels must reflect pending, not ready)."""
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[_approval(login="required-approver")])
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_pending"
    assert result["validation"]["labels"]["add"] == ["clearance-1-pending"]
    assert client.request_reviewers_calls == []
    comment_body = result["writeback"]["comment_body"]
    assert "codex" in comment_body.lower()


async def test_swm_overlay_ready_status_approved_before_codex_stays_pending() -> None:
    """Overlay path reproduction: automation reports 'ready' (e.g. zero Codex
    threads found) and the configured reviewer already approved the current
    head. apply_swm_overlay would promote straight to clearance_ready,
    skipping Stage 3 entirely since the approver already approved —
    enforce_codex_review_gate must still catch and demote it."""
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[_approval(login="required-approver")])
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


async def test_swm_overlay_ready_status_approved_with_codex_evidence_reaches_ready() -> None:
    """Same overlay path, but Codex actually reviewed the head — should proceed to Stage 4."""
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[_approval(login="required-approver"), _codex_review()])
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
    assert result["validation"]["status"] == "clearance_ready"


# ---------------------------------------------------------------------------
# enforce_codex_review_gate — no-op for other statuses
# ---------------------------------------------------------------------------


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


async def test_outdated_unresolved_codex_thread_dispatches_no_review_request() -> None:
    """Critical regression, end-to-end: zero reviews/comments + one Codex
    thread {isResolved: false, isOutdated: true} against a new head must stay
    clearance_pending and must NOT dispatch a review request."""
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[], review_threads=[_codex_thread(resolved=False, outdated=True)])
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_pending"
    assert client.request_reviewers_calls == []


async def test_thread_resolved_on_old_head_then_pushed_dispatches_no_review_request() -> None:
    """Round-2 reproduction, end-to-end: thread resolved on commit A, PR now
    on commit B (HEAD_SHA), zero new Codex activity. Must stay
    clearance_pending with no review request dispatched."""
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(reviews=[], review_threads=[_codex_thread(resolved=True)])
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_pending"
    assert client.request_reviewers_calls == []


# ---------------------------------------------------------------------------
# Round-4 P2: thumbs-reaction evidence, end-to-end
# ---------------------------------------------------------------------------


async def test_thumbs_reaction_after_head_arrival_dispatches_review_request(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(
        reviews=[_approval()],
        reactions=[_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z")],
        head_updated_at=HEAD_UPDATED_AT,
    )
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_ready_for_approval"
    assert len(client.request_reviewers_calls) >= 1


async def test_thumbs_reaction_before_head_arrival_dispatches_no_review_request() -> None:
    """Stale thumbs from an old head, end-to-end: stays pending, no dispatch."""
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(
        reviews=[_approval()],
        reactions=[_codex_thumbs_reaction(created_at="2026-05-01T08:00:00Z")],
        head_updated_at=HEAD_UPDATED_AT,
    )
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_pending"
    assert client.request_reviewers_calls == []


async def test_thumbs_reaction_missing_head_updated_at_dispatches_no_review_request() -> None:
    """FAIL CLOSED, end-to-end: the stub client returns head_updated_at=None
    (as a real fetch failure/absence would look), so the reaction alone must
    not be enough to dispatch a review request."""
    from voyager.bots.clearance.enrichment import enrich_clearance_route

    client = _StubClient(
        reviews=[_approval()],
        reactions=[_codex_thumbs_reaction(created_at="2026-05-01T09:15:00Z")],
        head_updated_at=None,
    )
    result = await enrich_clearance_route(client, _base_route(), repository="iterwheel/voyager")
    assert result["validation"]["status"] == "clearance_pending"
    assert client.request_reviewers_calls == []
