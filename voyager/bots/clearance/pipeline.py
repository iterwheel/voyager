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

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from voyager.bots.clearance.classify import (
    ThreadState,
    classify_thread,
    codex_comment_id,
    is_codex_thread,
    latest_author_reply,
    latest_codex_followup,
)
from voyager.bots.clearance.close_reason import build_close_reason_comment
from voyager.bots.clearance.constants import CLEARANCE_AGENT_SLUG
from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt
from voyager.bots.clearance.investigator import (
    InvestigationDecision,
    InvestigationError,
    ThreadInvestigationInput,
    ThreadInvestigator,
)
from voyager.bots.clearance.judge import VerdictDecision, judge
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
from voyager.bots.clearance.severity import evaluate as evaluate_severity
from voyager.bots.clearance.severity_input import extract_severity_and_kind
from voyager.bots.clearance.state import StateStore
from voyager.core.github_app import GitHubAppClient, GitHubGraphQLError
from voyager.core.writeback import _safe_exception_fields, build_writeback_failure, dry_run_enabled

_log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


async def _process_thread(
    thread_dict: dict[str, Any],
    *,
    repo: str,
    pr: int,
    head_sha: str,
    pr_title: str | None,
    now: datetime,
    base_branch: str,
    branch_protected_state: bool,
    client: GitHubAppClient,  # noqa: ARG001 — kept for future per-thread API calls
    pr_author_login: str | None = None,
    investigator: ThreadInvestigator | None = None,
    get_diff: Callable[[], Awaitable[str]] | None = None,
    failures: list[tuple[str, str]] | None = None,
    profile_name: str | None = None,
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

    author_reply_body = (reply or {}).get("body")

    # Extract codex severity + finding_kind from review body
    comments_nodes = (thread_dict.get("comments") or {}).get("nodes") or []
    codex_sev, finding_kind = extract_severity_and_kind(comments_nodes)

    # Evaluate severity demotion using the per-webhook branch_protected_state
    # (already fetched once in compute_clearance_automation; passed in here)
    sev_decision = evaluate_severity(
        codex_severity=codex_sev,
        finding_kind=finding_kind,
        branch_protected=branch_protected_state,
        base_branch=base_branch,
    )

    # Emit structured log on demotion (Codex MVE P3: include base_branch + finding_kind
    # so operators can grep by branch + correlate demotions to extractor signal)
    if sev_decision.effective_severity != sev_decision.codex_severity:
        _log.info(
            "severity_demoted: %s",
            json.dumps(
                {
                    "event": "severity_demoted",
                    "repo": repo,
                    "pr": pr,
                    "thread_id": thread_dict.get("id"),
                    "base_branch": base_branch,
                    "finding_kind": finding_kind,
                    "codex_severity": sev_decision.codex_severity.value,
                    "effective_severity": sev_decision.effective_severity.value,
                    "reason": sev_decision.reason,
                }
            ),
        )

    decision = judge(
        classification=state,
        author_reply_body=author_reply_body,
        code_changed=False,  # 7B-1: deferred to investigator wave (7B-3 adds diff verification)
        codex_followup_body=followup_body_for_judge,
        github_isResolved=bool(thread_dict.get("isResolved")),
    )

    path = thread_dict.get("path") or "unknown"
    line = thread_dict.get("line")

    # AUGMENT invariant: gate skips when judge() already returned RESOLVED.
    # Together with `state == ThreadState.B` this preserves *every* deterministic
    # RESOLVED path (github_isResolved=true, positive Codex follow-up, future
    # code_changed=True) without LLM overrule. Do not loosen the gate without
    # extending the regression set.
    llm_decision: InvestigationDecision | None = None
    llm_error_str: str | None = None
    if (
        investigator is not None
        and get_diff is not None
        and state == ThreadState.B
        and decision.verdict != Verdict.RESOLVED  # skip if already deterministically RESOLVED
    ):
        model_name = getattr(getattr(investigator, "_client", None), "model", "unknown")
        started = time.monotonic()
        failure_type: str | None = None
        try:
            diff_text = await get_diff()
            excerpt = extract_anchor_excerpt(
                diff_text,
                path=path,
                line=line,
                max_chars=investigator.max_diff_chars,
            )
            comments = (thread_dict.get("comments") or {}).get("nodes") or []
            codex_comment_body = (comments[0].get("body") if comments else None) or ""
            item = ThreadInvestigationInput(
                repo=repo,
                pr=pr,
                pr_title=pr_title,
                head_sha=head_sha,
                path=path,
                line=line,
                classification="B",
                codex_comment_body=codex_comment_body,
                author_reply_body=author_reply_body,
                diff_excerpt=excerpt,
                heuristic_verdict=decision.verdict.value,
                heuristic_reason=decision.reason,
            )
            returned = await investigator.investigate(item)
            try:
                coerced = Verdict(returned.verdict)
            except ValueError as exc:
                raise InvestigationError(
                    f"investigator returned unknown verdict: {returned.verdict!r}"
                ) from exc
            llm_decision = returned
            decision = VerdictDecision(
                verdict=coerced,
                reason=llm_decision.reason,
                substantive=decision.substantive,
            )
        except InvestigationError as exc:
            failure_type = "investigation_error"
            _log.warning(
                "investigator failed for thread %s (falling back to deterministic): %s",
                thread_dict.get("id"),
                exc,
                exc_info=True,
            )
            llm_error_str = str(exc)
            if failures is not None:
                failures.append((thread_dict.get("id") or "", str(exc)))
        except (httpx.HTTPError, TimeoutError) as exc:
            failure_type = "timeout" if isinstance(exc, TimeoutError) else "http_error"
            _log.warning(
                "diff fetch / investigator network failure for thread %s "
                "(falling back to deterministic): %s",
                thread_dict.get("id"),
                exc,
                exc_info=True,
            )
            llm_error_str = str(exc)
            if failures is not None:
                failures.append((thread_dict.get("id") or "", str(exc)))
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)
            downgrade = bool(
                llm_decision and llm_decision.reason and "below threshold" in llm_decision.reason
            )
            _log.info(
                "investigator_call: %s",
                json.dumps(
                    {
                        "event": "investigator_call",
                        "repo": repo,
                        "pr": pr,
                        "thread_id": thread_dict.get("id"),
                        "profile_name": profile_name,
                        "model": model_name,
                        "latency_ms": latency_ms,
                        "verdict": llm_decision.verdict if llm_decision else None,
                        "confidence": llm_decision.confidence if llm_decision else None,
                        "threshold_downgrade_fired": downgrade,
                        "failed": failure_type is not None,
                        "failure_type": failure_type,
                    }
                ),
            )

    thread_model = Thread(
        id=thread_dict["id"],
        comment_id=comment_id,
        path=path,
        line=line,
        codex_severity=sev_decision.codex_severity,
        effective_severity=sev_decision.effective_severity,
        demotion_reason=sev_decision.reason,
        verdict=decision.verdict,
        verdict_reason=decision.reason,
        github_isResolved=bool(thread_dict.get("isResolved")),
        author_reply_id=(reply or {}).get("databaseId"),
        author_reply_substantive=decision.substantive,
        code_changed=None,
        llm_verdict=llm_decision.verdict if llm_decision else None,
        llm_confidence=llm_decision.confidence if llm_decision else None,
        llm_reason=llm_decision.reason if llm_decision else None,
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
        codex_severity=sev_decision.codex_severity,
        effective_severity=sev_decision.effective_severity,
        demotion_reason=sev_decision.reason,
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
            llm_verdict=llm_decision.verdict if llm_decision else None,
            llm_confidence=llm_decision.confidence if llm_decision else None,
            llm_reason=llm_decision.reason if llm_decision else None,
            llm_evidence=llm_decision.evidence if llm_decision else None,
            llm_error=llm_error_str,
        ),
        github_state=GitHubThreadState(
            isResolved=bool(thread_dict.get("isResolved")),
            isOutdated=bool(thread_dict.get("isOutdated")),
        ),
    )
    return thread_model, snapshot


