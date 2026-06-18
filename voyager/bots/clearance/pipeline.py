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

from voyager.bots.assembly.constants import ASSEMBLY_AGENT_SLUG
from voyager.bots.clearance.classify import (
    ThreadState,
    _comment_nodes,
    classify_thread,
    codex_comment_id,
    is_codex_thread,
    latest_author_reply,
    latest_codex_followup,
)
from voyager.bots.clearance.close_reason import (
    build_close_reason_comment,
    build_delegated_close_reason_comment,
    build_manual_close_required_comment,
    build_thread_conclusion_comment,
)
from voyager.bots.clearance.constants import (
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_BOT_LOGIN,
    CODEX_REVIEW_RESULT_PREFIX,
    is_codex_login,
)
from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt
from voyager.bots.clearance.investigator import (
    InvestigationDecision,
    InvestigationError,
    ThreadInvestigationInput,
    ThreadInvestigator,
)
from voyager.bots.clearance.judge import VerdictDecision, judge
from voyager.bots.clearance.known_limitations import KnownLimitationStore
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


_AUTHORIZED_RESOLVER_APP_BY_PR_AUTHOR = {
    "iterwheel-assembly[bot]": ASSEMBLY_AGENT_SLUG,
    "app/iterwheel-assembly": ASSEMBLY_AGENT_SLUG,
    "iterwheel-assembly": ASSEMBLY_AGENT_SLUG,
}


def _authorized_resolver_app_for_pr_author(pr_author_login: str | None) -> str | None:
    """Return the App slug allowed to resolve threads for this PR author.

    Resolver fallback is deliberately narrow: only known Iterwheel App authors
    may supply an alternate App token. Human or third-party authors must not
    cause Clearance to resolve conversations through an unrelated identity.
    """
    return _AUTHORIZED_RESOLVER_APP_BY_PR_AUTHOR.get(str(pr_author_login or ""))


def _bot_login_for_app_slug(app_slug: str) -> str:
    return f"{app_slug}[bot]"


def _is_clearance_comment(comment: dict[str, Any]) -> bool:
    login = (comment.get("author") or {}).get("login")
    return login in {CLEARANCE_AGENT_SLUG, CLEARANCE_BOT_LOGIN}


def _has_current_head_verdict_comment(
    comments: list[dict[str, Any]],
    *,
    thread_id: str,
    head_sha: str,
    verdict: Verdict,
) -> bool:
    """Return true when Clearance already posted this head's verdict comment."""
    head_prefix = head_sha[:12]
    if verdict == Verdict.RESOLVED:
        marker_prefix = f"<!-- clearance-close-reason:{thread_id}:{head_prefix}"
        verdict_token = None
    else:
        marker_prefix = f"<!-- clearance-thread-conclusion:{thread_id}:{head_prefix}"
        verdict_token = f"Verdict: `{verdict.value}`"

    for comment in comments:
        if not _is_clearance_comment(comment):
            continue
        body = str(comment.get("body") or "")
        if not body.startswith(marker_prefix):
            continue
        if verdict_token is None or verdict_token in body:
            return True
    return False


def _has_current_head_final_verdict_comment(
    comments: list[dict[str, Any]],
    *,
    thread_id: str,
    head_sha: str,
) -> bool:
    """Return true when Clearance already posted any final verdict for this head."""
    head_prefix = head_sha[:12]
    marker_prefixes = (
        f"<!-- clearance-close-reason:{thread_id}:{head_prefix}",
        f"<!-- clearance-thread-conclusion:{thread_id}:{head_prefix}",
    )

    for comment in comments:
        if not _is_clearance_comment(comment):
            continue
        body = str(comment.get("body") or "")
        if body.startswith(marker_prefixes):
            return True
    return False


async def _has_fresh_current_head_final_verdict_comment(
    *,
    client: GitHubAppClient,
    repository: str,
    pr: int,
    thread_id: str,
    head_sha: str,
    cache: dict[str, Any],
) -> bool:
    """Re-fetch thread comments before writeback to suppress stale-snapshot duplicates."""
    key = f"{thread_id}:{head_sha[:12]}"
    marker_cache = cache.setdefault("markers", {})
    if key in marker_cache:
        return bool(marker_cache[key])

    found = False
    if "fresh_threads" not in cache:
        try:
            cache["fresh_threads"] = await client.pull_request_review_threads(
                CLEARANCE_AGENT_SLUG, repository, pr
            )
        except Exception as exc:
            safe = _safe_exception_fields(exc)
            _log.warning(
                "fresh verdict-comment dedupe check failed for thread %s on %s#%s: "
                "class=%s status=%s",
                thread_id,
                repository,
                pr,
                safe["error_class"],
                safe["status"],
            )
            marker_cache[key] = False
            return False

    for item in cache["fresh_threads"]:
        if item.get("id") != thread_id:
            continue
        comments = (item.get("comments") or {}).get("nodes") or []
        found = _has_current_head_final_verdict_comment(
            comments,
            thread_id=thread_id,
            head_sha=head_sha,
        )
        break

    marker_cache[key] = found
    return found


async def _has_fresh_current_head_resolved_comment(
    *,
    client: GitHubAppClient,
    repository: str,
    pr: int,
    thread_id: str,
    head_sha: str,
    cache: dict[str, Any],
) -> bool:
    """Re-fetch thread comments before writeback to suppress duplicate RESOLVED replies."""
    key = f"resolved:{thread_id}:{head_sha[:12]}"
    marker_cache = cache.setdefault("markers", {})
    if key in marker_cache:
        return bool(marker_cache[key])

    found = False
    if "fresh_threads" not in cache:
        try:
            cache["fresh_threads"] = await client.pull_request_review_threads(
                CLEARANCE_AGENT_SLUG, repository, pr
            )
        except Exception as exc:
            safe = _safe_exception_fields(exc)
            _log.warning(
                "fresh resolved-comment dedupe check failed for thread %s on %s#%s: "
                "class=%s status=%s",
                thread_id,
                repository,
                pr,
                safe["error_class"],
                safe["status"],
            )
            marker_cache[key] = False
            return False

    for item in cache["fresh_threads"]:
        if item.get("id") != thread_id:
            continue
        comments = (item.get("comments") or {}).get("nodes") or []
        found = _has_current_head_verdict_comment(
            comments,
            thread_id=thread_id,
            head_sha=head_sha,
            verdict=Verdict.RESOLVED,
        )
        break

    marker_cache[key] = found
    return found


