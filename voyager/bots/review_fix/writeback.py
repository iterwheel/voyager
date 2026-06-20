"""Writeback dispatcher for the governed PR review-fix bot."""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from voyager.bots.assembly.adapters import (
    AdapterExecutionContext,
    AdapterResult,
    DryRunAdapter,
    ExecutionAdapter,
    select_execution_adapter,
)
from voyager.bots.assembly.constants import ASSEMBLY_AGENT_SLUG, FORBIDDEN_OPERATIONS
from voyager.bots.assembly.job_contract import AssemblyJobContract
from voyager.bots.assembly.writeback import _build_adapter_context, _execute_adapter
from voyager.bots.clearance.classify import (
    classify_thread,
    is_codex_thread,
    latest_author_reply,
)
from voyager.core.config import ReviewFixConfig
from voyager.core.writeback import build_writeback_failure, dry_run_enabled
from voyager.governance.audit_log import ReviewFixAuditLog
from voyager.governance.enablement import Autonomy, EnablementConfig
from voyager.governance.loop_runner import (
    ReviewFixClassification,
    ReviewFixFinding,
    ReviewFixLoopFixResult,
    ReviewFixLoopOutcome,
    ReviewFixLoopOutcomeStatus,
    ReviewFixLoopRunner,
    ReviewFixLoopRunnerError,
    ReviewFixLoopSeams,
    ReviewFixLoopStatus,
    ReviewFixLoopWork,
)

from .constants import REVIEW_FIX_AGENT_SLUG, REVIEW_FIX_COMMENT_MARKER

_DEFAULT_CATEGORY = "codex-review"


@dataclass
class _LoopContext:
    client: Any
    route: dict[str, Any]
    repository: str
    pull: dict[str, Any]
    threads: list[dict[str, Any]]
    enablement: EnablementConfig
    audit_log_path: Path
    dry_run: bool
    cfg: Any | None
    handled: set[str] = field(default_factory=set)
    adapter_results: list[dict[str, Any]] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    expected_head_sha: str = ""
    event_loop: asyncio.AbstractEventLoop | None = None
    event_loop_thread_id: int | None = None


