"""Clearance pipeline — webhook-driven SWM-1101 per-thread verdict orchestrator.

Source pattern: /Users/frank/Projects/sweeping-monk/swm/poll.py:poll_pr

Phase 7B-1 scope: deterministic classify→judge→persist→Stage-1.5-sync only.
No LLM investigator in this phase — that lands in 7B-3. The ``investigator``
kwarg is accepted now so 7B-3 does not churn the public signature.

7B-1 limitation — State B (isOutdated) verdicts: under deterministic-only
routing, State B threads default to OPEN because this phase has no diff
comparator to verify whether the push actually addressed the Codex concern.
The investigator wave (7B-3) will add diff verification and may re-judge
outdated threads as RESOLVED when the diff confirms the fix.

Trigger: webhook-only (no polling cycle). Each call corresponds to one webhook
delivery processed by ``dispatch_route_writeback``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from voyager.bots.clearance.classify import (
    classify_thread,
    codex_comment_id,
    is_codex_thread,
    latest_author_reply,
    latest_codex_followup,
)
from voyager.bots.clearance.close_reason import build_close_reason_comment
from voyager.bots.clearance.constants import CLEARANCE_AGENT_SLUG
from voyager.bots.clearance.judge import judge
from voyager.bots.clearance.models import (
    Evidence,
    GitHubThreadState,
    PollRecord,
    Severity,
    Stage15Action,
    Stage15Mutation,
    Status,
    Thread,
    ThreadSnapshot,
    Verdict,
    VerdictHistoryEntry,
)
from voyager.bots.clearance.state import StateStore
from voyager.core.github_app import GitHubAppClient
from voyager.core.writeback import dry_run_enabled

if TYPE_CHECKING:
    from voyager.bots.clearance.investigator import ThreadInvestigator

_log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _process_thread(
    thread_dict: dict[str, Any],
    *,
    repo: str,
    pr: int,
    now: datetime,
    pr_author_login: str | None = None,
) -> tuple[Thread, ThreadSnapshot] | None:
    """Classify, judge, and build Thread + ThreadSnapshot for one Codex thread.

    Returns None when the thread is not a Codex thread or when no integer
    comment_id is available (persistence requires one).
    """
    if not is_codex_thread(thread_dict):
        return None

    comment_id = codex_comment_id(thread_dict)
    if comment_id is None:
        return None

    state = classify_thread(thread_dict)
    reply = latest_author_reply(thread_dict, author_login=pr_author_login)
    followup = latest_codex_followup(thread_dict)

    reply_ts = (reply or {}).get("createdAt") or ""
    followup_ts = (followup or {}).get("createdAt") or ""
    # Only honour a Codex follow-up if it's newer than the latest author reply.
    # Otherwise the followup is stale evidence about a prior state.
    followup_body_for_judge = (followup or {}).get("body") if followup_ts > reply_ts else None

    decision = judge(
        classification=state,
        author_reply_body=(reply or {}).get("body"),
        code_changed=False,  # 7B-1: deferred to investigator wave (7B-3 adds diff verification)
        codex_followup_body=followup_body_for_judge,
        github_isResolved=bool(thread_dict.get("isResolved")),
    )

    path = thread_dict.get("path") or "unknown"
    line = thread_dict.get("line")

    thread_model = Thread(
        id=thread_dict["id"],
        comment_id=comment_id,
        path=path,
        line=line,
        codex_severity=Severity.P2,
        effective_severity=Severity.P2,
        verdict=decision.verdict,
        verdict_reason=decision.reason,
        github_isResolved=bool(thread_dict.get("isResolved")),
        author_reply_id=(reply or {}).get("databaseId"),
        author_reply_substantive=decision.substantive,
        code_changed=None,
    )

    snapshot = ThreadSnapshot(
        thread_id=thread_dict["id"],
        repo=repo,
        pr=pr,
        first_seen=now,
        last_polled=now,
        codex_comment_id=comment_id,
        path=path,
        current_line=line,
        codex_severity=Severity.P2,
        effective_severity=Severity.P2,
        verdict=decision.verdict,
        verdict_history=[
            VerdictHistoryEntry(ts=now, verdict=decision.verdict, reason=decision.reason)
        ],
        evidence=Evidence(
            thread_state=state,
            author_reply_id=(reply or {}).get("databaseId"),
            author_reply_substantive=decision.substantive,
            code_changed=None,
            codex_followed_up=bool(followup),
        ),
        github_state=GitHubThreadState(
            isResolved=bool(thread_dict.get("isResolved")),
            isOutdated=bool(thread_dict.get("isOutdated")),
        ),
    )
    return thread_model, snapshot


def _compute_status(threads: list[Thread]) -> tuple[Status, str]:
    """Aggregate per-thread verdicts into a pipeline-level Status + reason.

    Rules (in precedence order):
      - No Codex threads → READY
      - Any OPEN verdict → BLOCKED
      - Any NEEDS_HUMAN_JUDGMENT verdict → PENDING
      - Otherwise all RESOLVED → READY
    """
    if not threads:
        return Status.READY, "no Codex review threads on PR"

    open_count = sum(1 for t in threads if t.verdict == Verdict.OPEN)
    if open_count:
        noun = "thread" if open_count == 1 else "threads"
        return Status.BLOCKED, f"{open_count} Codex review {noun} still OPEN"

    nhj_count = sum(1 for t in threads if t.verdict == Verdict.NEEDS_HUMAN_JUDGMENT)
    if nhj_count:
        noun = "thread" if nhj_count == 1 else "threads"
        return Status.PENDING, f"{nhj_count} Codex review {noun} need human judgment"

    return Status.READY, "all Codex review threads RESOLVED"


async def _maybe_sync_stage_15(
    *,
    client: GitHubAppClient,
    repository: str,
    threads: list[Thread],
    snapshots: list[ThreadSnapshot],
    pr: int,
    head_sha: str,
    dry_run: bool,
    now: datetime,
) -> list[Stage15Action]:
    """Stage 1.5 — resolve GitHub threads whose verdict is RESOLVED but isResolved=false.

    Posts a conclusion comment (best-effort, suppressed on failure) then calls
    resolveReviewThread. When dry_run=True, returns the planned actions without
    any GitHub writes.
    """
    actions: list[Stage15Action] = []
    snap_by_id = {s.thread_id: s for s in snapshots}

    for thread in threads:
        if thread.verdict != Verdict.RESOLVED:
            continue
        snap = snap_by_id.get(thread.id)
        if not snap or not snap.github_state:
            continue
        if snap.github_state.isResolved:
            continue

        comment_body = build_close_reason_comment(thread, snap, head_sha=head_sha)

        if dry_run:
            actions.append(
                Stage15Action(
                    mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                    threadId=thread.id,
                    result={"dry_run": True},
                )
            )
            continue

        result = await client.resolve_review_thread(CLEARANCE_AGENT_SLUG, repository, thread.id)
        actions.append(
            Stage15Action(
                mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                threadId=thread.id,
                result=result,
            )
        )

        snap.github_state = GitHubThreadState(
            isResolved=True,
            isOutdated=snap.github_state.isOutdated,
            resolvedBy=(result or {}).get("resolvedBy", {}).get("login"),
            synced_via="Stage 1.5 resolveReviewThread",
            synced_at=now,
        )
        thread.github_isResolved = True

        # In-thread reply is best-effort UX; the resolveReviewThread mutation above
        # is the system-of-record state change. Posting AFTER the mutation succeeds
        # guarantees we never leave a duplicate "RESOLVED" reply on a thread that
        # isn't actually resolved (Codex PR #9 P2): if the mutation fails, this
        # block never runs, and the next webhook re-enters the same branch with
        # a fresh snapshot — no spurious comment lingers from a partial attempt.
        try:
            await client.create_review_thread_reply(
                CLEARANCE_AGENT_SLUG,
                repository,
                pr,
                thread.comment_id,
                body=comment_body,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            _log.warning(
                "in-thread reply suppressed for thread %s (Stage 1.5 mutation already applied): %s: %s",
                thread.id,
                exc.__class__.__name__,
                exc,
                exc_info=True,
            )

    return actions


async def compute_clearance_automation(
    client: GitHubAppClient,
    route: dict[str, Any],
    *,
    repository: str,
    store: StateStore,
    investigator: ThreadInvestigator | None = None,  # noqa: ARG001 — reserved for 7B-3 LLM path
) -> dict[str, Any]:
    """Run the SWM-1101 per-thread verdict pipeline for one webhook event.

    Fetches the PR and its review threads, classifies and judges each Codex
    thread, persists a PollRecord + ThreadSnapshots, runs Stage 1.5 sync for
    RESOLVED threads whose GitHub ``isResolved`` is still false, and returns
    the ``automation`` dict shape that ``enrich_clearance_route`` / ``apply_swm_overlay``
    consume.

    The ``investigator`` kwarg is reserved for Phase 7B-3 (LLM verdict layer).
    Pass ``None`` (the default) in 7B-1; the deterministic path is always used.

    Returns a dict with keys: ``enabled``, ``status``, ``reason``,
    ``sync_actions``, ``sync_actions_count``, ``dry_run``.
    On fetch failure, returns ``status="error"`` without raising.
    """
    dry_run = dry_run_enabled()
    pr_number = int(route["validation"]["pr_number"])
    now = _now_utc()

    try:
        pr_data = await client.pull_request(CLEARANCE_AGENT_SLUG, repository, pr_number)
        raw_threads = await client.pull_request_review_threads(
            CLEARANCE_AGENT_SLUG, repository, pr_number
        )
    except Exception as exc:
        return {
            "enabled": True,
            "status": Status.ERROR.value,
            "reason": f"pipeline: fetch failed: {exc.__class__.__name__}: {exc}",
            "sync_actions": [],
            "sync_actions_count": 0,
        }

    head_sha = (pr_data.get("head") or {}).get("sha") or ""
    pr_title = pr_data.get("title")
    pr_author_login: str | None = (pr_data.get("user") or {}).get("login") or None

    threads: list[Thread] = []
    snapshots: list[ThreadSnapshot] = []

    for thread_dict in raw_threads:
        result = _process_thread(
            thread_dict, repo=repository, pr=pr_number, now=now, pr_author_login=pr_author_login
        )
        if result is None:
            continue
        thread_model, snapshot = result
        threads.append(thread_model)
        snapshots.append(snapshot)

    status, reason = _compute_status(threads)

    sync_actions = await _maybe_sync_stage_15(
        client=client,
        repository=repository,
        threads=threads,
        snapshots=snapshots,
        pr=pr_number,
        head_sha=head_sha,
        dry_run=dry_run,
        now=now,
    )

    trigger = "webhook+stage1.5-sync" if sync_actions and not dry_run else "webhook"

    open_count = sum(1 for t in threads if t.verdict != Verdict.RESOLVED)
    resolved_count = sum(1 for t in threads if t.verdict == Verdict.RESOLVED)

    record = PollRecord(
        ts=now,
        repo=repository,
        pr=pr_number,
        title=pr_title,
        head_sha=head_sha,
        status=status,
        codex_open=open_count,
        codex_resolved=resolved_count,
        threads=threads,
        stage15_actions=sync_actions,
        trigger=trigger,
    )
    store.append_poll(record)
    for snap in snapshots:
        store.write_thread(snap)

    return {
        "enabled": True,
        "status": status.value,
        "reason": reason,
        "sync_actions": [a.model_dump() for a in sync_actions],
        "sync_actions_count": len(sync_actions),
        "dry_run": dry_run,
    }
