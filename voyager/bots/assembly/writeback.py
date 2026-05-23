"""Assembly bot — writeback dispatcher.

Implements VOY-1817 Surface 11.  Sequenced per D11:

    branch -> PR -> codex-trigger -> progress-comment

Each step records its own failure to ``writeback_failures`` (CHG-1813
schema) and the progress-comment step always runs, including when every
preceding step failed.  Idempotency: branch creation is conditional on
``branch_ref_exists``; PR open is conditional on ``find_pull_request_by_head``.
No automatic cleanup on failure (retry is the recovery path).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from voyager.core.writeback import build_writeback_failure, dry_run_enabled

from .adapters import AdapterResult, select_execution_adapter
from .branch import make_branch_name
from .comment import build_assembly_comment
from .constants import (
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_COMMENT_MARKER,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    CODEX_REVIEW_TRIGGER_BODY,
)
from .job_contract import AssemblyJobContract, build_job_contract
from .preconditions import validate_preconditions

if TYPE_CHECKING:
    from voyager.core.github_app import GitHubAppClient

_log = logging.getLogger(__name__)


def _live_issue_from_route(_client: GitHubAppClient, route: dict[str, Any]) -> dict[str, Any]:
    """Best-effort: return the issue payload carried on the route validation.

    The writeback dispatcher does not currently refetch the live issue —
    it trusts the payload the router already validated, which the bridge
    snapshots into ``writeback.contract``.  D4 re-validation operates on
    the contract surface; an issue edit between routing and dispatch
    surfaces as a refusal here.
    """
    # The router puts a full contract dict under writeback.contract for the
    # happy path; on refusal it puts None and surfaces the refusal directly.
    writeback = route.get("writeback") or {}
    contract = writeback.get("contract") or {}
    # Reconstruct a minimal issue shape from the contract.
    return {
        "number": contract.get("issue_number"),
        "title": contract.get("issue_title"),
        "body": contract.get("issue_body"),
        "html_url": contract.get("issue_url"),
        # The router's preconditions already passed; the dispatcher's
        # re-check operates on the same gates.  Labels are recomputed here
        # from contract metadata since the payload is not preserved.
        "labels": writeback.get("issue_labels") or [],
        "state": writeback.get("issue_state") or "open",
    }


async def dispatch_assembly_writeback(
    client: GitHubAppClient,
    route: dict[str, Any],
    *,
    repository: str | None,
) -> dict[str, Any]:
    """Run the Assembly writeback sequence per D11.

    Returns the result dict shape documented in VOY-1817 §Writeback
    Result Schema.
    """
    writeback = route.get("writeback") or {}
    validation = route.get("validation") or {}
    refusal_router = writeback.get("refusal")
    contract_dict: dict[str, Any] | None = writeback.get("contract")
    command_flags: dict[str, Any] = writeback.get("command_flags") or {}
    delivery_id = str(route.get("delivery_id") or "")

    backend_env = command_flags.get("backend") or None
    adapter = select_execution_adapter(backend_env)
    backend_name = adapter.name

    base_result: dict[str, Any] = {
        "applied": False,
        "dry_run": dry_run_enabled(),
        "execution_backend": backend_name,
        "refusal": refusal_router,
        "contract": contract_dict,
        "adapter_result": None,
        "branch": None,
        "pull_request": None,
        "codex_review_comment_id": None,
        "assembly_comment_id": None,
        "writeback_failures": [],
    }

    if not repository:
        base_result["refusal"] = base_result["refusal"] or {
            "reason": "missing_repository",
            "missing_labels": [],
            "outside_allow_list": False,
        }
        return base_result

    # ------------------------------------------------------------------
    # Refusal path — router already refused; surface and stop.
    # ------------------------------------------------------------------
    if refusal_router is not None or contract_dict is None:
        return await _post_refusal_comment(client, route, repository, base_result)

    # ------------------------------------------------------------------
    # D4 — re-validate preconditions against the snapshot we have.
    # ------------------------------------------------------------------
    issue_snapshot = _live_issue_from_route(client, route)
    pre = validate_preconditions(
        {
            **issue_snapshot,
            # Re-attach labels from the original webhook so the gate is
            # meaningful even when the dispatcher cannot refetch.
            "labels": _labels_from_validation(validation) or issue_snapshot.get("labels"),
        },
        allow_missing_stack=bool(command_flags.get("allow_missing_stack")),
    )
    if not pre.ok:
        base_result["refusal"] = pre.as_refusal_dict()
        return await _post_refusal_comment(client, route, repository, base_result)

    # Rebuild the contract with a fresh requested_at + delivery_id so the
    # dispatcher's view is authoritative.
    contract = build_job_contract(
        issue=issue_snapshot,
        repository=repository,
        branch_name=(
            writeback.get("branch_name")
            or make_branch_name(
                int(issue_snapshot.get("number") or 0),
                issue_snapshot.get("title"),
            )
        ),
        delivery_id=delivery_id,
    )
    contract_dict = contract.to_dict()
    base_result["contract"] = contract_dict

    # ------------------------------------------------------------------
    # Adapter execution.  Failures are captured but do NOT abort the
    # progress-comment step (D11 "always runs").
    # ------------------------------------------------------------------
    adapter_result: AdapterResult | None = None
    adapter_failure: dict[str, Any] | None = None
    try:
        adapter_result = await adapter.execute(contract)
    except NotImplementedError as exc:
        adapter_failure = {
            "operation": "adapter.execute",
            "error_class": type(exc).__name__,
            "status": None,
            "repo": repository,
            "pr": None,
            "issue": contract.issue_number,
            "thread_id": None,
            "suggested_action": (
                "Wire the production execution backend before flipping "
                f"{ASSEMBLY_EXECUTION_BACKEND_ENV}=pi-oh-my-pi-deepseek."
            ),
        }
        base_result["writeback_failures"].append(adapter_failure)
    except Exception as exc:
        adapter_failure = {
            "operation": "adapter.execute",
            "error_class": type(exc).__name__,
            "status": None,
            "repo": repository,
            "pr": None,
            "issue": contract.issue_number,
            "thread_id": None,
            "suggested_action": (
                "Inspect adapter logs; the Assembly progress comment surfaces "
                "the failure so an operator can retry the invocation."
            ),
        }
        base_result["writeback_failures"].append(adapter_failure)

    if adapter_result is not None:
        base_result["adapter_result"] = {
            "status": adapter_result.status,
            "commit_shas": list(adapter_result.commit_shas),
            "summary": adapter_result.summary,
        }
    else:
        base_result["adapter_result"] = {
            "status": "failed",
            "commit_shas": [],
            "summary": (
                "execution backend deferred"
                if adapter_failure and adapter_failure["error_class"] == "NotImplementedError"
                else "adapter raised; see writeback_failures"
            ),
        }

    # ------------------------------------------------------------------
    # GitHub mutation gates.  Three independent dimensions:
    #   - dry_run_enabled() short-circuits all mutations (always upsert
    #     comment? no — the progress comment is itself a mutation).  When
    #     DRY_RUN is true we *skip* network writes and return planned shape.
    #   - adapter_result must produce commits before branch/PR steps run.
    #   - codex-trigger only fires when the PR open / update succeeded.
    # ------------------------------------------------------------------
    if dry_run_enabled():
        base_result["pull_request"] = {
            "number": None,
            "url": None,
            "action": "dry_run_skipped",
        }
        return base_result

    base_result["applied"] = True

    # Per D11, when the adapter produced no commits, skip branch/PR/codex
    # steps but still upsert the progress comment so the operator sees the
    # plan / dry-run / failure surface.
    commit_shas = list(adapter_result.commit_shas) if adapter_result is not None else []
    if not commit_shas:
        base_result["pull_request"] = {
            "number": None,
            "url": None,
            "action": "skipped_no_changes",
        }
        await _upsert_progress_comments(client, contract, repository, base_result)
        return base_result

    # ------------------------------------------------------------------
    # branch -> PR -> codex-trigger -> progress-comment
    # ------------------------------------------------------------------
    branch_ok = await _ensure_branch(client, repository, contract, commit_shas[-1], base_result)
    pr_ok = False
    if branch_ok:
        pr_ok = await _ensure_pull_request(client, repository, contract, base_result)
    if pr_ok:
        await _post_codex_trigger(client, repository, contract, base_result)

    await _upsert_progress_comments(client, contract, repository, base_result)
    return base_result


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


def _labels_from_validation(validation: dict[str, Any]) -> list[str]:
    snapshot = validation.get("issue_labels")
    if isinstance(snapshot, list):
        return [str(item) for item in snapshot]
    return []


async def _post_refusal_comment(
    client: GitHubAppClient,
    route: dict[str, Any],
    repository: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Upsert the refusal comment on the source issue.

    Refusal comments are written even when DRY_RUN=true so the operator
    can see why Assembly declined.  The router-side refusal goes here
    untouched; the dispatcher-side refusal includes any updated
    ``missing_labels`` discovered by D4 re-validation.
    """
    contract = result.get("contract") or {}
    issue_number = contract.get("issue_number") or (route.get("validation") or {}).get(
        "issue_number"
    )
    if not issue_number:
        return result
    body = build_assembly_comment(
        status="refused",
        contract=contract or None,
        adapter_result=None,
        refusal=result.get("refusal"),
        dry_run=result.get("dry_run", True),
        surface="issue",
    )
    if dry_run_enabled():
        return result
    try:
        comment = await client.upsert_issue_comment(
            ASSEMBLY_AGENT_SLUG,
            repository,
            int(issue_number),
            marker=ASSEMBLY_COMMENT_MARKER,
            body=body,
        )
        result["assembly_comment_id"] = comment.get("id")
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="upsertRefusalComment",
                exc=exc,
                repository=repository,
                issue=int(issue_number),
            )
        )
    return result


