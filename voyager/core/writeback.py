from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from voyager.bots.clearance.investigator import ThreadInvestigator

    from .github_app import GitHubAppClient

CLEARANCE_AGENT_SLUG = "iterwheel-clearance"  # voyager clearance bot App


def dry_run_enabled() -> bool:
    """Canonical dry-run predicate, shared by the server and writeback paths.

    Default is **true** (safe). When DRY_RUN is unset/empty/"1"/"true"/"yes",
    no GitHub writes happen — the background task only returns planned actions.
    Explicit "0" / "false" / "no" disables dry-run.

    Codex round 6 P2 (PR #7): the server's /healthz response and the writeback
    helper must agree on the same predicate, otherwise the bridge can claim
    writes are enabled while the helper silently no-ops, or vice versa.
    """
    raw = os.environ.get("DRY_RUN", "true").strip().lower()
    return raw not in {"0", "false", "no"}


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

    for label in remove_labels:
        await client.remove_label(app_slug, repository, int(issue_number), label)
    if add_labels:
        await client.add_labels(app_slug, repository, int(issue_number), add_labels)

    for reaction in remove_reactions:
        await client.remove_issue_reaction(app_slug, repository, int(issue_number), reaction)
    for reaction in add_reactions:
        await client.add_issue_reaction(app_slug, repository, int(issue_number), reaction)

    comment = None
    if writeback.get("comment_body"):
        if writeback.get("comment_mode") == "append":
            comment = await client.create_issue_comment(
                app_slug,
                repository,
                int(issue_number),
                body=writeback["comment_body"],
            )
        else:
            comment = await client.upsert_issue_comment(
                app_slug,
                repository,
                int(issue_number),
                marker=writeback["comment_marker"],
                body=writeback["comment_body"],
            )

    return {
        "applied": True,
        "dry_run": False,
        "planned": planned,
        "comment_url": (comment or {}).get("html_url"),
    }


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
                    expected_sha=webhook_head_sha,
                )
            except Exception as exc:
                _log.exception(
                    "clearance pipeline failed for %s; falling back to error automation",
                    repository,
                )
                automation = {
                    "enabled": True,
                    "status": "error",
                    "reason": f"pipeline failed: {exc.__class__.__name__}: {exc}",
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
                _log.warning(
                    "stale_guard: REST fetch failed for %s PR=%s; emitting fail-open log + counter, "
                    "proceeding with writeback: %s",
                    repository,
                    pr_number,
                    exc,
                )
                _log.info(
                    "stale_guard_failed_fail_open: %s",
                    json.dumps(
                        {
                            "event": "stale_guard_failed_fail_open",
                            "repo": repository,
                            "pr": pr_number,
                            "expected_sha": expected_sha,
                            "error": str(exc),
                        }
                    ),
                )

        try:
            enriched = await enrich_clearance_route(
                client, route, repository=repository, automation=automation
            )
        except Exception as exc:
            _log.exception(
                "clearance enrichment failed for %s; returning applied=False",
                repository,
            )
            return {
                "applied": False,
                "reason": f"clearance enrichment failed: {exc.__class__.__name__}: {exc}",
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