async def dispatch_review_fix_writeback(
    client: Any,
    route: dict[str, Any],
    *,
    repository: str | None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """Run one bounded review-fix loop for an explicitly requested PR."""
    validation = route.get("validation") or {}
    writeback = route.get("writeback") or {}
    pr_number = validation.get("pr_number") or validation.get("issue_number")
    base_result: dict[str, Any] = {
        "applied": False,
        "dry_run": _is_dry_run(route, cfg),
        "status": "review_fix_pending",
        "refusal": writeback.get("refusal"),
        "pr_number": pr_number,
        "audit_log_path": None,
        "outcome": None,
        "adapter_results": [],
        "contracts": [],
        "auto_merge": False,
        "review_fix_comment_id": None,
        "writeback_failures": [],
    }

    if writeback.get("refusal") is not None:
        base_result["status"] = "review_fix_refused"
        return await _post_refusal_comment(client, route, repository, base_result)
    if not repository:
        return _refused(base_result, "missing_repository")
    if not pr_number:
        return await _refuse_with_comment(
            client,
            route,
            repository,
            base_result,
            "missing_pr_number",
        )

    enablement = _enablement_from_config(cfg)
    if enablement is None:
        return await _refuse_with_comment(
            client,
            route,
            repository,
            base_result,
            "missing_review_fix_enablement",
        )
    if enablement.autonomy is not Autonomy.L3 or enablement.envelope is None:
        return await _refuse_with_comment(
            client,
            route,
            repository,
            base_result,
            "review_fix_requires_l3_envelope",
        )

    try:
        pull = await client.pull_request(ASSEMBLY_AGENT_SLUG, repository, int(pr_number))
    except Exception:
        return await _refuse_with_comment(
            client,
            route,
            repository,
            base_result,
            "pull_request_fetch_failed",
        )

    guard = _guard_pull_request_target(pull, repository)
    if guard is not None:
        return await _refuse_with_comment(client, route, repository, base_result, guard)

    try:
        threads = await client.pull_request_review_threads(
            app_slug=ASSEMBLY_AGENT_SLUG,
            repo=repository,
            pull_number=int(pr_number),
        )
    except Exception:
        return await _refuse_with_comment(
            client,
            route,
            repository,
            base_result,
            "review_thread_fetch_failed",
        )

    audit_log_path = _audit_log_path(cfg, repository, int(pr_number))
    context = _LoopContext(
        client=client,
        route=route,
        repository=repository,
        pull=pull,
        threads=list(threads or []),
        enablement=enablement,
        audit_log_path=audit_log_path,
        dry_run=base_result["dry_run"],
        cfg=cfg,
        expected_head_sha=_expected_head_sha(pull),
        event_loop=asyncio.get_running_loop(),
        event_loop_thread_id=threading.get_ident(),
    )

    outcome = await _run_loop_in_worker(context)
    records = ReviewFixAuditLog(audit_log_path).read_all()
    base_result.update(
        {
            "applied": not base_result["dry_run"]
            and any(item.get("status") == "executed" for item in context.adapter_results),
            "status": f"review_fix_{outcome.status.value}",
            "audit_log_path": str(audit_log_path),
            "audit_records": len(records),
            "outcome": _outcome_dict(outcome),
            "adapter_results": context.adapter_results,
            "contracts": context.contracts,
            "refusal": None,
        }
    )
    return base_result


def _run_loop(context: _LoopContext) -> ReviewFixLoopOutcome:
    runner = ReviewFixLoopRunner(
        enablement=context.enablement,
        audit_log=ReviewFixAuditLog(context.audit_log_path),
        seams=ReviewFixLoopSeams(
            gather=lambda status: _gather_findings(context, status),
            classify=lambda finding, status: _classify_finding(context, finding, status),
            fix=lambda work, status: _fix_finding(context, work, status),
        ),
        root_path=Path.cwd(),
    )
    return runner.run()


async def _run_loop_in_worker(context: _LoopContext) -> ReviewFixLoopOutcome:
    """Run sync loop logic off-thread while async client calls stay on request loop."""
    return await asyncio.to_thread(_run_loop, context)


def _gather_findings(
    context: _LoopContext,
    status: ReviewFixLoopStatus,
) -> list[ReviewFixFinding]:
    _ = status
    findings: list[ReviewFixFinding] = []
    for thread in context.threads:
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id or thread_id in context.handled:
            continue
        if not _thread_is_actionable(thread):
            continue
        findings.append(
            ReviewFixFinding(
                finding_id=thread_id,
                category=_finding_category(thread),
            )
        )
    return findings


def _classify_finding(
    context: _LoopContext,
    finding: ReviewFixFinding,
    status: ReviewFixLoopStatus,
) -> ReviewFixClassification:
    _ = status
    thread = _thread_for_finding(context, finding)
    if thread is None:
        return ReviewFixClassification(fixable=False, reason="thread_not_found")
    if thread.get("isResolved"):
        return ReviewFixClassification(fixable=False, reason="thread_resolved")
    if thread.get("isOutdated"):
        return ReviewFixClassification(fixable=False, reason="thread_outdated")
    state = classify_thread(thread)
    author_login = ((context.pull.get("user") or {}).get("login")) or None
    if latest_author_reply(thread, author_login=author_login) is not None:
        return ReviewFixClassification(fixable=False, reason="author_reply_present")
    return ReviewFixClassification(fixable=True, reason=f"clearance_state={state.value}")


def _fix_finding(
    context: _LoopContext,
    work: ReviewFixLoopWork,
    status: ReviewFixLoopStatus,
) -> ReviewFixLoopFixResult:
    if not context.dry_run:
        _ensure_expected_head_current(context)
    thread = _thread_for_finding(context, work.finding)
    contract = _build_contract(context, work.finding, thread)
    adapter, adapter_context = _run_context_coroutine(
        context,
        _prepare_adapter(
            context,
            contract,
            finding_id=work.finding.finding_id,
            round_number=status.round_number,
        ),
    )
    adapter_result = _run_context_coroutine(
        context,
        _execute_adapter(adapter, contract, adapter_context),
    )
    adapter_dict = _adapter_result_dict(adapter_result)
    context.adapter_results.append(adapter_dict)
    context.contracts.append(contract.to_dict())

    if adapter_result.status == "dry_run" and context.dry_run:
        context.handled.add(work.finding.finding_id)
    elif adapter_result.status == "executed" and adapter_result.commit_shas:
        context.expected_head_sha = str(adapter_result.commit_shas[-1])
        cleared = _finding_cleared_after_execution(context, work.finding)
        if cleared is None:
            raise ReviewFixLoopRunnerError("post_execution_thread_refresh_failed")
        if cleared:
            context.handled.add(work.finding.finding_id)

    commit = _result_commit(adapter_result, work.finding.finding_id)
    envelope = context.enablement.envelope
    assert envelope is not None
    return ReviewFixLoopFixResult(
        commit=commit,
        verdict=adapter_result.status,
        tests=(
            f"assembly_status={adapter_result.status}",
            f"round={status.round_number}",
            f"branch={contract.branch_name}",
            f"verify_command={envelope.verify_command}",
        ),
    )


async def _prepare_adapter(
    context: _LoopContext,
    contract: AssemblyJobContract,
    *,
    finding_id: str | None = None,
    round_number: int | None = None,
) -> tuple[ExecutionAdapter, AdapterExecutionContext]:
    adapter: ExecutionAdapter = (
        DryRunAdapter() if context.dry_run else select_execution_adapter(cfg=context.cfg)
    )
    adapter_context = await _build_adapter_context(
        context.client,
        adapter,
        context.repository,
        is_dry_run=context.dry_run,
        audit_id=_audit_id(
            context,
            contract,
            finding_id=finding_id,
            round_number=round_number,
        ),
        cfg=context.cfg,
    )
    expected_head_sha = context.expected_head_sha or _expected_head_sha(context.pull)
    if expected_head_sha and not context.dry_run:
        adapter_context = replace(adapter_context, expected_remote_sha=expected_head_sha)
    return adapter, adapter_context


def _build_contract(
    context: _LoopContext,
    finding: ReviewFixFinding,
    thread: dict[str, Any] | None,
) -> AssemblyJobContract:
    pull = context.pull
    base = pull.get("base") or {}
    head_ref, expected_head_sha = _reviewed_head(context)
    body = _contract_body(context, finding, thread)
    now = datetime.now(UTC).isoformat()
    envelope = context.enablement.envelope
    assert envelope is not None
    return AssemblyJobContract(
        repository=context.repository,
        issue_number=int(pull.get("number") or 0),
        issue_url=str(pull.get("html_url") or ""),
        issue_title=f"Address PR review finding {finding.finding_id}",
        issue_body=body,
        branch_name=head_ref,
        base_branch=str(base.get("ref") or "main"),
        task_summary=f"Address PR review finding {finding.finding_id}",
        acceptance_criteria=[
            "Address the referenced PR review finding.",
            "Keep the existing PR branch as the only target branch.",
            "Use the recorded expected head SHA as the stale-head guard before any mutating publish.",
            "Do not merge, approve, or resolve review threads.",
        ],
        forbidden_operations=FORBIDDEN_OPERATIONS,
        verification_commands=(envelope.verify_command,),
        delivery_id=str(context.route.get("delivery_id") or ""),
        requested_at=now,
        extra={
            "review_fix": {
                "finding_id": finding.finding_id,
                "category": finding.category,
                "pr_number": int(pull.get("number") or 0),
                "expected_head_sha": expected_head_sha,
            }
        },
    )


def _contract_body(
    context: _LoopContext,
    finding: ReviewFixFinding,
    thread: dict[str, Any] | None,
) -> str:
    first_comment = _first_comment(thread or {})
    body = str(first_comment.get("body") or "").strip()
    path = str((thread or {}).get("path") or "")
    line = (thread or {}).get("line") or (thread or {}).get("originalLine")
    location = f"{path}:{line}" if path and line else path or "unknown location"
    return (
        "## Problem / Goal\n\n"
        f"Address PR review finding `{finding.finding_id}` at `{location}` on "
        f"{context.repository}#{context.pull.get('number')}.\n\n"
        "Review finding:\n\n"
        f"{body}\n\n"
        "## Acceptance Criteria\n\n"
        "- [ ] The referenced review finding is addressed in the PR branch.\n"
        "- [ ] The existing PR branch remains the only target branch.\n"
        "- [ ] No merge, approval, or review-thread resolution is performed.\n"
    )


def _reviewed_head(context: _LoopContext) -> tuple[str, str]:
    pull = context.pull
    head = pull.get("head") or {}
    return str(head.get("ref") or ""), context.expected_head_sha or _expected_head_sha(pull)


def _expected_head_sha(pull: dict[str, Any]) -> str:
    head = pull.get("head") or {}
    return str(head.get("sha") or "")


def _thread_is_actionable(thread: dict[str, Any]) -> bool:
    return (
        is_codex_thread(thread)
        and not bool(thread.get("isResolved"))
        and not bool(thread.get("isOutdated"))
    )


def _thread_for_finding(
    context: _LoopContext,
    finding: ReviewFixFinding,
) -> dict[str, Any] | None:
    for thread in context.threads:
        if str(thread.get("id") or "") == finding.finding_id:
            return thread
    return None


def _finding_cleared_after_execution(
    context: _LoopContext,
    finding: ReviewFixFinding,
) -> bool | None:
    refreshed_threads = _refetch_review_threads(context)
    if refreshed_threads is None:
        return None
    context.threads = refreshed_threads
    thread = _thread_for_finding(context, finding)
    return thread is None or not _thread_is_actionable(thread)


def _refetch_review_threads(context: _LoopContext) -> list[dict[str, Any]] | None:
    pr_number = int(context.pull.get("number") or 0)
    if pr_number <= 0:
        return None
    try:
        threads = _run_context_coroutine(
            context,
            context.client.pull_request_review_threads(
                app_slug=ASSEMBLY_AGENT_SLUG,
                repo=context.repository,
                pull_number=pr_number,
            ),
        )
    except Exception:
        return None
    return list(threads or [])


def _ensure_expected_head_current(context: _LoopContext) -> None:
    expected_sha = context.expected_head_sha or _expected_head_sha(context.pull)
    if not expected_sha:
        raise ReviewFixLoopRunnerError("missing_expected_head_sha")
    pr_number = int(context.pull.get("number") or 0)
    if pr_number <= 0:
        raise ReviewFixLoopRunnerError("missing_pr_number_for_stale_head_guard")
    pull = _run_context_coroutine(
        context,
        context.client.pull_request(ASSEMBLY_AGENT_SLUG, context.repository, pr_number),
    )
    actual_sha = _expected_head_sha(pull)
    if actual_sha != expected_sha:
        raise ReviewFixLoopRunnerError(
            "stale_pr_head: "
            f"expected={expected_sha[:12] or 'none'} actual={actual_sha[:12] or 'none'}"
        )
    context.pull = pull
    context.expected_head_sha = actual_sha


def _run_context_coroutine(context: _LoopContext, coro: Any) -> Any:
    loop = context.event_loop
    if (
        loop is not None
        and loop.is_running()
        and context.event_loop_thread_id != threading.get_ident()
    ):
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return asyncio.run(coro)


def _finding_category(thread: dict[str, Any]) -> str:
    raw = thread.get("findingKind") or _first_comment(thread).get("findingKind")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _DEFAULT_CATEGORY


def _first_comment(thread: dict[str, Any]) -> dict[str, Any]:
    comments = (thread.get("comments") or {}).get("nodes") or []
    if comments and isinstance(comments[0], dict):
        return comments[0]
    return {}


def _is_dry_run(route: dict[str, Any], cfg: Any | None) -> bool:
    flags = (route.get("writeback") or {}).get("command_flags") or {}
    return dry_run_enabled(cfg) or bool(flags.get("dry_run"))


def _enablement_from_config(cfg: Any | None) -> EnablementConfig | None:
    review_fix: ReviewFixConfig | None = getattr(cfg, "review_fix", None)
    if review_fix is None:
        return None
    return review_fix.enablement


def _audit_log_path(cfg: Any | None, repository: str, pr_number: int) -> Path:
    review_fix: ReviewFixConfig | None = getattr(cfg, "review_fix", None)
    base = (
        review_fix.audit_dir
        if review_fix is not None and review_fix.audit_dir is not None
        else Path(getattr(cfg, "work_dir", Path(".voyager/state"))) / "review-fix"
    )
    owner, _, name = repository.partition("/")
    owner = _path_token(owner or "unknown")
    name = _path_token(name or repository)
    return base / owner / name / f"{int(pr_number)}.jsonl"


def _path_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "unknown"


def _guard_pull_request_target(pull: dict[str, Any], repository: str) -> str | None:
    if str(pull.get("state") or "open").lower() != "open":
        return "pull_request_not_open"
    head = pull.get("head") or {}
    base = pull.get("base") or {}
    head_ref = str(head.get("ref") or "").strip()
    base_ref = str(base.get("ref") or "").strip()
    head_repo = (head.get("repo") or {}).get("full_name")
    base_repo = (base.get("repo") or {}).get("full_name")
    default_branch = str((base.get("repo") or {}).get("default_branch") or "").strip()
    if not head_ref:
        return "missing_head_branch"
    if not isinstance(head_repo, str) or not head_repo.strip():
        return "missing_pr_repo_metadata"
    if not isinstance(base_repo, str) or not base_repo.strip():
        return "missing_pr_repo_metadata"
    if head_repo != repository or base_repo != repository:
        return "fork_pull_request_refused"
    default_candidates = {item for item in (base_ref, default_branch, "main", "master") if item}
    if head_ref in default_candidates:
        return "default_branch_target_refused"
    return None


def _adapter_result_dict(result: AdapterResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "commit_shas": list(result.commit_shas),
        "summary": result.summary,
        "details": result.details,
    }


def _result_commit(result: AdapterResult, finding_id: str) -> str:
    if result.commit_shas:
        return str(result.commit_shas[-1])
    return f"{result.status}:{finding_id}"


def _audit_id(
    context: _LoopContext,
    contract: AssemblyJobContract,
    *,
    finding_id: str | None = None,
    round_number: int | None = None,
) -> str:
    delivery = str(context.route.get("delivery_id") or "manual")
    parts = ["review-fix", str(contract.issue_number), _path_token(delivery)]
    if round_number is not None:
        parts.append(f"r{round_number}")
    if finding_id:
        parts.append(_path_token(finding_id))
    return "-".join(parts)


def _outcome_dict(outcome: ReviewFixLoopOutcome) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": outcome.status.value,
        "rounds_run": outcome.rounds_run,
        "clean_rounds": outcome.clean_rounds,
        "escalation": outcome.escalation,
    }
    if outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH and outcome.kill_switch_path:
        data["kill_switch_path"] = str(outcome.kill_switch_path)
    return data