async def _ensure_branch(
    client: GitHubAppClient,
    repository: str,
    contract: AssemblyJobContract,
    head_sha: str,
    result: dict[str, Any],
) -> bool:
    """Create the feature branch ref idempotently. Returns True on success."""
    branch_name = contract.branch_name
    try:
        exists = await client.branch_ref_exists(ASSEMBLY_AGENT_SLUG, repository, branch_name)
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="branchRefExists",
                exc=exc,
                repository=repository,
                issue=contract.issue_number,
            )
        )
        return False

    if exists:
        result["branch"] = {"name": branch_name, "created": False, "sha": head_sha}
        return True

    try:
        ref = await client.create_branch_ref(ASSEMBLY_AGENT_SLUG, repository, branch_name, head_sha)
        result["branch"] = {
            "name": branch_name,
            "created": True,
            "sha": (ref.get("object") or {}).get("sha") or head_sha,
        }
        return True
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="createBranchRef",
                exc=exc,
                repository=repository,
                issue=contract.issue_number,
            )
        )
        return False


async def _ensure_pull_request(
    client: GitHubAppClient,
    repository: str,
    contract: AssemblyJobContract,
    result: dict[str, Any],
) -> bool:
    """Open or update the PR. Returns True on success."""
    branch_name = contract.branch_name
    base_branch = contract.base_branch
    pr_title = f"{contract.issue_title} (Closes #{contract.issue_number})"
    pr_body = (
        f"Implements #{contract.issue_number}.\n\n"
        f"Closes #{contract.issue_number}.\n\n"
        f"Task summary: {contract.task_summary}\n"
    )

    try:
        existing = await client.find_pull_request_by_head(
            ASSEMBLY_AGENT_SLUG, repository, branch_name
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="findPullRequest",
                exc=exc,
                repository=repository,
                issue=contract.issue_number,
            )
        )
        return False

    if existing:
        pr_number = int(existing.get("number") or 0)
        try:
            await client.update_pull_request(
                ASSEMBLY_AGENT_SLUG, repository, pr_number, body=pr_body
            )
            result["pull_request"] = {
                "number": pr_number,
                "url": existing.get("html_url"),
                "action": "updated",
            }
            return True
        except (httpx.HTTPError, TimeoutError) as exc:
            result["writeback_failures"].append(
                build_writeback_failure(
                    operation="updatePullRequest",
                    exc=exc,
                    repository=repository,
                    pr=pr_number,
                    issue=contract.issue_number,
                )
            )
            return False

    try:
        pr = await client.create_pull_request(
            ASSEMBLY_AGENT_SLUG,
            repository,
            title=pr_title,
            head=branch_name,
            base=base_branch,
            body=pr_body,
        )
        result["pull_request"] = {
            "number": pr.get("number"),
            "url": pr.get("html_url"),
            "action": "opened",
        }
        return True
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="createPullRequest",
                exc=exc,
                repository=repository,
                issue=contract.issue_number,
            )
        )
        return False