def _compute_status(threads: list[Thread]) -> tuple[Status, str]:
    """Aggregate per-thread verdicts into a pipeline-level Status + reason.

    β precedence (Wave 7C, VOY-1809):
      1. No threads → READY
      2. Any OPEN with effective_severity ∈ {P1, P2} → BLOCKED (count only
         high-priority OPEN in the reason)
      3. Any NEEDS_HUMAN_JUDGMENT → PENDING
      4. Only OPEN P3 remaining (others RESOLVED) → READY with low-priority note
      5. All RESOLVED → READY
    """
    if not threads:
        return Status.READY, "no Codex review threads on PR"

    open_high = [
        t
        for t in threads
        if t.verdict == Verdict.OPEN and t.effective_severity in (Severity.P1, Severity.P2)
    ]
    if open_high:
        n = len(open_high)
        noun = "thread" if n == 1 else "threads"
        return Status.BLOCKED, f"{n} high-priority {noun} still OPEN"

    nhj = [t for t in threads if t.verdict == Verdict.NEEDS_HUMAN_JUDGMENT]
    if nhj:
        n = len(nhj)
        noun = "thread" if n == 1 else "threads"
        verb = "needs" if n == 1 else "need"
        return Status.PENDING, f"{n} Codex review {noun} {verb} human judgment"

    open_low = [
        t for t in threads if t.verdict == Verdict.OPEN and t.effective_severity == Severity.P3
    ]
    if open_low:
        n = len(open_low)
        noun = "thread" if n == 1 else "threads"
        return Status.READY_WITH_LOW_PRIORITY, (
            f"all blocking threads RESOLVED; {n} low-priority {noun} still open"
        )

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

        # CHG-1813: Catch resolveReviewThread write failures and record
        # structured metadata instead of propagating the exception.
        # On failure: snap.github_state, thread.github_isResolved, and
        # the in-thread reply remain unchanged (A6).
        try:
            result = await client.resolve_review_thread(CLEARANCE_AGENT_SLUG, repository, thread.id)
        except (httpx.HTTPError, GitHubGraphQLError, TimeoutError) as exc:
            failure = build_writeback_failure(
                operation="resolveReviewThread",
                exc=exc,
                repository=repository,
                pr=pr,
                thread_id=thread.id,
            )
            _log.warning(
                "resolveReviewThread failed for thread %s on %s#%s: %s",
                thread.id,
                repository,
                pr,
                json.dumps(failure),
            )
            actions.append(
                Stage15Action(
                    mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                    threadId=thread.id,
                    result={"applied": False, **failure},
                )
            )
            continue

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
            safe = _safe_exception_fields(exc)
            _log.warning(
                "in-thread reply suppressed for thread %s "
                "(Stage 1.5 mutation already applied): class=%s status=%s",
                thread.id,
                safe["error_class"],
                safe["status"],
            )

    return actions


