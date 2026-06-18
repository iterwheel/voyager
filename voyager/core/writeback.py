from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

from .redaction import sanitize_public_text

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from voyager.bots.clearance.investigator import ThreadInvestigator

    from .github_app import GitHubAppClient

CLEARANCE_AGENT_SLUG = "iterwheel-clearance"  # voyager clearance bot App


def _sanitize_public_text(value: Any, *, limit: int = 180) -> str:
    """Redact public diagnostics, including github_pat_ and dotted ghs_ token shapes."""
    return sanitize_public_text(value, limit=limit)


def _graphql_error_public_fields(errors: list[dict[str, Any]]) -> dict[str, Any]:
    types: list[str] = []
    messages: list[str] = []
    for error in errors[:3]:
        error_type = _sanitize_public_text(error.get("type") or "unknown", limit=80)
        message = _sanitize_public_text(error.get("message") or "", limit=180)
        if error_type and error_type not in types:
            types.append(error_type)
        if message and message not in messages:
            messages.append(message)

    first_type = types[0] if types else "unknown"
    first_message = messages[0] if messages else "no message"
    summary = f"{first_type}: {first_message}"
    return {
        "graphql_error_types": types,
        "graphql_error_messages": messages,
        "graphql_error_summary": _sanitize_public_text(summary, limit=220),
    }


def _safe_exception_fields(exc: BaseException) -> dict[str, Any]:
    """Return class-name and optional HTTP status from an exception.

    Never includes ``str(exc)``, request URLs, headers, or token-bearing
    messages — used for fallback reason/log fields where the full
    ``build_writeback_failure()`` schema is not needed.
    """
    import httpx as _httpx

    fields: dict[str, Any] = {"error_class": type(exc).__name__}
    if isinstance(exc, _httpx.HTTPStatusError) and exc.response is not None:
        fields["status"] = exc.response.status_code
    else:
        fields["status"] = None
    return fields


