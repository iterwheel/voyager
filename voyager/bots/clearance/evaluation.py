"""Clearance bot — snapshot evaluation logic."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from .constants import (
    CLEARANCE_BLOCKED_LABEL,
    CLEARANCE_CLASSIFIER_VERSION,
    CLEARANCE_LABELS,
    CLEARANCE_PENDING_LABEL,
    CLEARANCE_READY_LABEL,
)


class ReviewStateView(TypedDict):
    """Review state extracted from GitHub API response."""

    current_approvals: list[str]
    stale_approvals: list[str]
    blocking_reviewers: list[str]
    unresolved_thread_count: int


class ConfidenceView(TypedDict):
    """Confidence assessment with reasons and semantic notes."""

    reasons: list[str]
    semantic_fix_verified: bool
    semantic_fix_note: str


class LabelsDict(TypedDict):
    """Labels to add and remove on the PR."""

    add: list[str]
    remove: list[str]


class ReactionsDict(TypedDict):
    """Reactions to add and remove on the PR body."""

    add: list[str]
    remove: list[str]


class ClearanceEvaluation(TypedDict):
    """Clearance readiness evaluation with GitHub-actionable metadata.

    All fields are always present:
    - status: one of clearance_ready | clearance_pending | clearance_blocked
    - conclusion: one of success | neutral | failure
    - issue_number: PR number (duplicated from pr_number for compatibility)
    - pr_number: GitHub PR number
    - classifier: version string of the evaluator
    - summary: single-line readiness summary
    - review_state: current approvals, blocking reviewers, unresolved threads
    - confidence: reasons why Clearance is blocked/pending and semantic notes
    - labels: add/remove label lists for GitHub mutations
    - reactions: add/remove reaction lists for GitHub mutations
    - pr_url: the PR's GitHub URL (may be None as a value, but key always present)
    - head_sha: commit SHA at the PR head (may be "")
    - target_kind: always "pull_request"
    """

    status: str
    conclusion: str
    issue_number: int
    pr_number: int
    classifier: str
    summary: str
    review_state: ReviewStateView
    confidence: ConfidenceView
    labels: LabelsDict
    reactions: ReactionsDict
    pr_url: str | None
    head_sha: str
    target_kind: str


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def latest_decisive_reviews_by_author(
    reviews: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    decisive_states = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
    for review in sorted(reviews, key=lambda item: parse_timestamp(item.get("submitted_at"))):
        user = review.get("user") or {}
        author = user.get("login")
        state = str(review.get("state") or "").upper()
        if not author or state not in decisive_states:
            continue
        latest[author] = review
    return latest


def evaluate_clearance_snapshot(snapshot: dict[str, Any]) -> ClearanceEvaluation:
    pull_request = snapshot["pull_request"]
    head_sha = ((pull_request.get("head") or {}).get("sha")) or ""
    reviews = list(snapshot.get("reviews") or [])
    review_threads = list(snapshot.get("review_threads") or [])

    latest_reviews = latest_decisive_reviews_by_author(reviews)
    blocking_reviewers = sorted(
        author
        for author, review in latest_reviews.items()
        if str(review.get("state") or "").upper() == "CHANGES_REQUESTED"
    )
    approvals = {
        author: review
        for author, review in latest_reviews.items()
        if str(review.get("state") or "").upper() == "APPROVED"
    }
    current_approvals = sorted(
        author
        for author, review in approvals.items()
        if not head_sha or review.get("commit_id") == head_sha
    )
    stale_approvals = sorted(
        author
        for author, review in approvals.items()
        if head_sha and review.get("commit_id") and review.get("commit_id") != head_sha
    )
    # Outdated unresolved threads (isOutdated=true) are conversations on code
    # that has since been replaced; counting them as blockers would keep
    # Clearance in BLOCKED even after the author pushes a fix. Filter them out
    # so only current unresolved threads block readiness. Codex round 5 P2.
    unresolved_threads = [
        thread
        for thread in review_threads
        if not thread.get("isResolved") and not thread.get("isOutdated")
    ]

    reasons: list[str] = []
    if pull_request.get("draft"):
        reasons.append("PR is still draft.")
    if pull_request.get("state") != "open":
        reasons.append("PR is not open.")
    if blocking_reviewers:
        reasons.append(
            f"Changes requested by: {', '.join('@' + user for user in blocking_reviewers)}."
        )
    if unresolved_threads:
        reasons.append(f"{len(unresolved_threads)} review thread(s) are unresolved.")
    if not current_approvals:
        if stale_approvals:
            reasons.append(
                f"Only stale approval(s) exist: {', '.join('@' + user for user in stale_approvals)}."
            )
        else:
            reasons.append("No approval on the current PR head.")

    if blocking_reviewers or unresolved_threads:
        status = "clearance_blocked"
        conclusion = "failure"
        label = CLEARANCE_BLOCKED_LABEL
    elif reasons:
        status = "clearance_pending"
        conclusion = "neutral"
        label = CLEARANCE_PENDING_LABEL
    else:
        status = "clearance_ready"
        conclusion = "success"
        label = CLEARANCE_READY_LABEL

    labels: LabelsDict = {
        "add": [label],
        "remove": [item for item in CLEARANCE_LABELS if item != label],
    }
    reactions: ReactionsDict = (
        {"add": ["+1"], "remove": ["eyes", "rocket"]}
        if status == "clearance_ready"
        else {"add": ["eyes"], "remove": ["+1", "rocket"]}
    )
    review_state: ReviewStateView = {
        "current_approvals": current_approvals,
        "stale_approvals": stale_approvals,
        "blocking_reviewers": blocking_reviewers,
        "unresolved_thread_count": len(unresolved_threads),
    }
    confidence: ConfidenceView = {
        "reasons": reasons,
        "semantic_fix_verified": False,
        "semantic_fix_note": (
            "Clearance v1 verifies GitHub review state and review-thread resolution; "
            "it does not prove that every requested semantic code change was fixed."
        ),
    }
    result: ClearanceEvaluation = {
        "status": status,
        "conclusion": conclusion,
        "issue_number": pull_request["number"],
        "pr_number": pull_request["number"],
        "pr_url": pull_request.get("html_url"),
        "target_kind": "pull_request",
        "classifier": CLEARANCE_CLASSIFIER_VERSION,
        "head_sha": head_sha,
        "review_state": review_state,
        "confidence": confidence,
        "labels": labels,
        "reactions": reactions,
        "summary": (
            "Clearance is ready for Countdown."
            if status == "clearance_ready"
            else "Clearance is not ready yet."
        ),
    }
    return result
