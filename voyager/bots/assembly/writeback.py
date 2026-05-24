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

import asyncio
import inspect
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx

from voyager.core.writeback import build_writeback_failure, dry_run_enabled

from .adapters import AdapterExecutionContext, AdapterResult, select_execution_adapter
from .branch import make_branch_name
from .comment import build_assembly_comment
from .constants import (
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_COMMENT_MARKER,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    ASSEMBLY_PI_COMMAND_PATH_ENV,
    ASSEMBLY_PI_DEFAULT_COMMAND_PATH,
    ASSEMBLY_PI_DEFAULT_TIMEOUT_SECONDS,
    ASSEMBLY_PI_DEFAULT_WORKDIR,
    ASSEMBLY_PI_TIMEOUT_SECONDS_ENV,
    ASSEMBLY_PI_WORKDIR_ENV,
    CODEX_REVIEW_TRIGGER_BODY,
)
from .job_contract import AssemblyJobContract, build_job_contract
from .preconditions import validate_preconditions

if TYPE_CHECKING:
    from voyager.core.github_app import GitHubAppClient

_assembly_writeback_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _get_lock(repository: str, branch_name: str) -> asyncio.Lock:
    """Return (creating if needed) the per-(repo, branch) writeback lock.

    Lock dict grows monotonically (no TTL). At Voyager's ~50 issues/year
    cadence x 64 bytes/lock the worst-case footprint is ~3 KB until bridge
    restart, which is well within acceptable. WeakValueDictionary migration
    trigger is documented in CHG-1819 D6 / Out of Scope.
    """
    key = (repository, branch_name)
    lock = _assembly_writeback_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _assembly_writeback_locks[key] = lock
    return lock


_log = logging.getLogger(__name__)


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _path_env(name: str, default: str) -> Path:
    raw = os.environ.get(name)
    value = raw.strip() if raw else default
    return Path(value).expanduser()


def _cached_issue_from_route(route: dict[str, Any]) -> dict[str, Any]:
    """Return the cached webhook snapshot of the issue from the route shape.

    Used as the fallback when the live GitHub refetch fails. The
    ``_source: "cached"`` marker lets the dispatcher distinguish this from
    a successful live refetch with an authoritative-but-empty label list.
    """
    writeback = route.get("writeback") or {}
    contract = writeback.get("contract") or {}
    return {
        "number": contract.get("issue_number"),
        "title": contract.get("issue_title"),
        "body": contract.get("issue_body"),
        "html_url": contract.get("issue_url"),
        "labels": writeback.get("issue_labels") or [],
        "state": writeback.get("issue_state") or "open",
        "_source": "cached",
    }


def _is_dry_run(command_flags: dict[str, Any]) -> bool:
    """Combined dry-run gate: env ``DRY_RUN`` OR per-command ``--dry-run``.

    Codex round-2 P1 (PR #74): the parsed ``--dry-run`` flag must gate
    GitHub mutations independently of the global env so that an operator
    can request a safe dry run on a per-comment basis even when
    ``DRY_RUN=false`` is in effect for production.
    """
    return dry_run_enabled() or bool(command_flags.get("dry_run"))


async def _build_adapter_context(
    client: GitHubAppClient,
    adapter: Any,
    repository: str,
    *,
    is_dry_run: bool,
) -> AdapterExecutionContext:
    installation_token: str | None = None
    if getattr(adapter, "requires_installation_token", False) is True and not is_dry_run:
        installation_token = await client.installation_token(
            ASSEMBLY_AGENT_SLUG,
            repository=repository,
        )
    return AdapterExecutionContext(
        repository=repository,
        workdir=_path_env(ASSEMBLY_PI_WORKDIR_ENV, ASSEMBLY_PI_DEFAULT_WORKDIR),
        timeout_seconds=_positive_int_env(
            ASSEMBLY_PI_TIMEOUT_SECONDS_ENV,
            ASSEMBLY_PI_DEFAULT_TIMEOUT_SECONDS,
        ),
        command_path=(
            os.environ.get(ASSEMBLY_PI_COMMAND_PATH_ENV) or ASSEMBLY_PI_DEFAULT_COMMAND_PATH
        ),
        installation_token=installation_token,
    )