async def _refuse_with_comment(
    client: Any,
    route: dict[str, Any],
    repository: str,
    result: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return await _post_refusal_comment(client, route, repository, _refused(result, reason))


async def _post_refusal_comment(
    client: Any,
    route: dict[str, Any],
    repository: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    validation = route.get("validation") or {}
    issue_number = validation.get("issue_number") or validation.get("pr_number")
    if result.get("dry_run") or not issue_number or not repository:
        return result

    try:
        comment = await client.upsert_issue_comment(
            REVIEW_FIX_AGENT_SLUG,
            repository,
            int(issue_number),
            marker=REVIEW_FIX_COMMENT_MARKER,
            body=_build_refusal_comment(result.get("refusal") or {}),
        )
        result["review_fix_comment_id"] = comment.get("id")
    except (httpx.HTTPError, TimeoutError) as exc:
        result.setdefault("writeback_failures", []).append(
            build_writeback_failure(
                operation="upsertRefusalComment",
                exc=exc,
                repository=repository,
                issue=int(issue_number),
            )
        )
    return result


def _build_refusal_comment(refusal: dict[str, Any]) -> str:
    reason = str(refusal.get("reason") or "unknown")
    lines = [
        REVIEW_FIX_COMMENT_MARKER,
        "**Review-fix refused this invocation.**",
        "",
        f"Reason: `{reason}`",
    ]
    if reason == "unauthorized_actor":
        actor_login = str(refusal.get("actor_login") or "unknown")
        actor_association = str(refusal.get("actor_association") or "none")
        lines.extend(
            [
                "",
                f"Actor: `{actor_login}` (association: `{actor_association}`)",
                "",
                "Review-fix only mutates PR branches when the triggering actor is authorized.",
            ]
        )
    return "\n".join(lines).strip()


def _refused(result: dict[str, Any], reason: str) -> dict[str, Any]:
    result["status"] = "review_fix_refused"
    result["refusal"] = {"reason": reason}
    return result