async def _current_head_verdict_reply_skip_reason(
    *,
    client: GitHubAppClient,
    repository: str,
    pr: int,
    thread: Thread,
    head_sha: str,
    cache: dict[str, Any],
) -> str | None:
    if thread.verdict == Verdict.RESOLVED:
        if thread.existing_close_reason_marker:
            return "existing resolved verdict reply for current head"
        if await _has_fresh_current_head_resolved_comment(
            client=client,
            repository=repository,
            pr=pr,
            thread_id=thread.id,
            head_sha=head_sha,
            cache=cache,
        ):
            return "existing resolved verdict reply for current head after refresh"
        return None

    if (
        thread.existing_head_verdict_marker
        or thread.existing_close_reason_marker
        or thread.existing_thread_conclusion_marker
    ):
        return "existing final verdict reply for current head"

    if await _has_fresh_current_head_final_verdict_comment(
        client=client,
        repository=repository,
        pr=pr,
        thread_id=thread.id,
        head_sha=head_sha,
        cache=cache,
    ):
        return "existing final verdict reply for current head after refresh"

    return None


async def _app_can_resolve_thread(
    *,
    client: GitHubAppClient,
    app_slug: str,
    repository: str,
    pr: int,
    thread_id: str,
    cache: dict[str, list[dict[str, Any]] | None] | None = None,
) -> bool:
    """Check whether *app_slug* can resolve *thread_id* without mutating it."""
    if cache is not None and app_slug in cache:
        threads = cache[app_slug]
    else:
        try:
            threads = await client.pull_request_review_threads(app_slug, repository, pr)
        except Exception as exc:
            safe = _safe_exception_fields(exc)
            _log.warning(
                "resolver fallback capability check failed for app=%s thread=%s "
                "on %s#%s: class=%s status=%s",
                app_slug,
                thread_id,
                repository,
                pr,
                safe["error_class"],
                safe["status"],
            )
            threads = None
        if cache is not None:
            cache[app_slug] = threads

    if not threads:
        return False

    for item in threads:
        if item.get("id") == thread_id:
            return item.get("viewerCanResolve") is True
    return False


async def _app_head_repo_accessible(
    *,
    client: GitHubAppClient,
    app_slug: str,
    head_repo: str,
    cache: dict[tuple[str, str], bool],
) -> bool:
    key = (app_slug, head_repo)
    if key not in cache:
        try:
            cache[key] = await client.check_head_repo_accessible(app_slug, head_repo)
        except Exception as exc:
            safe = _safe_exception_fields(exc)
            _log.warning(
                "resolver fallback head-repo access check failed for app=%s head_repo=%s: "
                "class=%s status=%s",
                app_slug,
                head_repo,
                safe["error_class"],
                safe["status"],
            )
            cache[key] = False
    return cache[key]


_CLEAN_CODEX_REVIEW_VERDICTS = (
    "didn't find any major issues",
    "did not find any major issues",
    "no major issues found",
    "found no major issues",
    "no major issues found in this pr",
)
_CLEAN_CODEX_REVIEW_SUFFIXES = (
    "",
    ". nice work",
    "! nice work",
)


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _normalized_review_body(body: Any) -> str:
    text = str(body or "").split("<details", 1)[0]
    return " ".join(text.split()).replace("\u2019", "'").lower()


def _clean_codex_review_verdict(body: Any) -> str | None:
    normalized = _normalized_review_body(body)
    prefix = CODEX_REVIEW_RESULT_PREFIX.lower()
    if not normalized.startswith(prefix):
        return None
    verdict = normalized[len(prefix) :].strip().rstrip(".!").strip()
    for clean in _CLEAN_CODEX_REVIEW_VERDICTS:
        for suffix in _CLEAN_CODEX_REVIEW_SUFFIXES:
            if verdict == f"{clean}{suffix}":
                return clean
    return verdict


def _is_current_head_codex_review(review: dict[str, Any], *, head_sha: str) -> bool:
    """True when a non-dismissed Codex PR review belongs to the current head."""
    login = (review.get("user") or {}).get("login") or (review.get("author") or {}).get("login")
    if not is_codex_login(login):
        return False
    if str(review.get("state") or "").upper() == "DISMISSED":
        return False
    return bool(head_sha and str(review.get("commit_id") or "") == head_sha)


def _is_clean_current_codex_review(review: dict[str, Any], *, head_sha: str) -> bool:
    """True when a Codex PR review reports a clean result on the current head."""
    if not _is_current_head_codex_review(review, head_sha=head_sha):
        return False
    verdict = _clean_codex_review_verdict(review.get("body"))
    return verdict in _CLEAN_CODEX_REVIEW_VERDICTS


def _issue_comment_login(comment: dict[str, Any]) -> str | None:
    return ((comment.get("user") or {}).get("login")) or (
        (comment.get("author") or {}).get("login")
    )


def _issue_comment_created_at(comment: dict[str, Any]) -> str:
    return str(comment.get("created_at") or comment.get("createdAt") or "")


def _is_clean_current_codex_issue_comment(
    comment: dict[str, Any],
    *,
    current_head_updated_at: str | None,
) -> bool:
    """True when a Codex PR issue comment is a clean signal for the current head."""
    if not current_head_updated_at:
        return False
    if not is_codex_login(_issue_comment_login(comment)):
        return False
    created_at = _issue_comment_created_at(comment)
    if not created_at or created_at <= current_head_updated_at:
        return False
    verdict = _clean_codex_review_verdict(comment.get("body"))
    return verdict in _CLEAN_CODEX_REVIEW_VERDICTS


def _is_current_head_codex_issue_comment(
    comment: dict[str, Any],
    *,
    current_head_updated_at: str | None,
) -> bool:
    if not current_head_updated_at:
        return False
    if not is_codex_login(_issue_comment_login(comment)):
        return False
    created_at = _issue_comment_created_at(comment)
    return bool(created_at and created_at > current_head_updated_at)