def _adapter_context_mode(execute: Any) -> str:
    """Return how to pass context while preserving older one-arg test doubles."""
    try:
        signature = inspect.signature(execute)
    except (TypeError, ValueError):
        return "positional"

    parameters = list(signature.parameters.values())
    if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in parameters):
        return "positional"
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters):
        return "keyword"
    for param in parameters:
        if param.name == "context" and param.kind == inspect.Parameter.KEYWORD_ONLY:
            return "keyword"

    positional = [
        param
        for param in parameters
        if param.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    return "positional" if len(positional) >= 2 else "none"


async def _execute_adapter(
    adapter: Any,
    contract: AssemblyJobContract,
    context: AdapterExecutionContext,
) -> AdapterResult:
    execute = adapter.execute
    mode = _adapter_context_mode(execute)
    if mode == "positional":
        return cast(AdapterResult, await execute(contract, context))
    if mode == "keyword":
        return cast(AdapterResult, await execute(contract, context=context))
    return cast(AdapterResult, await execute(contract))


def _redact_secret(value: Any, secret: str | None) -> Any:
    if not isinstance(secret, str) or not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[redacted]")
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secret(item, secret) for item in value)
    if isinstance(value, dict):
        return {
            _redact_secret(key, secret): _redact_secret(item, secret) for key, item in value.items()
        }
    return value