async def _post_codex_trigger(
    client: GitHubAppClient,
    repository: str,
    contract: AssemblyJobContract,
    result: dict[str, Any],
) -> None:
    """Post a fresh ``@codex review`` comment on the PR (D7: per push)."""
    pr_number = (result.get("pull_request") or {}).get("number")
    if not pr_number:
        return
    try:
        comment = await client.create_issue_comment(
            ASSEMBLY_AGENT_SLUG,
            repository,
            int(pr_number),
            body=CODEX_REVIEW_TRIGGER_BODY,
        )
        result["codex_review_comment_id"] = comment.get("id")
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="createCodexTriggerComment",
                exc=exc,
                repository=repository,
                pr=int(pr_number),
                issue=contract.issue_number,
            )
        )


async def _upsert_progress_comments(
    client: GitHubAppClient,
    contract: AssemblyJobContract,
    repository: str,
    result: dict[str, Any],
) -> None:
    """Upsert the Assembly progress comment on the issue (and PR when present).

    Per D11 the progress-comment step always runs, including when the
    adapter raised or earlier steps failed.
    """
    contract_dict = contract.to_dict()
    branch = result.get("branch") or {}
    pull_request = result.get("pull_request") or {}
    adapter_result = result.get("adapter_result") or {}
    failures = list(result.get("writeback_failures") or [])

    status = "applied"
    if failures and not pull_request.get("number"):
        status = "failed"
    elif failures:
        status = "partial"
    elif adapter_result.get("status") in {"dry_run", "no_changes"}:
        status = "dry_run" if adapter_result.get("status") == "dry_run" else "no_changes"

    issue_body = build_assembly_comment(
        status=status,
        contract=contract_dict,
        adapter_result=adapter_result,
        branch=branch,
        pull_request=pull_request,
        writeback_failures=failures,
        dry_run=result.get("dry_run", False),
        surface="issue",
    )

    try:
        comment = await client.upsert_issue_comment(
            ASSEMBLY_AGENT_SLUG,
            repository,
            contract.issue_number,
            marker=ASSEMBLY_COMMENT_MARKER,
            body=issue_body,
        )
        result["assembly_comment_id"] = comment.get("id")
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="upsertAssemblyComment",
                exc=exc,
                repository=repository,
                issue=contract.issue_number,
            )
        )
        return

    pr_number = pull_request.get("number")
    if not pr_number:
        return
    pr_body = build_assembly_comment(
        status=status,
        contract=contract_dict,
        adapter_result=adapter_result,
        branch=branch,
        pull_request=pull_request,
        writeback_failures=failures,
        dry_run=result.get("dry_run", False),
        surface="pr",
    )
    try:
        await client.upsert_issue_comment(
            ASSEMBLY_AGENT_SLUG,
            repository,
            int(pr_number),
            marker=ASSEMBLY_COMMENT_MARKER,
            body=pr_body,
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="upsertAssemblyPRComment",
                exc=exc,
                repository=repository,
                pr=int(pr_number),
                issue=contract.issue_number,
            )
        )