def _latest_clean_codex_review_after_thread(
    reviews: list[dict[str, Any]],
    *,
    head_sha: str,
    thread_dict: dict[str, Any],
) -> dict[str, Any] | None:
    comments = _comment_nodes(thread_dict)
    thread_created_at = (comments[0].get("createdAt") if comments else None) or ""
    if not thread_created_at:
        return None

    candidates: list[dict[str, Any]] = []
    for review in reviews:
        if not _is_current_head_codex_review(review, head_sha=head_sha):
            continue
        if str(review.get("submitted_at") or "") <= thread_created_at:
            continue
        candidates.append(review)
    if not candidates:
        return None
    latest = max(candidates, key=lambda review: str(review.get("submitted_at") or ""))
    if _is_clean_current_codex_review(latest, head_sha=head_sha):
        return latest
    return None


def _latest_clean_codex_issue_comment_after_thread(
    comments: list[dict[str, Any]],
    *,
    current_head_updated_at: str | None,
    thread_dict: dict[str, Any],
) -> dict[str, Any] | None:
    comments_nodes = _comment_nodes(thread_dict)
    thread_created_at = (comments_nodes[0].get("createdAt") if comments_nodes else None) or ""
    if not thread_created_at:
        return None

    candidates: list[dict[str, Any]] = []
    for comment in comments:
        if not _is_current_head_codex_issue_comment(
            comment, current_head_updated_at=current_head_updated_at
        ):
            continue
        created_at = _issue_comment_created_at(comment)
        if created_at <= thread_created_at:
            continue
        candidates.append(comment)
    if not candidates:
        return None
    latest = max(candidates, key=_issue_comment_created_at)
    if _is_clean_current_codex_issue_comment(
        latest, current_head_updated_at=current_head_updated_at
    ):
        return latest
    return None


def _latest_clean_codex_signal_after_thread(
    *,
    reviews: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    head_sha: str,
    current_head_updated_at: str | None,
    thread_dict: dict[str, Any],
) -> tuple[str, dict[str, Any], str] | None:
    comments_nodes = _comment_nodes(thread_dict)
    thread_created_at = (comments_nodes[0].get("createdAt") if comments_nodes else None) or ""
    if not thread_created_at:
        return None

    candidates: list[tuple[str, dict[str, Any], str, bool]] = []
    for review in reviews:
        if not _is_current_head_codex_review(review, head_sha=head_sha):
            continue
        submitted_at = str(review.get("submitted_at") or "")
        if submitted_at <= thread_created_at:
            continue
        clean = _clean_codex_review_verdict(review.get("body")) in _CLEAN_CODEX_REVIEW_VERDICTS
        candidates.append(("pull_request_review", review, submitted_at, clean))

    for comment in issue_comments:
        if not _is_current_head_codex_issue_comment(
            comment, current_head_updated_at=current_head_updated_at
        ):
            continue
        created_at = _issue_comment_created_at(comment)
        if created_at <= thread_created_at:
            continue
        clean = _clean_codex_review_verdict(comment.get("body")) in _CLEAN_CODEX_REVIEW_VERDICTS
        candidates.append(("issue_comment", comment, created_at, clean))

    if not candidates:
        return None
    source, item, ts, clean = max(candidates, key=lambda candidate: candidate[2])
    if clean:
        return source, item, ts
    return None


