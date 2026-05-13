from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from voyager.bots.clearance.investigator import ThreadInvestigator

    from .github_app import GitHubAppClient


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

        automation: dict[str, Any] | None = None
        if store is not None:
            from voyager.bots.clearance.pipeline import compute_clearance_automation

            try:
                automation = await compute_clearance_automation(
                    client,
                    route,
                    repository=repository,
                    store=store,
                    default_profile_name=default_profile_name,
                    investigator=investigator,
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
            }
        return await apply_route_writeback(client, enriched, repository=repository)

    return await apply_route_writeback(client, route, repository=repository)