async def _live_issue_from_route(
    client: GitHubAppClient,
    repository: str,
    route: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Refetch the live issue from GitHub before D4 re-validation.

    Codex round-1 P1 (PR #74): D4's "live issue is authoritative" promise
    requires an actual GitHub round-trip — the cached webhook payload may
    be stale (label removed, issue closed) by the time the background
    writeback task fires.

    On HTTP/timeout failure, falls back to the cached webhook snapshot and
    records the failure in ``result["writeback_failures"]`` so the operator
    sees the degraded path.
    """
    cached = _cached_issue_from_route(route)
    issue_number = cached.get("number")
    if not issue_number:
        return cached
    try:
        live = await client.get_issue(ASSEMBLY_AGENT_SLUG, repository, int(issue_number))
    except (httpx.HTTPError, TimeoutError) as exc:
        result["writeback_failures"].append(
            build_writeback_failure(
                operation="getIssue",
                exc=exc,
                repository=repository,
                issue=int(issue_number),
            )
        )
        return cached
    if not isinstance(live, dict):
        # Defensive: real GitHub always returns a JSON object, but mock
        # clients that auto-create get_issue without setting a return value
        # would yield a non-dict. Fall back to cached rather than crashing
        # the dispatcher.
        return cached
    # GitHub returns labels as objects with {name, color, ...}; normalise to
    # plain names for the precondition gate, which is what Blueprint/Stack
    # snapshots also carry.
    live_labels = [
        item.get("name")
        for item in (live.get("labels") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    # Codex round-2 P1: a successful live refetch is authoritative for
    # labels even when the list is empty — the operator may have removed
    # all gating labels between routing and dispatch.
    #
    # Codex round-4 P2: the same authority applies to ``title``, ``body``,
    # ``html_url``, and ``state``. ``or cached.get(...)`` would silently
    # replace an intentionally-cleared live field with stale webhook
    # content. ``dict.get(key, default)`` returns the default only when
    # the key is missing — an empty/None live value still wins.
    return {
        "number": live.get("number", issue_number),
        "title": live.get("title", cached.get("title")),
        "body": live.get("body", cached.get("body")),
        "html_url": live.get("html_url", cached.get("html_url")),
        "labels": live_labels,
        "state": live.get("state", cached.get("state")) or "open",
        "pull_request": live.get("pull_request"),
        "_source": "live",
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

    # Backend selection is env-only (`ASSEMBLY_EXECUTION_BACKEND`) per VOY-1817 D3.
    # `command_flags` carries `dry_run` / `allow_missing_stack` only; there is
    # no `--backend` command flag (closed by CHG-1819 F2; see VOY-1819).
    adapter = select_execution_adapter()
    backend_name = adapter.name

    is_dry_run = _is_dry_run(command_flags)

    base_result: dict[str, Any] = {
        "applied": False,
        "dry_run": is_dry_run,
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
    # D4 — re-validate preconditions against the LIVE issue snapshot
    # (Codex round-1 P1 fix: refetch from GitHub, not just the cached
    # webhook payload).  On refetch failure the cached snapshot is used
    # and the failure is recorded in ``base_result["writeback_failures"]``.
    # ------------------------------------------------------------------
    issue_snapshot = await _live_issue_from_route(client, repository, route, base_result)
    # Codex round-2 P1: a successful live refetch's label list is
    # authoritative even when empty. Only when the snapshot is the cached
    # fallback (`_source == "cached"`) do we layer in webhook-derived
    # labels as a defensive backstop.
    if issue_snapshot.get("_source") == "live":
        snapshot_labels = list(issue_snapshot.get("labels") or [])
    else:
        snapshot_labels = issue_snapshot.get("labels") or _labels_from_validation(validation)
    pre = validate_preconditions(
        {**issue_snapshot, "labels": snapshot_labels},
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
    # CHG-1819 F3 — per-(repository, branch_name) asyncio lock.
    #
    # Serialises concurrent `/assembly` deliveries that target the same
    # branch so two background tasks cannot both compute the same commits
    # and race on `create_branch_ref` (which would 422 the second caller).
    # Scope per CHG-1819 D5: branch is the shared GitHub resource; the
    # delivery_id is unique per webhook and would never block. Lock dict
    # growth is documented on `_get_lock`.
    # ------------------------------------------------------------------
    async with _get_lock(repository, contract.branch_name):
        # --------------------------------------------------------------
        # Adapter execution.  Failures are captured but do NOT abort the
        # progress-comment step (D11 "always runs").
        # --------------------------------------------------------------
        adapter_result: AdapterResult | None = None
        adapter_failure: dict[str, Any] | None = None
        adapter_context: AdapterExecutionContext | None = None
        try:
            adapter_context = await _build_adapter_context(
                client,
                adapter,
                repository,
                is_dry_run=is_dry_run,
            )
            adapter_result = await _execute_adapter(adapter, contract, adapter_context)
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
            secret = adapter_context.installation_token if adapter_context else None
            base_result["adapter_result"] = {
                "status": adapter_result.status,
                "commit_shas": _redact_secret(list(adapter_result.commit_shas), secret),
                "summary": _redact_secret(adapter_result.summary, secret),
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

        # --------------------------------------------------------------
        # GitHub mutation gates.  Four independent dimensions (Codex
        # round-2 P1 added per-command `--dry-run`):
        #   - ``DRY_RUN`` env short-circuits all mutations globally.
        #   - The parsed ``--dry-run`` command flag short-circuits
        #     mutations for a single invocation (e.g. ``/assembly --dry-run``).
        #   - adapter_result must produce commits before branch/PR steps run.
        #   - codex-trigger only fires when the PR open / update succeeded.
        # --------------------------------------------------------------
        if is_dry_run:
            base_result["pull_request"] = {
                "number": None,
                "url": None,
                "action": "dry_run_skipped",
            }
            return base_result

        base_result["applied"] = True

        # Per D11, when the adapter produced no commits, skip
        # branch/PR/codex steps but still upsert the progress comment so
        # the operator sees the plan / dry-run / failure surface.
        commit_shas = (
            list(adapter_result.commit_shas)
            if adapter_result is not None and adapter_result.status == "executed"
            else []
        )
        if not commit_shas:
            base_result["pull_request"] = {
                "number": None,
                "url": None,
                "action": "skipped_no_changes",
            }
            await _preserve_existing_pr_context_for_no_changes(
                client, repository, contract, base_result
            )
            await _upsert_progress_comments(client, contract, repository, base_result)
            return base_result

        # --------------------------------------------------------------
        # branch -> PR -> codex-trigger -> progress-comment
        # --------------------------------------------------------------
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

    Skipped when the combined dry-run gate (``DRY_RUN`` env OR per-command
    ``--dry-run`` flag) is on — the refusal comment is itself a GitHub
    mutation, and dry-run must be inert. The router-side refusal goes
    through untouched; the dispatcher-side refusal includes any updated
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
    if result.get("dry_run"):
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


async def _preserve_existing_pr_context_for_no_changes(
    client: GitHubAppClient,
    repository: str,
    contract: AssemblyJobContract,
    result: dict[str, Any],
) -> None:
    """Keep duplicate no_changes runs from erasing an already-open PR context."""
    adapter_result = result.get("adapter_result") or {}
    if adapter_result.get("status") != "no_changes":
        return

    try:
        existing = await client.find_pull_request_by_head(
            ASSEMBLY_AGENT_SLUG, repository, contract.branch_name
        )
    except (httpx.HTTPError, TimeoutError):
        # This lookup is only for monotonic progress rendering. If it is
        # unavailable, keep the original first-run no_changes surface.
        _log.warning(
            "Assembly no_changes PR-context lookup failed",
            extra={"repository": repository, "issue": contract.issue_number},
            exc_info=True,
        )
        return
    if not existing:
        return

    try:
        pr_number = int(existing.get("number") or 0)
    except (TypeError, ValueError):
        return
    if pr_number <= 0:
        return

    result["branch"] = {
        "name": contract.branch_name,
        "created": False,
        "sha": (existing.get("head") or {}).get("sha"),
    }
    result["pull_request"] = {
        "number": pr_number,
        "url": existing.get("html_url"),
        "action": "updated",
    }


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

    adapter_status = adapter_result.get("status")
    status = "applied"
    if adapter_status == "failed" or (failures and not pull_request.get("number")):
        status = "failed"
    elif failures:
        status = "partial"
    elif adapter_status == "dry_run":
        status = "dry_run"
    elif adapter_status == "no_changes" and not pull_request.get("number"):
        status = "no_changes"

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