def _known_limitation_line_candidates(thread_dict: dict[str, Any]) -> list[int | None]:
    lines: list[int | None] = []
    for key in ("line", "originalLine", "originalStartLine", "startLine"):
        if key not in thread_dict:
            continue
        raw = thread_dict.get(key)
        if raw is None:
            if key == "line" and None not in lines:
                lines.append(None)
            continue
        try:
            line = int(raw)
        except (TypeError, ValueError):
            continue
        if line not in lines:
            lines.append(line)
    return lines or [None]


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
    pr_reviews: list[dict[str, Any]] | None = None,
    pr_issue_comments: list[dict[str, Any]] | None = None,
    pr_author_login: str | None = None,
    investigator: ThreadInvestigator | None = None,
    get_diff: Callable[[], Awaitable[str]] | None = None,
    failures: list[tuple[str, str]] | None = None,
    profile_name: str | None = None,
    pr_pushed_at: str | None = None,
    current_head_updated_at: str | None = None,
    known_limitation_store: KnownLimitationStore | None = None,
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

    # Check known-limitations store for an already-accepted finding.
    # When a finding's fingerprint matches a recorded limitation, the thread
    # is suppressed (RESOLVED + annotated with the decision link) — no
    # judgment, no signal lookup, no investigator cost.
    if known_limitation_store is not None:
        expr_path = thread_dict.get("path") or "unknown"
        expr_line = thread_dict.get("line")
        body_raw = (comments_nodes[0].get("body") if comments_nodes else None) or ""
        line_candidates = _known_limitation_line_candidates(thread_dict)
        kl_entry = known_limitation_store.lookup_for_finding(
            repo=repo,
            path=expr_path,
            line_candidates=line_candidates,
            body=body_raw,
        )
        if kl_entry is not None:
            existing_close_reason_marker = _has_current_head_verdict_comment(
                comments_nodes,
                thread_id=thread_dict["id"],
                head_sha=head_sha,
                verdict=Verdict.RESOLVED,
            )
            existing_thread_conclusion_marker = _has_current_head_verdict_comment(
                comments_nodes,
                thread_id=thread_dict["id"],
                head_sha=head_sha,
                verdict=Verdict.RESOLVED,
            )
            existing_head_verdict_marker = _has_current_head_final_verdict_comment(
                comments_nodes,
                thread_id=thread_dict["id"],
                head_sha=head_sha,
            )
            _log.info(
                "known_limitation_suppressed: %s",
                json.dumps(
                    {
                        "event": "known_limitation_suppressed",
                        "repo": repo,
                        "pr": pr,
                        "thread_id": thread_dict.get("id"),
                        "fingerprint": kl_entry.fingerprint[:12],
                        "decision_link": kl_entry.decision_link,
                    }
                ),
            )
            thread_model = Thread(
                id=thread_dict["id"],
                comment_id=comment_id,
                path=expr_path,
                line=expr_line,
                codex_severity=sev_decision.codex_severity,
                effective_severity=sev_decision.effective_severity,
                demotion_reason=sev_decision.reason,
                verdict=Verdict.RESOLVED,
                verdict_reason=f"accepted known limitation — {kl_entry.decision_link}",
                github_isResolved=bool(thread_dict.get("isResolved")),
                known_limitation_link=kl_entry.decision_link,
                existing_head_verdict_marker=existing_head_verdict_marker,
                existing_close_reason_marker=existing_close_reason_marker,
                existing_thread_conclusion_marker=existing_thread_conclusion_marker,
            )
            snapshot = ThreadSnapshot(
                thread_id=thread_dict["id"],
                repo=repo,
                pr=pr,
                first_seen=now,
                last_polled=now,
                codex_comment_id=comment_id,
                path=expr_path,
                current_line=expr_line,
                codex_severity=sev_decision.codex_severity,
                effective_severity=sev_decision.effective_severity,
                demotion_reason=sev_decision.reason,
                verdict=Verdict.RESOLVED,
                verdict_history=[
                    VerdictHistoryEntry(
                        ts=now,
                        verdict=Verdict.RESOLVED,
                        reason=f"accepted known limitation — {kl_entry.decision_link}",
                    )
                ],
                evidence=Evidence(
                    thread_state=state,
                    codex_followed_up=False,
                ),
                github_state=GitHubThreadState(
                    isResolved=bool(thread_dict.get("isResolved")),
                    isOutdated=bool(thread_dict.get("isOutdated")),
                    viewerCanResolve=bool(thread_dict.get("viewerCanResolve", True)),
                ),
            )
            return thread_model, snapshot

    decision = judge(
        classification=state,
        author_reply_body=author_reply_body,
        code_changed=False,  # 7B-1: deferred to investigator wave (7B-3 adds diff verification)
        codex_followup_body=followup_body_for_judge,
        github_isResolved=bool(thread_dict.get("isResolved")),
    )

    clean_codex_review = None
    clean_codex_issue_comment = None
    clean_codex_evidence: dict[str, Any] | None = None
    clean_codex_evidence_source: str | None = None
    clean_codex_evidence_ts: str = ""
    if not thread_dict.get("isResolved"):
        clean_signal = _latest_clean_codex_signal_after_thread(
            reviews=pr_reviews or [],
            issue_comments=pr_issue_comments or [],
            head_sha=head_sha,
            current_head_updated_at=current_head_updated_at,
            thread_dict=thread_dict,
        )
        if clean_signal is not None:
            clean_codex_evidence_source, clean_codex_evidence, clean_codex_evidence_ts = (
                clean_signal
            )
            if clean_codex_evidence_source == "pull_request_review":
                clean_codex_review = clean_codex_evidence
            elif clean_codex_evidence_source == "issue_comment":
                clean_codex_issue_comment = clean_codex_evidence

        if clean_codex_evidence is not None and (
            not followup_ts or clean_codex_evidence_ts > followup_ts
        ):
            decision = VerdictDecision(
                Verdict.RESOLVED,
                ("current-head Codex clean signal reported no major issues after this thread"),
                substantive=decision.substantive,
            )
        else:
            clean_codex_review = None
            clean_codex_issue_comment = None
            clean_codex_evidence = None
            clean_codex_evidence_source = None
            clean_codex_evidence_ts = ""

    path = thread_dict.get("path") or "unknown"
    line = thread_dict.get("line")

    # Issue #63: State A threads where the Codex comment predates the most
    # recent push may have been addressed in a newer commit, even though
    # GitHub didn't mark the thread outdated.  Compare the first comment's
    # createdAt against pr_pushed_at to determine staleness.
    codex_review_stale = False
    if state == ThreadState.A and pr_pushed_at:
        comments = _comment_nodes(thread_dict)
        codex_created = (comments[0].get("createdAt") if comments else None) or ""
        # ISO-8601 timestamps are lexicographically comparable when in the
        # same timezone (GitHub always emits UTC with trailing 'Z').
        codex_review_stale = bool(codex_created and codex_created < pr_pushed_at)

    # AUGMENT invariant: gate skips when judge() already returned RESOLVED.
    # Together with `state == ThreadState.B` this preserves *every* deterministic
    # RESOLVED path (github_isResolved=true, positive Codex follow-up, future
    # code_changed=True) without LLM overrule. Do not loosen the gate without
    # extending the regression set.
    # Issue #63: also route State A threads to the investigator when the Codex
    # review predates the most recent push (codex_review_stale=True).
    llm_decision: InvestigationDecision | None = None
    llm_error_str: str | None = None
    llm_model_name: str | None = None
    investigator_eligible = state == ThreadState.B or (
        state == ThreadState.A and codex_review_stale
    )
    if (
        investigator is not None
        and get_diff is not None
        and investigator_eligible
        and decision.verdict != Verdict.RESOLVED  # skip if already deterministically RESOLVED
    ):
        raw_model_name = getattr(getattr(investigator, "_client", None), "model", None)
        llm_model_name = str(raw_model_name) if raw_model_name else None
        model_name = llm_model_name or "unknown"
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
                classification=state.value,
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

    existing_close_reason_marker = _has_current_head_verdict_comment(
        comments_nodes,
        thread_id=thread_dict["id"],
        head_sha=head_sha,
        verdict=Verdict.RESOLVED,
    )
    existing_thread_conclusion_marker = _has_current_head_verdict_comment(
        comments_nodes,
        thread_id=thread_dict["id"],
        head_sha=head_sha,
        verdict=decision.verdict,
    )
    existing_head_verdict_marker = _has_current_head_final_verdict_comment(
        comments_nodes,
        thread_id=thread_dict["id"],
        head_sha=head_sha,
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
        llm_model=llm_model_name if llm_decision else None,
        llm_confidence=llm_decision.confidence if llm_decision else None,
        llm_reason=llm_decision.reason if llm_decision else None,
        clean_codex_review_id=(clean_codex_evidence.get("id") if clean_codex_evidence else None),
        clean_codex_signal_source=clean_codex_evidence_source,
        existing_head_verdict_marker=existing_head_verdict_marker,
        existing_close_reason_marker=existing_close_reason_marker,
        existing_thread_conclusion_marker=existing_thread_conclusion_marker,
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
            codex_followed_up=bool(followup) or bool(clean_codex_evidence),
            clean_codex_review_id=(
                clean_codex_evidence.get("id") if clean_codex_evidence else None
            ),
            clean_codex_review_head=(
                clean_codex_review.get("commit_id")
                if clean_codex_review
                else (head_sha if clean_codex_issue_comment else None)
            ),
            clean_codex_review_submitted_at=clean_codex_evidence_ts or None,
            clean_codex_signal_source=clean_codex_evidence_source,
            llm_verdict=llm_decision.verdict if llm_decision else None,
            llm_model=llm_model_name if llm_decision else None,
            llm_confidence=llm_decision.confidence if llm_decision else None,
            llm_reason=llm_decision.reason if llm_decision else None,
            llm_evidence=llm_decision.evidence if llm_decision else None,
            llm_error=llm_error_str,
        ),
        github_state=GitHubThreadState(
            isResolved=bool(thread_dict.get("isResolved")),
            isOutdated=bool(thread_dict.get("isOutdated")),
            viewerCanResolve=bool(thread_dict.get("viewerCanResolve", True)),
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


def _semantic_blocker_count(threads: list[Thread]) -> int:
    """Count Codex threads that should stop Clearance from advancing."""
    return sum(
        1
        for thread in threads
        if (
            thread.verdict == Verdict.OPEN
            and thread.effective_severity in (Severity.P1, Severity.P2)
        )
        or thread.verdict == Verdict.NEEDS_HUMAN_JUDGMENT
    )


def _stage15_resolved_visual_thread_count(sync_actions: list[Stage15Action]) -> int:
    """Count semantically resolved threads whose GitHub UI may still lag."""
    return sum(
        1
        for action in sync_actions
        if action.mutation == Stage15Mutation.RESOLVE_REVIEW_THREAD
        and (action.result or {}).get("applied") is not False
    )


def _stage15_visual_unresolved_skipped_count(sync_actions: list[Stage15Action]) -> int:
    """Count Stage 1.5 skips caused by GitHub viewerCanResolve=false."""
    return sum(
        1
        for action in sync_actions
        if action.mutation == Stage15Mutation.RESOLVE_REVIEW_THREAD
        and (action.result or {}).get("skipped") is True
        and (action.result or {}).get("skip_reason") == "viewerCanResolve is false"
    )


async def _maybe_post_thread_verdict_comments(
    *,
    client: GitHubAppClient,
    repository: str,
    threads: list[Thread],
    snapshots: list[ThreadSnapshot],
    pr: int,
    head_sha: str,
    dry_run: bool,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Post per-head verdict comments for unresolved non-RESOLVED threads.

    Stage 1.5 owns RESOLVED close comments because it also performs the
    resolveReviewThread mutation. This helper covers the remaining unresolved
    OPEN / NEEDS_HUMAN_JUDGMENT cases so each current-head decision is visible
    on the conversation it judged.
    """
    actions: list[dict[str, Any]] = []
    snap_by_id = {s.thread_id: s for s in snapshots}
    verdict_reply_dedupe_cache: dict[str, Any] = {}

    for thread in threads:
        if thread.verdict == Verdict.RESOLVED:
            continue
        snap = snap_by_id.get(thread.id)
        if not snap or not snap.github_state or snap.github_state.isResolved:
            continue

        base_result: dict[str, Any] = {
            "operation": "createReviewThreadReply",
            "repo": repository,
            "pr": pr,
            "thread_id": thread.id,
            "comment_id": thread.comment_id,
            "head_sha": head_sha,
            "verdict": thread.verdict.value,
        }

        skip_reason = await _current_head_verdict_reply_skip_reason(
            client=client,
            repository=repository,
            pr=pr,
            thread=thread,
            head_sha=head_sha,
            cache=verdict_reply_dedupe_cache,
        )
        if skip_reason:
            actions.append(
                {
                    **base_result,
                    "skipped": True,
                    "skip_reason": skip_reason,
                }
            )
            continue

        if dry_run:
            actions.append({**base_result, "dry_run": True})
            continue

        comment_body = build_thread_conclusion_comment(
            thread,
            snap,
            head_sha=head_sha,
            model=model,
        )
        try:
            reply = await client.create_review_thread_reply(
                CLEARANCE_AGENT_SLUG,
                repository,
                pr,
                thread.comment_id,
                body=comment_body,
            )
        except (httpx.HTTPError, GitHubGraphQLError, RuntimeError, TimeoutError) as exc:
            failure = build_writeback_failure(
                operation="createReviewThreadReply",
                exc=exc,
                repository=repository,
                pr=pr,
                thread_id=thread.id,
            )
            _log.warning(
                "thread verdict reply failed for thread %s on %s#%s: %s",
                thread.id,
                repository,
                pr,
                json.dumps(failure),
            )
            actions.append({**base_result, "applied": False, **failure})
            continue

        actions.append(
            {
                **base_result,
                "posted": True,
                "url": (reply or {}).get("html_url"),
            }
        )

    return actions


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
    head_repo: str | None = None,
    is_fork_pr: bool = False,
    pr_author_login: str | None = None,
) -> list[Stage15Action]:
    """Stage 1.5 — resolve GitHub threads whose verdict is RESOLVED but isResolved=false.

    Posts a conclusion comment (best-effort, suppressed on failure) then calls
    resolveReviewThread. When dry_run=True, returns the planned actions without
    any GitHub writes.

    Issue #62: when *is_fork_pr* is True and *head_repo* does not have an
    installation for the Clearance app, the resolve mutation is skipped and a
    specific unsupported-context action is recorded instead so the operator sees
    a precise "manual resolve required" message rather than a generic permission
    error that repeats on every webhook.
    """
    actions: list[Stage15Action] = []
    snap_by_id = {s.thread_id: s for s in snapshots}

    # Issue #62: fork-PR head-repo accessibility.  Lazy-evaluated on first
    # thread that actually needs a mutation, so fork PRs with zero Stage 1.5
    # candidates avoid an unnecessary network request (Codex P1, review
    # 4341921018 round 5).
    fork_head_blocked: bool | None = None  # None = unchecked yet
    resolver_thread_cache: dict[str, list[dict[str, Any]] | None] = {}
    resolver_head_repo_access_cache: dict[tuple[str, str], bool] = {}
    verdict_reply_dedupe_cache: dict[str, Any] = {}

    for thread in threads:
        if thread.verdict != Verdict.RESOLVED:
            continue
        snap = snap_by_id.get(thread.id)
        if not snap or not snap.github_state:
            continue
        if snap.github_state.isResolved:
            continue

        # Issue #100: skip resolveReviewThread when the viewer (Clearance app)
        # cannot resolve this thread. This is non-fatal — the thread verdict is
        # already RESOLVED; we just cannot sync the GitHub UI state for this
        # particular thread. Record the skip as an operator-visible action
        # without triggering a writeback failure.
        if snap.github_state.viewerCanResolve is False:
            resolver_app_slug = _authorized_resolver_app_for_pr_author(pr_author_login)
            resolver_can_resolve = False
            resolver_head_repo_accessible = True
            if resolver_app_slug:
                resolver_can_resolve = await _app_can_resolve_thread(
                    client=client,
                    app_slug=resolver_app_slug,
                    repository=repository,
                    pr=pr,
                    thread_id=thread.id,
                    cache=resolver_thread_cache,
                )
                if resolver_can_resolve and is_fork_pr and head_repo and head_repo != repository:
                    resolver_head_repo_accessible = await _app_head_repo_accessible(
                        client=client,
                        app_slug=resolver_app_slug,
                        head_repo=head_repo,
                        cache=resolver_head_repo_access_cache,
                    )

            if resolver_can_resolve and resolver_head_repo_accessible:
                assert resolver_app_slug is not None
                resolver_login = _bot_login_for_app_slug(resolver_app_slug)
                comment_body = build_delegated_close_reason_comment(
                    thread,
                    snap,
                    head_sha=head_sha,
                    resolver_login=resolver_login,
                )

                if dry_run:
                    actions.append(
                        Stage15Action(
                            mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                            threadId=thread.id,
                            result={
                                "dry_run": True,
                                "repo": repository,
                                "pr": pr,
                                "thread_id": thread.id,
                                "verifier_app": CLEARANCE_AGENT_SLUG,
                                "clearance_viewerCanResolve": False,
                                "resolver_app": resolver_app_slug,
                                "resolver_login": resolver_login,
                                "resolver_viewerCanResolve": True,
                                "fallback": True,
                            },
                        )
                    )
                    continue

                try:
                    result = await client.resolve_review_thread(
                        resolver_app_slug, repository, thread.id
                    )
                except (httpx.HTTPError, GitHubGraphQLError, TimeoutError) as exc:
                    failure = build_writeback_failure(
                        operation="resolveReviewThread",
                        exc=exc,
                        repository=repository,
                        pr=pr,
                        thread_id=thread.id,
                    )
                    _log.warning(
                        "resolver fallback resolveReviewThread failed for app=%s "
                        "thread=%s on %s#%s: %s",
                        resolver_app_slug,
                        thread.id,
                        repository,
                        pr,
                        json.dumps(failure),
                    )
                    actions.append(
                        Stage15Action(
                            mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                            threadId=thread.id,
                            result={
                                "applied": False,
                                **failure,
                                "verifier_app": CLEARANCE_AGENT_SLUG,
                                "clearance_viewerCanResolve": False,
                                "resolver_app": resolver_app_slug,
                                "resolver_login": resolver_login,
                                "resolver_viewerCanResolve": True,
                                "fallback": True,
                            },
                        )
                    )
                    continue

                resolved_by = ((result or {}).get("resolvedBy") or {}).get(
                    "login"
                ) or resolver_login
                result_with_meta = {
                    **dict(result or {}),
                    "verifier_app": CLEARANCE_AGENT_SLUG,
                    "clearance_viewerCanResolve": False,
                    "resolver_app": resolver_app_slug,
                    "resolver_login": resolved_by,
                    "resolver_viewerCanResolve": True,
                    "fallback": True,
                }
                snap.github_state = GitHubThreadState(
                    isResolved=True,
                    isOutdated=snap.github_state.isOutdated,
                    viewerCanResolve=snap.github_state.viewerCanResolve,
                    resolvedBy=resolved_by,
                    synced_via=f"Stage 1.5 {resolver_app_slug} resolveReviewThread",
                    synced_at=now,
                )
                thread.github_isResolved = True
                thread.github_resolvedBy = resolved_by

                fallback_reply_result: dict[str, Any] = {"posted": False}
                skip_reason = await _current_head_verdict_reply_skip_reason(
                    client=client,
                    repository=repository,
                    pr=pr,
                    thread=thread,
                    head_sha=head_sha,
                    cache=verdict_reply_dedupe_cache,
                )
                if skip_reason:
                    fallback_reply_result = {
                        "posted": False,
                        "skipped": skip_reason,
                    }
                else:
                    try:
                        reply = await client.create_review_thread_reply(
                            CLEARANCE_AGENT_SLUG,
                            repository,
                            pr,
                            thread.comment_id,
                            body=comment_body,
                        )
                        fallback_reply_result = {
                            "posted": True,
                            "url": (reply or {}).get("html_url"),
                        }
                    except (httpx.HTTPError, RuntimeError) as exc:
                        safe = _safe_exception_fields(exc)
                        fallback_reply_result = {
                            "posted": False,
                            "error_class": safe["error_class"],
                            "status": safe["status"],
                        }
                        _log.warning(
                            "resolver fallback in-thread reply suppressed for thread %s "
                            "(mutation already applied): class=%s status=%s",
                            thread.id,
                            safe["error_class"],
                            safe["status"],
                        )
                result_with_meta["in_thread_reply"] = fallback_reply_result
                actions.append(
                    Stage15Action(
                        mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                        threadId=thread.id,
                        result=result_with_meta,
                    )
                )
                continue

            skip_reason = (
                f"Unsupported capability: Clearance cannot resolve thread "
                f"{thread.id} because viewerCanResolve is false. "
                f"The thread verdict is already RESOLVED; no action needed."
            )
            _log.info(skip_reason)
            reply_result: dict[str, Any] = {"posted": False}
            if not dry_run:
                skip_reason = await _current_head_verdict_reply_skip_reason(
                    client=client,
                    repository=repository,
                    pr=pr,
                    thread=thread,
                    head_sha=head_sha,
                    cache=verdict_reply_dedupe_cache,
                )
                if skip_reason:
                    reply_result = {
                        "posted": False,
                        "skipped": skip_reason,
                    }
                else:
                    comment_body = build_manual_close_required_comment(
                        thread, snap, head_sha=head_sha
                    )
                    try:
                        reply = await client.create_review_thread_reply(
                            CLEARANCE_AGENT_SLUG,
                            repository,
                            pr,
                            thread.comment_id,
                            body=comment_body,
                        )
                        reply_result = {
                            "posted": True,
                            "url": (reply or {}).get("html_url"),
                        }
                    except (httpx.HTTPError, RuntimeError) as exc:
                        safe = _safe_exception_fields(exc)
                        reply_result = {
                            "posted": False,
                            "error_class": safe["error_class"],
                            "status": safe["status"],
                        }
                        _log.warning(
                            "manual-close in-thread reply suppressed for thread %s "
                            "(viewerCanResolve=false): class=%s status=%s",
                            thread.id,
                            safe["error_class"],
                            safe["status"],
                        )
            actions.append(
                Stage15Action(
                    mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                    threadId=thread.id,
                    result={
                        "skipped": True,
                        "skip_reason": "viewerCanResolve is false",
                        "repo": repository,
                        "pr": pr,
                        "thread_id": thread.id,
                        "in_thread_reply": reply_result,
                        "fallback_resolver": (
                            {
                                "app_slug": resolver_app_slug,
                                "login": _bot_login_for_app_slug(resolver_app_slug),
                                "viewerCanResolve": resolver_can_resolve,
                                "headRepoAccessible": resolver_head_repo_accessible,
                            }
                            if resolver_app_slug
                            else None
                        ),
                    },
                )
            )
            continue

        comment_body = build_close_reason_comment(thread, snap, head_sha=head_sha)

        # Lazy head-repo accessibility check for fork PRs — runs only when
        # we encounter the first thread that actually needs a mutation.
        if fork_head_blocked is None and is_fork_pr and head_repo and head_repo != repository:
            if not dry_run:
                accessible = await client.check_head_repo_accessible(
                    CLEARANCE_AGENT_SLUG, head_repo
                )
                fork_head_blocked = not accessible
            else:
                try:
                    accessible = await client.check_head_repo_accessible(
                        CLEARANCE_AGENT_SLUG, head_repo
                    )
                except Exception:
                    accessible = False
                fork_head_blocked = not accessible

        # Issue #62: skip resolveReviewThread on fork PRs where the head repo
        # is not accessible. This produces a specific unsupported-context action
        # instead of a generic permission error that repeats on every webhook.
        # Must run before the dry_run gate so dry-run output also surfaces the
        # UnsupportedContext result instead of a misleading resolvable path.
        if fork_head_blocked:
            skip_reason = (
                f"Unsupported context: PR #{pr} is from fork {head_repo}. "
                f"Install {CLEARANCE_AGENT_SLUG} on {head_repo} to enable "
                f"auto-resolve, or resolve thread {thread.id} manually."
            )
            _log.warning(skip_reason)
            actions.append(
                Stage15Action(
                    mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                    threadId=thread.id,
                    result={
                        "applied": False,
                        "operation": "resolveReviewThread",
                        "error_class": "UnsupportedContext",
                        "status": None,
                        "repo": repository,
                        "pr": pr,
                        "thread_id": thread.id,
                        "suggested_action": skip_reason,
                    },
                )
            )
            continue

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

        snap.github_state = GitHubThreadState(
            isResolved=True,
            isOutdated=snap.github_state.isOutdated,
            viewerCanResolve=snap.github_state.viewerCanResolve,
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
        close_reply_result: dict[str, Any] = {"posted": False}
        skip_reason = await _current_head_verdict_reply_skip_reason(
            client=client,
            repository=repository,
            pr=pr,
            thread=thread,
            head_sha=head_sha,
            cache=verdict_reply_dedupe_cache,
        )
        if skip_reason:
            close_reply_result = {
                "posted": False,
                "skipped": skip_reason,
            }
        else:
            try:
                reply = await client.create_review_thread_reply(
                    CLEARANCE_AGENT_SLUG,
                    repository,
                    pr,
                    thread.comment_id,
                    body=comment_body,
                )
                close_reply_result = {
                    "posted": True,
                    "url": (reply or {}).get("html_url"),
                }
            except (httpx.HTTPError, RuntimeError) as exc:
                safe = _safe_exception_fields(exc)
                close_reply_result = {
                    "posted": False,
                    "error_class": safe["error_class"],
                    "status": safe["status"],
                }
                _log.warning(
                    "in-thread reply suppressed for thread %s "
                    "(Stage 1.5 mutation already applied): class=%s status=%s",
                    thread.id,
                    safe["error_class"],
                    safe["status"],
                )

        actions.append(
            Stage15Action(
                mutation=Stage15Mutation.RESOLVE_REVIEW_THREAD,
                threadId=thread.id,
                result={**dict(result or {}), "in_thread_reply": close_reply_result},
            )
        )

    return actions


def _thread_verdict_counts(threads: list[Thread]) -> dict[str, int]:
    return {verdict.value: sum(1 for t in threads if t.verdict == verdict) for verdict in Verdict}


def _thread_verdict_comment_counts(actions: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"posted": 0, "skipped": 0, "failed": 0, "dry_run": 0}
    for action in actions:
        if action.get("posted") is True:
            counts["posted"] += 1
        elif action.get("skipped") is True:
            counts["skipped"] += 1
        elif action.get("dry_run") is True:
            counts["dry_run"] += 1
        elif action.get("applied") is False:
            counts["failed"] += 1
    return counts


def _writeback_failures(
    sync_actions: list[Stage15Action],
    thread_verdict_comment_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Collect structured writeback failures from mutation/comment results.

    Returns a dict with ``writeback_failures``, ``writeback_failure_count``,
    and ``writeback_failure_reason`` only when failures are present.
    Returns an empty dict when no failures occurred.
    """
    failures: list[dict[str, Any]] = []
    for action in sync_actions:
        result = action.result or {}
        if result.get("applied") is False and result.get("operation"):
            failures.append(result)
    for result in thread_verdict_comment_actions or []:
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
    known_limitation_store: KnownLimitationStore | None = None,
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

    try:
        raw_reviews = await client.pull_request_reviews(CLEARANCE_AGENT_SLUG, repository, pr_number)
    except Exception as exc:
        safe = _safe_exception_fields(exc)
        _log.warning(
            "pull_request_reviews fetch failed for %s#%s (clean review signal disabled): "
            "class=%s status=%s",
            repository,
            pr_number,
            safe["error_class"],
            safe["status"],
        )
        raw_reviews = []

    try:
        raw_issue_comments = await client.issue_comments(
            CLEARANCE_AGENT_SLUG, repository, pr_number
        )
    except Exception as exc:
        safe = _safe_exception_fields(exc)
        _log.warning(
            "issue_comments fetch failed for %s#%s (clean issue-comment signal disabled): "
            "class=%s status=%s",
            repository,
            pr_number,
            safe["error_class"],
            safe["status"],
        )
        raw_issue_comments = []

    head_sha = (pr_data.get("head") or {}).get("sha") or ""
    pr_title = pr_data.get("title")
    pr_author_login: str | None = (pr_data.get("user") or {}).get("login") or None
    base_branch = (pr_data.get("base") or {}).get("ref") or "main"
    # Issue #63: PR pushed_at timestamp for stale-thread detection.
    # A Codex thread whose first comment predates the most recent push may have
    # been addressed in a newer commit even though GitHub didn't mark it outdated.
    pr_pushed_at: str | None = pr_data.get("pushed_at") or None
    try:
        current_head_updated_at = (
            await client.pull_request_head_updated_at(CLEARANCE_AGENT_SLUG, repository, pr_number)
            if head_sha
            else None
        )
    except Exception as exc:
        safe = _safe_exception_fields(exc)
        _log.warning(
            "head update timestamp fetch failed for %s#%s head=%s "
            "(clean issue-comment signal disabled): class=%s status=%s",
            repository,
            pr_number,
            head_sha,
            safe["error_class"],
            safe["status"],
        )
        current_head_updated_at = None
    # Issue #62: detect fork PRs. The REST API always includes head.repo.full_name
    # and base.repo.full_name; when they differ the PR is from a fork.
    head_repo: str | None = (pr_data.get("head") or {}).get("repo", {}).get("full_name") or None
    base_repo: str | None = (pr_data.get("base") or {}).get("repo", {}).get("full_name") or None
    is_fork_pr = bool(head_repo and base_repo and head_repo != base_repo)
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
            pr_reviews=raw_reviews,
            pr_issue_comments=raw_issue_comments,
            pr_author_login=pr_author_login,
            investigator=investigator,
            get_diff=get_diff,
            failures=investigator_failures,
            profile_name=default_profile_name,
            pr_pushed_at=pr_pushed_at,
            current_head_updated_at=current_head_updated_at,
            known_limitation_store=known_limitation_store,
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

    thread_verdict_comment_actions = await _maybe_post_thread_verdict_comments(
        client=client,
        repository=repository,
        threads=threads,
        snapshots=snapshots,
        pr=pr_number,
        head_sha=head_sha,
        dry_run=dry_run,
    )

    sync_actions = await _maybe_sync_stage_15(
        client=client,
        repository=repository,
        threads=threads,
        snapshots=snapshots,
        pr=pr_number,
        head_sha=head_sha,
        dry_run=dry_run,
        now=now,
        head_repo=head_repo,
        is_fork_pr=is_fork_pr,
        pr_author_login=pr_author_login,
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
    semantic_blockers = _semantic_blocker_count(threads)
    visual_unresolved_threads = _stage15_resolved_visual_thread_count(sync_actions)
    visual_unresolved_skipped_threads = _stage15_visual_unresolved_skipped_count(sync_actions)
    thread_verdict_counts = _thread_verdict_counts(threads)
    thread_verdict_comment_counts = _thread_verdict_comment_counts(thread_verdict_comment_actions)

    # CHG-1813: Aggregate Stage 1.5 writeback failures before persistence so
    # state/history consumers see the same error status as the readiness panel.
    wb_failures = _writeback_failures(sync_actions, thread_verdict_comment_actions)
    persisted_status = status
    persisted_reason = reason
    if persisted_status == Status.READY and visual_unresolved_skipped_threads:
        noun = "thread" if visual_unresolved_skipped_threads == 1 else "threads"
        persisted_reason = (
            f"{reason}; {visual_unresolved_skipped_threads} outdated visual-unresolved "
            f"{noun} still visible (viewerCanResolve=false; not blocking)"
        )
    if wb_failures:
        persisted_status = Status.ERROR
        persisted_reason = wb_failures["writeback_failure_reason"]

    record = PollRecord(
        ts=now,
        repo=repository,
        pr=pr_number,
        title=pr_title,
        head_sha=head_sha,
        status=persisted_status,
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
        "status": persisted_status.value,
        "reason": persisted_reason,
        "sync_actions": [a.model_dump() for a in sync_actions],
        "sync_actions_count": len(sync_actions),
        "dry_run": dry_run,
        "head_sha": head_sha,
        "unresolved_codex_thread_count": sum(1 for t in threads if t.verdict != Verdict.RESOLVED),
        "semantic_blocker_count": semantic_blockers,
        "visual_unresolved_thread_count": visual_unresolved_threads,
        "visual_unresolved_skipped_thread_count": visual_unresolved_skipped_threads,
        "thread_verdict_counts": thread_verdict_counts,
        "thread_verdict_comment_actions": thread_verdict_comment_actions,
        "thread_verdict_comment_actions_count": len(thread_verdict_comment_actions),
        "thread_verdict_comment_posted_count": thread_verdict_comment_counts["posted"],
        "thread_verdict_comment_skipped_count": thread_verdict_comment_counts["skipped"],
        "thread_verdict_comment_failed_count": thread_verdict_comment_counts["failed"],
        "thread_verdict_comment_dry_run_count": thread_verdict_comment_counts["dry_run"],
    }
    if investigator_failures:
        result_dict["investigator_error_count"] = len(investigator_failures)
        result_dict["investigator_error_thread_ids"] = [tid for tid, _ in investigator_failures]
        result_dict["investigator_error_reason"] = investigator_failures[0][1]

    # Only add keys when failures are present; successful results omit them.
    if wb_failures:
        result_dict.update(wb_failures)

    return result_dict