def build_writeback_failure(
    *,
    operation: str,
    exc: BaseException,
    repository: str,
    pr: int | None = None,
    issue: int | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Convert a writeback exception into the structured failure metadata schema.

    Never includes raw exception strings, tokens, or request URLs in the
    returned dict.  Safe for GitHub-visible comments and debug records.
    """
    import httpx as _httpx

    from .github_app import GitHubGraphQLError

    status: int | None = None
    if isinstance(exc, _httpx.HTTPStatusError) and exc.response is not None:
        status = exc.response.status_code

    error_class = "GraphQLError" if isinstance(exc, GitHubGraphQLError) else type(exc).__name__

    # Determine suggested_action based on failure family
    if isinstance(exc, GitHubGraphQLError):
        suggested_action = (
            "Verify the GitHub App permissions, repository installation, and installation "
            "access for this operation. For resolveReviewThread, check "
            "reviewThreads.viewerCanResolve before retrying."
        )
    elif isinstance(exc, _httpx.HTTPStatusError) and exc.response is not None:
        code = exc.response.status_code
        if code == 429:
            suggested_action = (
                "Check GitHub API rate-limit status and retry after the limit resets."
            )
        elif code in (401, 403, 404):
            suggested_action = "Verify the GitHub App permissions, repository installation, and installation access for this operation."
        else:
            suggested_action = "Review the structured writeback failure fields and retry after correcting the operation target."
    elif isinstance(
        exc, (_httpx.TimeoutException, TimeoutError, _httpx.ConnectError, _httpx.ReadError)
    ):
        suggested_action = (
            "Check GitHub API reachability and retry the operation after service health recovers."
        )
    else:
        suggested_action = "Review the structured writeback failure fields and retry after correcting the operation target."

    failure = {
        "operation": operation,
        "error_class": error_class,
        "status": status,
        "repo": repository,
        "pr": pr,
        "issue": issue,
        "thread_id": thread_id,
        "suggested_action": suggested_action,
    }
    if isinstance(exc, GitHubGraphQLError):
        failure.update(_graphql_error_public_fields(exc.errors))
    return failure


def format_writeback_failure_warning(failure: dict[str, Any]) -> str:
    """Format a writeback failure dict into a compact operator-facing warning line.

    Never includes raw exception strings or secrets.  Defensive on malformed
    input (missing ``pr`` and ``issue`` renders as ``unknown target``).
    """
    operation = failure.get("operation", "unknown")
    error_class = failure.get("error_class", "unknown")
    status = failure.get("status")
    repo = failure.get("repo", "unknown")
    pr = failure.get("pr")
    issue = failure.get("issue")
    thread_id = failure.get("thread_id")
    suggested_action = failure.get("suggested_action", "")
    graphql_summary = failure.get("graphql_error_summary")
    details = f" GitHub GraphQL: {graphql_summary}." if graphql_summary else ""

    status_part = f", HTTP {status}" if status is not None else ""

    if pr is not None:
        target = f"{repo}#{pr}"
        thread_part = f" thread {thread_id}" if thread_id else ""
        return (
            f"⚠️ Automation writeback: {operation} failed ({error_class}{status_part}) "
            f"on {target}{thread_part}.{details} {suggested_action}"
        )
    elif issue is not None:
        return (
            f"⚠️ Automation writeback: {operation} failed ({error_class}{status_part}) "
            f"on {repo}#{issue}.{details} {suggested_action}"
        )
    else:
        return (
            f"⚠️ Automation writeback: {operation} failed ({error_class}{status_part}) "
            f"on {repo} unknown target.{details} {suggested_action}"
        )


def dry_run_enabled(cfg: Any | None = None) -> bool:
    """Canonical dry-run predicate, shared by the server and writeback paths.

    Default is **true** (safe). ``DRY_RUN`` remains the first-precedence
    runtime override. When it is unset, ``cfg.bridge.dry_run`` supplies the TOML
    fallback; if config cannot be loaded, the helper stays safe and returns
    true. Explicit env "0" / "false" / "no" disables dry-run.

    Codex round 6 P2 (PR #7): the server's /healthz response and the writeback
    helper must agree on the same predicate, otherwise the bridge can claim
    writes are enabled while the helper silently no-ops, or vice versa.
    """
    raw = os.environ.get("DRY_RUN")
    if raw is not None:
        return raw.strip().lower() not in {"0", "false", "no"}
    if cfg is not None:
        return bool(getattr(getattr(cfg, "bridge", None), "dry_run", True))
    try:
        from voyager.core.config import load_config

        return bool(load_config().bridge.dry_run)
    except Exception:
        return True


async def apply_route_writeback(
    client: GitHubAppClient,
    route: dict[str, Any],
    *,
    repository: str | None,
) -> dict[str, Any]:
    if not repository:
        return {"applied": False, "reason": "missing repository"}

    app_slug = route["agent"]
    validation = route["validation"]
    issue_number = validation.get("issue_number")
    if not issue_number:
        return {"applied": False, "reason": "missing issue number"}

    writeback = route.get("writeback") or {}
    labels = writeback.get("labels") or {}
    reactions = writeback.get("reactions") or {}
    add_labels: list[str] = list(labels.get("add") or [])
    remove_labels: list[str] = list(labels.get("remove") or [])
    add_reactions: list[str] = list(reactions.get("add") or [])
    remove_reactions: list[str] = list(reactions.get("remove") or [])
    planned: dict[str, Any] = {
        "comment": bool(writeback.get("comment_body")),
        "add_labels": add_labels,
        "remove_labels": remove_labels,
        "add_reactions": add_reactions,
        "remove_reactions": remove_reactions,
    }

    if dry_run_enabled():
        return {"applied": False, "dry_run": True, "planned": planned}

    # CHG-1813: Capture label/reaction/comment writeback failures as structured
    # metadata. Partial failures are captured and the comment body is patched
    # with the compact warning before upsert/create.
    wb_failures: list[dict[str, Any]] = []

    for label in remove_labels:
        try:
            await client.remove_label(app_slug, repository, int(issue_number), label)
        except (httpx.HTTPError, TimeoutError) as exc:
            wb_failures.append(
                build_writeback_failure(
                    operation="removeLabel",
                    exc=exc,
                    repository=repository,
                    issue=int(issue_number),
                )
            )

    if add_labels:
        try:
            await client.add_labels(app_slug, repository, int(issue_number), add_labels)
        except (httpx.HTTPError, TimeoutError) as exc:
            wb_failures.append(
                build_writeback_failure(
                    operation="addLabels",
                    exc=exc,
                    repository=repository,
                    issue=int(issue_number),
                )
            )

    for reaction in remove_reactions:
        try:
            await client.remove_issue_reaction(app_slug, repository, int(issue_number), reaction)
        except (httpx.HTTPError, TimeoutError) as exc:
            wb_failures.append(
                build_writeback_failure(
                    operation="removeReaction",
                    exc=exc,
                    repository=repository,
                    issue=int(issue_number),
                )
            )

    for reaction in add_reactions:
        try:
            await client.add_issue_reaction(app_slug, repository, int(issue_number), reaction)
        except (httpx.HTTPError, TimeoutError) as exc:
            wb_failures.append(
                build_writeback_failure(
                    operation="addReaction",
                    exc=exc,
                    repository=repository,
                    issue=int(issue_number),
                )
            )

    comment = None
    comment_body = writeback.get("comment_body") or ""
    if comment_body:
        # CHG-1813: If label/reaction failures occurred and a comment body
        # exists, insert the compact warning after the marker line.
        if wb_failures:
            warning_line = format_writeback_failure_warning(wb_failures[0])
            comment_body = _insert_warning_after_marker(comment_body, warning_line)

        if writeback.get("comment_mode") == "append":
            try:
                comment = await client.create_issue_comment(
                    app_slug,
                    repository,
                    int(issue_number),
                    body=comment_body,
                )
            except (httpx.HTTPError, TimeoutError) as exc:
                wb_failures.append(
                    build_writeback_failure(
                        operation="createComment",
                        exc=exc,
                        repository=repository,
                        issue=int(issue_number),
                    )
                )
        else:
            try:
                comment = await client.upsert_issue_comment(
                    app_slug,
                    repository,
                    int(issue_number),
                    marker=writeback["comment_marker"],
                    body=comment_body,
                )
            except (httpx.HTTPError, TimeoutError) as exc:
                wb_failures.append(
                    build_writeback_failure(
                        operation="upsertComment",
                        exc=exc,
                        repository=repository,
                        issue=int(issue_number),
                    )
                )

    result: dict[str, Any] = {
        "applied": True,
        "dry_run": False,
        "planned": planned,
        "comment_url": (comment or {}).get("html_url"),
    }

    # CHG-1813: Only add writeback failure keys when at least one failure occurred.
    if wb_failures:
        result["writeback_failures"] = wb_failures
        result["writeback_failure_count"] = len(wb_failures)
        first = wb_failures[0]
        op = first.get("operation", "unknown")
        ec = first.get("error_class", "unknown")
        st = first.get("status")
        st_part = f", HTTP {st}" if st is not None else ""
        count = len(wb_failures)
        if count == 1:
            result["writeback_failure_reason"] = (
                f"1 writeback operation failed; first: {op} ({ec}{st_part})"
            )
        else:
            result["writeback_failure_reason"] = (
                f"{count} writeback operations failed; first: {op} ({ec}{st_part})"
            )

    return result


def _insert_warning_after_marker(body: str, warning_line: str) -> str:
    """Insert a warning line after the first HTML marker comment.

    If the body starts with an HTML marker comment (``<!-- ... -->``), the
    warning is inserted on a new line immediately after it. Otherwise the
    warning is prepended above the existing body.  Safe when ``body`` is empty.
    """
    if not body:
        return body
    import re

    marker_match = re.match(r"^(<!--[^>]*-->)\n?", body)
    if marker_match:
        marker = marker_match.group(1)
        rest = body[marker_match.end() :]
        return f"{marker}\n{warning_line}\n{rest}"
    return f"{warning_line}\n{body}"


async def dispatch_route_writeback(
    client: GitHubAppClient,
    route: dict[str, Any],
    *,
    repository: str | None,
    store: Any = None,
    default_profile_name: str | None = None,
    investigator: ThreadInvestigator | None = None,
) -> dict[str, Any]:
    """Dispatch a route to the right writeback path.

    Routes from `route_clearance_event` carry only ``{"dynamic": "clearance_readiness"}``
    in their writeback shape — the real labels / comment / reactions come from
    `enrich_clearance_route`, which fetches the live PR snapshot (pull request,
    reviews, review threads) and computes the concrete writeback. Routes from
    Blueprint and Stack already carry concrete writeback shapes, so they go
    straight to ``apply_route_writeback``.

    When ``store`` is provided, the SWM-1101 per-thread pipeline runs via
    ``compute_clearance_automation`` before enrichment and its result is passed
    as ``automation=`` to ``enrich_clearance_route``. When ``store`` is None,
    legacy PR-body-only enrichment runs unchanged.

    The ``investigator`` kwarg is forwarded to ``compute_clearance_automation``
    for the Wave 7B-3 LLM investigator path.

    Codex round 1 P1 (PR #7).
    """
    writeback = route.get("writeback") or {}
    dynamic = writeback.get("dynamic")

    if dynamic == "assembly_implementation":
        # Lazy import — see clearance lazy import below.  Keeps the Assembly
        # package free of writeback-internal coupling and lets tests mock
        # ``dispatch_assembly_writeback`` cleanly.
        from voyager.bots.assembly.writeback import dispatch_assembly_writeback

        return await dispatch_assembly_writeback(
            client,
            route,
            repository=repository,
        )

    if dynamic == "clearance_readiness":
        if not repository:
            return {
                "applied": False,
                "reason": "missing repository (required for Clearance enrichment)",
            }
        # Lazy import: the clearance bot is a separate package, importing it at
        # module top would create a tight coupling and complicate test mocking.
        from voyager.bots.clearance import enrich_clearance_route

        validation = route.get("validation") or {}
        pr_number = validation.get("pr_number")

        automation: dict[str, Any] | None = None
        if store is not None:
            from voyager.bots.clearance.known_limitations import KnownLimitationStore
            from voyager.bots.clearance.pipeline import compute_clearance_automation

            try:
                webhook_head_sha: str | None = (route.get("validation") or {}).get(
                    "webhook_head_sha"
                ) or None
                automation = await compute_clearance_automation(
                    client,
                    route,
                    repository=repository,
                    store=store,
                    default_profile_name=default_profile_name,
                    investigator=investigator,
                    known_limitation_store=KnownLimitationStore(),
                    expected_sha=webhook_head_sha,
                )
            except Exception as exc:
                safe = _safe_exception_fields(exc)
                _log.warning(
                    "clearance pipeline failed for %s; falling back to error automation "
                    "(class=%s status=%s)",
                    repository,
                    safe["error_class"],
                    safe["status"],
                )
                automation = {
                    "enabled": True,
                    "status": "error",
                    "reason": f"pipeline failed: {safe['error_class']}",
                    "sync_actions": [],
                    "sync_actions_count": 0,
                }

        if (automation or {}).get("status") == "stale_verdict_skip":
            _log.info(
                "writeback_skipped_stale_verdict: %s",
                json.dumps(
                    {
                        "event": "writeback_skipped_stale_verdict",
                        "repo": repository,
                        "pr": pr_number,
                        "automation_status": "stale_verdict_skip",
                    }
                ),
            )
            return {
                "ok": True,
                "skipped": "stale_verdict",
                "automation": automation,
            }

        # Wave 7C-2 stale-verdict guard (VOY-1809 commit 6).
        # If the verdict was computed against a head_sha that differs from the
        # PR's CURRENT head, the verdict is stale (a concurrent webhook
        # advanced the head). Skip writeback to prevent applying an outdated
        # verdict. Per VOY-1809 D4 + D5.
        expected_sha = (automation or {}).get("head_sha")  # .get() per F8 legacy tolerance
        if expected_sha and not dry_run_enabled() and pr_number is not None:
            try:
                pull = await client.pull_request(CLEARANCE_AGENT_SLUG, repository, int(pr_number))
                actual_sha = (pull.get("head") or {}).get("sha") or ""
                if actual_sha and actual_sha != expected_sha:
                    _log.info(
                        "stale_verdict_skip: %s",
                        json.dumps(
                            {
                                "event": "stale_verdict_skip",
                                "repo": repository,
                                "pr": pr_number,
                                "expected_sha": expected_sha,
                                "actual_sha": actual_sha,
                            }
                        ),
                    )
                    stale_automation = dict(automation or {})
                    # Note: automation.status="stale_verdict_skip" is un-enumerated vs
                    # the Status enum. Downstream consumers must tolerate unknown
                    # values (per VOY-1809 F6).
                    stale_automation["status"] = "stale_verdict_skip"
                    return {
                        "ok": True,
                        "skipped": "stale_verdict",
                        "automation": stale_automation,
                    }
            except (httpx.HTTPError, TimeoutError) as exc:
                # Codex MVE-round P2: fail-open observability — log + counter event
                # so persistent API instability surfaces.
                safe = _safe_exception_fields(exc)
                _log.warning(
                    "stale_guard: REST fetch failed for %s PR=%s; emitting fail-open log + counter, "
                    "proceeding with writeback",
                    repository,
                    pr_number,
                )
                _log.info(
                    "stale_guard_failed_fail_open: %s",
                    json.dumps(
                        {
                            "event": "stale_guard_failed_fail_open",
                            "repo": repository,
                            "pr": pr_number,
                            "expected_sha": expected_sha,
                            "error_class": safe["error_class"],
                            "status": safe["status"],
                        }
                    ),
                )

        try:
            enriched = await enrich_clearance_route(
                client, route, repository=repository, automation=automation
            )
        except Exception as exc:
            safe = _safe_exception_fields(exc)
            _log.warning(
                "clearance enrichment failed for %s; returning applied=False (class=%s status=%s)",
                repository,
                safe["error_class"],
                safe["status"],
            )
            return {
                "applied": False,
                "reason": f"clearance enrichment failed: {safe['error_class']}",
                "automation": automation,
            }
        # Codex GH-bot PR #15 P1: include the `automation` dict in the normal
        # apply-path return (not just the stale-skip + error paths). Without
        # this, the e2e harness's `_flatten_writeback` reads `status`,
        # `automation_reason`, and thread counts as None even though voyager
        # computed them — every A/B/C/F scenario fails comparison.
        apply_result = await apply_route_writeback(client, enriched, repository=repository)
        if automation is not None:
            apply_result["automation"] = automation
        return apply_result

    return await apply_route_writeback(client, route, repository=repository)
