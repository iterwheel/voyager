"""Clearance bot — snapshot evaluation logic."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict, cast

from voyager.core.codex_review_watch import _is_clean_summary

from .constants import (
    ALL_CLEARANCE_LABELS,
    CLEARANCE_BLOCKED_LABEL,
    CLEARANCE_CLASSIFIER_VERSION,
    CLEARANCE_PENDING_LABEL,
    CLEARANCE_READY_FOR_APPROVAL_LABEL,
    CLEARANCE_READY_LABEL,
    CODEX_REVIEW_RESULT_PREFIX,
    configured_review_request_users,
    is_codex_login,
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
    - status: one of clearance_ready | clearance_pending | clearance_blocked | clearance_ready_for_approval
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


def codex_reviewed_current_head(snapshot: dict[str, Any]) -> bool:
    """True when Codex has reviewed the PR's current head.

    Operator-reported defect (order_system_django PR #71): Clearance requested
    human approval 9 seconds after PR creation because "zero Codex review
    threads" was treated as "review clear." Codex hadn't reviewed anything
    yet. This predicate is head-anchored evidence that Codex actually has —
    a review or comment for a since-superseded head does not count.

    Any ONE of the following is sufficient:
      (a) a non-dismissed Codex PR review (``reviews``) submitted against the
          current head commit — head-anchored via the review's own
          ``commit_id``;
      (b) a Codex clean-verdict PR comment (``issue_comments``) starting with
          the same ``Codex Review:`` prefix as ``CODEX_REVIEW_RESULT_PREFIX``
          (constants.py) whose parsed ``Reviewed commit:`` value
          prefix-matches the current head — head-anchored via that value;
          reuses the exact parsing precedent in
          ``voyager.core.codex_review_watch._is_clean_summary``;
      (c) a Codex ``+1`` reaction on the PR body (``reactions``) — Codex's
          third clean-verdict signal alongside (b), mirrored from
          ``codex_review_watch._detect_signal``'s "thumbs" branch. A
          reaction carries no commit id, so it is TIME-anchored instead: it
          only counts when its ``created_at`` is later than
          ``snapshot["head_updated_at"]``, the best-available "current head
          arrived on this PR" timestamp (see
          ``GitHubAppClient.pull_request_head_updated_at`` — already used by
          ``pipeline.py`` for the identical problem, judging whether a Codex
          issue-comment clean signal predates the current head). FAIL
          CLOSED: a missing/empty ``head_updated_at`` means (c) never fires,
          regardless of how many ``+1`` reactions exist.

    Deliberately NOT evidence: review-thread existence/resolution
    (``review_threads``). Round-2 review finding: thread state cannot be
    reliably head-anchored. A thread resolved on an old head, followed by a
    push with no new Codex activity, satisfies "resolved" for the *new* head
    with zero Codex review of it — resolution proves the finding was
    addressed as of *some* head, not the current one. Worse, GitHub can
    re-anchor an old, untouched review comment to carry the *new* commit id
    (a known trap — see VOY-1832 / TRN-1209: ``created_at``, not
    ``commit_id``, is the only reliable comment-freshness key), so even a
    commit-id check on threads is not trustworthy. Whenever Codex reviews and
    leaves inline findings it necessarily also submits a PR review (author
    ``chatgpt-codex-connector``, state ``COMMENTED``, with its own
    ``commit_id``) — so predicate (a) already covers "Codex reviewed this
    head and left findings" without trusting thread state at all.
    """
    pull_request = snapshot["pull_request"]
    head_sha = ((pull_request.get("head") or {}).get("sha")) or ""
    if not head_sha:
        return False

    for review in snapshot.get("reviews") or []:
        login = (review.get("user") or {}).get("login")
        if (
            is_codex_login(login)
            and str(review.get("state") or "").upper() != "DISMISSED"
            and review.get("commit_id") == head_sha
        ):
            return True

    for comment in snapshot.get("issue_comments") or []:
        login = (comment.get("user") or {}).get("login")
        body = str(comment.get("body") or "")
        if (
            is_codex_login(login)
            and body.startswith(CODEX_REVIEW_RESULT_PREFIX)
            and _is_clean_summary(body, head_sha)
        ):
            return True

    # (c) is time-anchored, not head-anchored — GitHub emits ISO-8601 UTC
    # ('Z'-suffixed) timestamps throughout, which are lexicographically
    # comparable as plain strings (same convention pipeline.py already uses
    # for its own current-head-freshness comment check).
    head_updated_at = str(snapshot.get("head_updated_at") or "")
    if head_updated_at:
        for reaction in snapshot.get("reactions") or []:
            login = (reaction.get("user") or {}).get("login")
            created_at = str(reaction.get("created_at") or "")
            if (
                is_codex_login(login)
                and reaction.get("content") == "+1"
                and created_at > head_updated_at
            ):
                return True

    return False


_CODEX_GATED_STATUSES = frozenset({"clearance_ready_for_approval", "clearance_ready"})


def enforce_codex_review_gate(
    evaluation: ClearanceEvaluation, snapshot: dict[str, Any]
) -> ClearanceEvaluation:
    """Demote ``clearance_ready_for_approval`` (Stage 3) or ``clearance_ready``
    (Stage 4) back to ``clearance_pending`` when Codex has not reviewed the
    PR's current head (see ``codex_reviewed_current_head``).

    Stage 4 needs the same gate as Stage 3: an operator approving before
    Codex has reviewed the current head must not let the PR reach
    ``clearance_ready`` either — a merge loop gating on stage>=3 would
    otherwise auto-merge a head Codex never saw. This is a plain widening of
    the same check, not a new one: both stages mean "the codex-review
    precondition plus something else (a pending human approval, or an
    already-present one) is satisfied," so both need the precondition to
    actually hold.

    Applied as the last step of both ``evaluate_clearance_snapshot`` (so the
    plain GitHub-review-state path is gated for both stages) and
    ``enrich_clearance_route`` (so the SWM overlay's own, separate Stage 3
    *and* Stage 4 promotions — which fire from ``automation["status"] in
    {"ready", "ready_with_low_priority"}`` and can reach either stage without
    going back through ``evaluate_clearance_snapshot``'s branch chain — are
    gated too). No-op for every other status (``clearance_pending``,
    ``clearance_blocked``).
    """
    if evaluation["status"] not in _CODEX_GATED_STATUSES:
        return evaluation
    if codex_reviewed_current_head(snapshot):
        return evaluation

    updated: dict[str, Any] = dict(evaluation)
    confidence = dict(updated["confidence"])
    # Drop the "awaiting configured reviewer" reason: it implies Clearance is
    # one step from requesting a human review, which is no longer true once
    # this gate demotes back to pending — Codex hasn't reviewed yet, so no
    # human review request has been (or will be) made.
    kept_reasons = [
        reason
        for reason in confidence["reasons"]
        if not str(reason).startswith("Awaiting approval from configured reviewer(s):")
    ]
    confidence["reasons"] = [
        *kept_reasons,
        "Waiting for Codex review of the current head before requesting operator approval.",
    ]
    updated["status"] = "clearance_pending"
    updated["conclusion"] = "neutral"
    updated["summary"] = "Clearance is not ready yet."
    updated["confidence"] = confidence
    updated["labels"] = {
        "add": [CLEARANCE_PENDING_LABEL],
        "remove": [item for item in ALL_CLEARANCE_LABELS if item != CLEARANCE_PENDING_LABEL],
    }
    updated["reactions"] = {"add": ["eyes"], "remove": ["+1", "rocket"]}
    return cast(ClearanceEvaluation, updated)


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

    configured = configured_review_request_users()
    current_approvals_lc = {u.lower() for u in current_approvals}
    configured_approval_present = bool(configured) and any(
        user.lower() in current_approvals_lc for user in configured
    )

    # Hard preempts that override env-routing semantics.
    is_draft_or_closed = bool(pull_request.get("draft")) or pull_request.get("state") != "open"

    if blocking_reviewers or unresolved_threads:
        status = "clearance_blocked"
        conclusion = "failure"
        label = CLEARANCE_BLOCKED_LABEL
    elif is_draft_or_closed:
        # Draft / closed is always pending regardless of env config — the operator
        # action is "ready the PR for review", not "find a reviewer".
        status = "clearance_pending"
        conclusion = "neutral"
        label = CLEARANCE_PENDING_LABEL
    elif configured and not configured_approval_present:
        # Env-driven routing: automation is green AND named human(s) have not
        # approved current head. This is precisely clearance_ready_for_approval —
        # the gate the dispatcher fires against. Codex-bot PR #26 review P1:
        # was previously unreachable because the preceding `elif reasons:`
        # branch caught "No approval on the current PR head." first.
        status = "clearance_ready_for_approval"
        conclusion = "neutral"
        label = CLEARANCE_READY_FOR_APPROVAL_LABEL
        reasons.append(
            "Awaiting approval from configured reviewer(s): "
            + ", ".join("@" + user for user in configured)
            + "."
        )
    elif reasons:
        # Env unset and some reason exists (e.g. no current-head approval) →
        # pre-#25 legacy semantics: pending.
        status = "clearance_pending"
        conclusion = "neutral"
        label = CLEARANCE_PENDING_LABEL
    else:
        status = "clearance_ready"
        conclusion = "success"
        label = CLEARANCE_READY_LABEL

    labels: LabelsDict = {
        "add": [label],
        "remove": [item for item in ALL_CLEARANCE_LABELS if item != label],
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
            else "Clearance is ready for human approval."
            if status == "clearance_ready_for_approval"
            else "Clearance is not ready yet."
        ),
    }
    return enforce_codex_review_gate(result, snapshot)