def _stage15_writeback_failures(sync_actions: list[Stage15Action]) -> dict[str, Any]:
    """Collect Stage 1.5 writeback failures from sync action results.

    Returns a dict with ``writeback_failures``, ``writeback_failure_count``,
    and ``writeback_failure_reason`` only when failures are present.
    Returns an empty dict when no failures occurred.
    """
    failures: list[dict[str, Any]] = []
    for action in sync_actions:
        result = action.result or {}
        if result.get("applied") is False and result.get("operation"):
            failures.append(result)

    if not failures:
        return {}

    count = len(failures)
    first = failures[0]
    operation = first.get("operation", "unknown")
    error_class = first.get("error_class", "unknown")
    status = first.get("status")
    status_part = f", HTTP {status}" if status is not None else ""
    if count == 1:
        reason = f"1 writeback operation failed; first: {operation} ({error_class}{status_part})"
    else:
        reason = (
            f"{count} writeback operations failed; first: {operation} ({error_class}{status_part})"
        )

    return {
        "writeback_failures": failures,
        "writeback_failure_count": count,
        "writeback_failure_reason": reason,
    }


async def compute_clearance_automation(
    client: GitHubAppClient,
    route: dict[str, Any],
    *,
    repository: str,
    store: StateStore,
    investigator: ThreadInvestigator | None = None,
    default_profile_name: str | None = None,
    expected_sha: str | None = None,
) -> dict[str, Any]:
    """Run the SWM-1101 per-thread verdict pipeline for one webhook event.

    Fetches the PR and its review threads, classifies and judges each Codex
    thread, persists a PollRecord + ThreadSnapshots, runs Stage 1.5 sync for
    RESOLVED threads whose GitHub ``isResolved`` is still false, and returns
    the ``automation`` dict shape that ``enrich_clearance_route`` / ``apply_swm_overlay``
    consume.

    When ``investigator`` is provided, State B threads with ``code_changed=False``
    are routed through the LLM investigator (Wave 7B-3 D1=B AUGMENT). Threads
    on the deterministic fast-path pay zero diff cost (lazy memoized fetch).

    When ``expected_sha`` is provided (the webhook-time PR head SHA), Stage 1.5
    mutations are skipped if the freshly fetched PR head has advanced past
    ``expected_sha``. This pre-mutation stale check prevents applying verdicts
    computed against a now-superseded commit.

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
        safe = _safe_exception_fields(exc)
        return {
            "enabled": True,
            "status": Status.ERROR.value,
            "reason": f"pipeline: fetch failed: {safe['error_class']}",
            "sync_actions": [],
            "sync_actions_count": 0,
        }

    head_sha = (pr_data.get("head") or {}).get("sha") or ""
    pr_title = pr_data.get("title")
    pr_author_login: str | None = (pr_data.get("user") or {}).get("login") or None
    base_branch = (pr_data.get("base") or {}).get("ref") or "main"

    # Wave 7C-1 commit 3 + Codex MVE-round P2: hoist branch_protected fetch out of
    # the per-thread loop. All threads on the same PR share the same base branch,
    # so calling branch_protected once per webhook (not N times for N threads)
    # eliminates the N-REST-rate-limit risk Codex flagged. Fail-safe to True on
    # any exception per VOY-1809 D3 (don't demote on uncertainty).
    try:
        branch_protected_state = await client.branch_protected(
            CLEARANCE_AGENT_SLUG, repository, base_branch
        )
    except Exception as exc:
        safe = _safe_exception_fields(exc)
        _log.warning(
            "branch_protected fetch failed for %s branch=%s "
            "(fail-safe -> True): class=%s status=%s",
            repository,
            base_branch,
            safe["error_class"],
            safe["status"],
        )
        branch_protected_state = True

    # Lazy memoized diff fetch — fires GitHub API only when the first
    # State B + code_changed=False thread actually needs it. Gemini's
    # round-3 refinement of D3=B: a webhook where every thread resolves
    # via deterministic fast-path pays zero diff cost.
    _diff_cache: dict[str, str] = {}

    async def get_diff() -> str:
        if "diff" not in _diff_cache:
            _diff_cache["diff"] = await client.pull_request_diff(
                CLEARANCE_AGENT_SLUG, repository, pr_number
            )
        return _diff_cache["diff"]

    threads: list[Thread] = []
    snapshots: list[ThreadSnapshot] = []
    investigator_failures: list[tuple[str, str]] = []

    for thread_dict in raw_threads:
        result = await _process_thread(
            thread_dict,
            repo=repository,
            pr=pr_number,
            head_sha=head_sha,
            pr_title=pr_title,
            now=now,
            base_branch=base_branch,
            branch_protected_state=branch_protected_state,
            client=client,
            pr_author_login=pr_author_login,
            investigator=investigator,
            get_diff=get_diff,
            failures=investigator_failures,
            profile_name=default_profile_name,
        )
        if result is None:
            continue
        thread_model, snapshot = result
        threads.append(thread_model)
        snapshots.append(snapshot)

    status, reason = _compute_status(threads)

    # Pre-mutation stale guard (first check): if the caller supplied the
    # webhook-time head SHA and the freshly fetched PR head has already advanced,
    # skip Stage 1.5 writes so we don't apply verdicts computed against a
    # superseded commit.
    if expected_sha and head_sha and head_sha != expected_sha:
        _log.info(
            "pipeline_stale_verdict_skip: %s",
            json.dumps(
                {
                    "event": "pipeline_stale_verdict_skip",
                    "repo": repository,
                    "pr": pr_number,
                    "expected_sha": expected_sha,
                    "actual_sha": head_sha,
                }
            ),
        )
        return {
            "enabled": True,
            "status": "stale_verdict_skip",
            "reason": f"head advanced from {expected_sha} to {head_sha}; Stage 1.5 skipped",
            "sync_actions": [],
            "sync_actions_count": 0,
            "dry_run": dry_run,
            "head_sha": head_sha,
        }

    # Pre-mutation stale guard (second check): re-fetch the PR head right before
    # Stage 1.5 to close the race window between the initial fetch and the
    # resolveReviewThread mutations. The investigator and classify steps can take
    # non-trivial time; the head may have advanced since.
    #
    # When expected_sha is provided (pull_request webhook), use it as the
    # mutation-boundary baseline. When expected_sha is None (check_suite events
    # or /clearance issue comments), use the initial head_sha fetched at the top
    # of this function — that initial fetch is the earliest known-good head for
    # this pipeline run, so any advancement past it still indicates stale verdicts.
    try:
        pr_data_fresh = await client.pull_request(CLEARANCE_AGENT_SLUG, repository, pr_number)
        head_sha_fresh: str | None = (pr_data_fresh.get("head") or {}).get("sha") or ""
    except Exception as exc:
        safe = _safe_exception_fields(exc)
        _log.warning(
            "pre-stage-1.5 stale re-fetch failed (fail-open, proceeding): class=%s status=%s",
            safe["error_class"],
            safe["status"],
        )
        head_sha_fresh = None
    baseline = expected_sha or head_sha
    if baseline and head_sha_fresh and head_sha_fresh != baseline:
        _log.info(
            "pipeline_stale_verdict_skip: %s",
            json.dumps(
                {
                    "event": "pipeline_stale_verdict_skip",
                    "repo": repository,
                    "pr": pr_number,
                    "expected_sha": baseline,
                    "actual_sha": head_sha_fresh,
                }
            ),
        )
        return {
            "enabled": True,
            "status": "stale_verdict_skip",
            "reason": (
                f"head advanced from {baseline} to {head_sha_fresh} "
                "during processing; Stage 1.5 skipped"
            ),
            "sync_actions": [],
            "sync_actions_count": 0,
            "dry_run": dry_run,
            "head_sha": head_sha_fresh,
        }

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

    investigator_fired = any(t.llm_verdict for t in threads)
    if investigator_fired:
        trigger = "webhook+investigator" + (
            "+stage1.5-sync" if sync_actions and not dry_run else ""
        )
    elif sync_actions and not dry_run:
        trigger = "webhook+stage1.5-sync"
    else:
        trigger = "webhook"

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

    result_dict: dict[str, Any] = {
        "enabled": True,
        "status": status.value,
        "reason": reason,
        "sync_actions": [a.model_dump() for a in sync_actions],
        "sync_actions_count": len(sync_actions),
        "dry_run": dry_run,
        "head_sha": head_sha,
        "unresolved_codex_thread_count": sum(1 for t in threads if t.verdict != Verdict.RESOLVED),
    }
    if investigator_failures:
        result_dict["investigator_error_count"] = len(investigator_failures)
        result_dict["investigator_error_thread_ids"] = [tid for tid, _ in investigator_failures]
        result_dict["investigator_error_reason"] = investigator_failures[0][1]

    # CHG-1813: Aggregate Stage 1.5 writeback failures.
    # Only add keys when failures are present; successful results omit them.
    wb_failures = _stage15_writeback_failures(sync_actions)
    if wb_failures:
        result_dict.update(wb_failures)
        # If any intended Stage 1.5 write failed, surface as ERROR status
        # so apply_swm_overlay handles it via the existing "error" path.
        result_dict["status"] = Status.ERROR.value
        # Keep the original pipeline-level reason alongside failure metadata;
        # override reason with the failure summary.
        result_dict["reason"] = wb_failures["writeback_failure_reason"]

    return result_dict
