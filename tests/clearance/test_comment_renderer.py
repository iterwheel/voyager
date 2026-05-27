from __future__ import annotations

import pytest


def _evaluation(
    *,
    status: str,
    label: str,
    current_approvals: list[str] | None = None,
    stale_approvals: list[str] | None = None,
    blocking_reviewers: list[str] | None = None,
    unresolved_thread_count: int = 0,
    reasons: list[str] | None = None,
) -> dict:
    return {
        "status": status,
        "conclusion": "success" if status == "clearance_ready" else "neutral",
        "issue_number": 77,
        "pr_number": 77,
        "classifier": "clearance-v1",
        "summary": "Clearance renderer test.",
        "review_state": {
            "current_approvals": current_approvals or [],
            "stale_approvals": stale_approvals or [],
            "blocking_reviewers": blocking_reviewers or [],
            "unresolved_thread_count": unresolved_thread_count,
        },
        "confidence": {
            "reasons": reasons or [],
            "semantic_fix_verified": False,
            "semantic_fix_note": "Renderer diagnostics note.",
        },
        "labels": {"add": [label], "remove": []},
        "reactions": {"add": [], "remove": []},
        "pr_url": "https://github.test/pull/77",
        "head_sha": "sha-abc",
        "target_kind": "pull_request",
    }


def _automation(status: str = "ready") -> dict:
    return {
        "enabled": True,
        "status": status,
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [],
        "sync_actions_count": 2,
        "dry_run": False,
        "head_sha": "sha-abc",
    }


def _automation_with_skipped_stage15_actions() -> dict:
    return {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [
            {
                "mutation": "resolveReviewThread",
                "threadId": "PRRT_alpha",
                "result": {
                    "skipped": True,
                    "skip_reason": "viewerCanResolve is false",
                    "repo": "frankyxhl/trinity",
                    "pr": 133,
                    "thread_id": "PRRT_alpha",
                },
            },
            {
                "mutation": "resolveReviewThread",
                "threadId": "PRRT_beta",
                "result": {
                    "skipped": True,
                    "skip_reason": "viewerCanResolve is false",
                    "repo": "frankyxhl/trinity",
                    "pr": 133,
                    "thread_id": "PRRT_beta",
                },
            },
        ],
        "sync_actions_count": 2,
        "dry_run": False,
        "head_sha": "sha-abc",
        "unresolved_codex_thread_count": 0,
        "semantic_blocker_count": 0,
        "visual_unresolved_thread_count": 2,
        "visual_unresolved_skipped_thread_count": 2,
    }


def _automation_with_fallback_stage15_action() -> dict:
    return {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [
            {
                "mutation": "resolveReviewThread",
                "threadId": "PRRT_alpha",
                "result": {
                    "id": "PRRT_alpha",
                    "isResolved": True,
                    "resolver_app": "iterwheel-assembly",
                    "resolver_login": "iterwheel-assembly[bot]",
                    "fallback": True,
                },
            }
        ],
        "sync_actions_count": 1,
        "dry_run": False,
        "head_sha": "sha-abc",
        "unresolved_codex_thread_count": 0,
        "semantic_blocker_count": 0,
        "visual_unresolved_thread_count": 1,
        "visual_unresolved_skipped_thread_count": 0,
    }


# ---------------------------------------------------------------------------
# CHG-1813: writeback failure warning in comment rendering
# ---------------------------------------------------------------------------


def _automation_with_writeback_failure(
    *,
    status: str = "error",
    operation: str = "resolveReviewThread",
    error_class: str = "HTTPStatusError",
    http_status: int | None = 403,
    repo: str = "iterwheel/sandbox",
    pr: int | None = 49,
    issue: int | None = None,
    thread_id: str | None = "PRRT_codex_alpha",
) -> dict:
    failure = {
        "operation": operation,
        "error_class": error_class,
        "status": http_status,
        "repo": repo,
        "pr": pr,
        "issue": issue,
        "thread_id": thread_id,
        "suggested_action": "Verify the GitHub App permissions, repository installation, and installation access for this operation.",
    }
    return {
        "enabled": True,
        "status": status,
        "reason": "1 writeback operation failed; first: resolveReviewThread (HTTPStatusError, HTTP 403)",
        "sync_actions": [],
        "sync_actions_count": 1,
        "dry_run": False,
        "head_sha": "sha-abc",
        "writeback_failures": [failure],
        "writeback_failure_count": 1,
        "writeback_failure_reason": f"1 writeback operation failed; first: {operation} ({error_class}, HTTP {http_status})",
    }


def test_writeback_failure_warning_appears_in_comment() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    automation = _automation_with_writeback_failure()
    comment = build_clearance_comment(
        _evaluation(status="clearance_blocked", label="clearance-2-blocked"),
        automation=automation,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "⚠️ Automation writeback: resolveReviewThread failed" in comment
    assert "HTTPStatusError" in comment
    assert "HTTP 403" in comment
    assert "iterwheel/sandbox#49" in comment
    assert "thread PRRT_codex_alpha" in comment
    assert "Verify the GitHub App permissions" in comment


def test_graphql_writeback_failure_summary_appears_in_comment() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    automation = _automation_with_writeback_failure(
        error_class="GraphQLError",
        http_status=None,
    )
    automation["writeback_failures"][0].update(
        {
            "graphql_error_types": ["FORBIDDEN"],
            "graphql_error_messages": ["Resource not accessible by integration"],
            "graphql_error_summary": "FORBIDDEN: Resource not accessible by integration",
            "suggested_action": "Check reviewThreads.viewerCanResolve before retrying.",
        }
    )
    comment = build_clearance_comment(
        _evaluation(status="clearance_blocked", label="clearance-2-blocked"),
        automation=automation,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "GitHub GraphQL: FORBIDDEN: Resource not accessible by integration." in comment
    assert "reviewThreads.viewerCanResolve" in comment


def test_writeback_failure_warning_contains_no_secrets() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    # Use a failure dict that would be concerning if secrets leaked
    automation = _automation_with_writeback_failure()
    comment = build_clearance_comment(
        _evaluation(status="clearance_blocked", label="clearance-2-blocked"),
        automation=automation,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "ghp_" not in comment
    assert "token=" not in comment
    assert "Authorization" not in comment
    assert "Bearer" not in comment


def test_no_writeback_failure_warning_when_no_failures() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    comment = build_clearance_comment(
        _evaluation(status="clearance_ready", label="clearance-4-ready-for-merge"),
        automation=_automation(),
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "⚠️ Automation writeback:" not in comment
    assert "writeback failure" not in comment.lower()


def test_stage15_skipped_actions_are_visible_without_looking_successful() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    comment = build_clearance_comment(
        _evaluation(status="clearance_ready_for_approval", label="clearance-3-ready-for-approval"),
        automation=_automation_with_skipped_stage15_actions(),
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    summary = "thread sync actions: 2 (applied: 0, skipped: 2, failed: 0)"
    assert f"✅ Automation: ready; {summary}" in comment
    assert f"- Automation: ready; {summary}; dry-run: false" in comment
    assert (
        "- Skipped resolveReviewThread: 2 threads, reason: viewerCanResolve is false. "
        "GitHub conversations may remain visually unresolved/outdated even though "
        "Clearance no longer treats them as blockers."
    ) in comment
    assert "ghp_" not in comment
    assert "token=" not in comment
    assert "Authorization" not in comment


def test_stage15_assembly_fallback_counts_as_applied_not_skipped() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    comment = build_clearance_comment(
        _evaluation(status="clearance_ready_for_approval", label="clearance-3-ready-for-approval"),
        automation=_automation_with_fallback_stage15_action(),
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    summary = "thread sync actions: 1 (applied: 1, skipped: 0, failed: 0)"
    assert f"✅ Automation: ready; {summary}" in comment
    assert "Skipped resolveReviewThread" not in comment


def test_issue_118_readiness_comment_names_visual_unresolved_skipped_threads() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    comment = build_clearance_comment(
        _evaluation(status="clearance_ready_for_approval", label="clearance-3-ready-for-approval"),
        automation=_automation_with_skipped_stage15_actions(),
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "Stage: 3 - Ready for approval" in comment
    assert "✅ Threads: 0 blocking; 2 visual-unresolved skipped threads" in comment
    assert (
        "✅ Automation: ready; thread sync actions: 2 (applied: 0, skipped: 2, failed: 0)"
        in comment
    )
    assert "- Semantic blocking threads: 0" in comment
    assert "- Visual-unresolved skipped threads: 2" in comment
    assert "- Skipped resolveReviewThread: 2 threads, reason: viewerCanResolve is false." in comment
    assert "GitHub conversations may remain visually unresolved/outdated" in comment
    assert "Clearance no longer treats them as blockers" in comment


def test_issue_124_readiness_comment_does_not_call_visual_unresolved_zero_unresolved() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    comment = build_clearance_comment(
        _evaluation(
            status="clearance_ready_for_approval",
            label="clearance-3-ready-for-approval",
            unresolved_thread_count=1,
        ),
        automation={
            **_automation_with_skipped_stage15_actions(),
            "sync_actions": [
                _automation_with_skipped_stage15_actions()["sync_actions"][0],
            ],
            "sync_actions_count": 1,
            "reason": (
                "all Codex review threads RESOLVED; 1 outdated visual-unresolved "
                "thread still visible (viewerCanResolve=false; not blocking)"
            ),
            "visual_unresolved_thread_count": 1,
            "visual_unresolved_skipped_thread_count": 1,
        },
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "✅ Threads: 0 blocking; 1 visual-unresolved skipped thread" in comment
    assert "✅ Threads: 0 unresolved" not in comment
    assert "- Unresolved threads: 1" in comment
    assert "- Visual-unresolved skipped threads: 1" in comment
    assert "outdated visual-unresolved thread still visible" in comment


def test_thread_success_summary_requires_final_ready_status() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    comment = build_clearance_comment(
        _evaluation(
            status="clearance_blocked",
            label="clearance-2-blocked",
            unresolved_thread_count=1,
        ),
        automation={
            **_automation(),
            "status": "ready",
            "unresolved_codex_thread_count": 0,
            "semantic_blocker_count": 0,
            "visual_unresolved_thread_count": 0,
            "visual_unresolved_skipped_thread_count": 0,
        },
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "❌ Threads: 1 unresolved" in comment
    assert "✅ Threads: 0 blocking" not in comment


def test_writeback_failure_warning_for_generic_issue_operation() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    automation = _automation_with_writeback_failure(
        operation="addLabels",
        error_class="HTTPStatusError",
        http_status=404,
        pr=None,
        issue=42,
        thread_id=None,
    )
    comment = build_clearance_comment(
        _evaluation(status="clearance_blocked", label="clearance-2-blocked"),
        automation=automation,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "⚠️ Automation writeback: addLabels failed" in comment
    assert "iterwheel/sandbox#42" in comment
    assert "HTTP 404" in comment
    # Should NOT have "thread" for issue-level operations
    assert " thread " not in comment.split("iterwheel/sandbox#42")[1].split(".")[0]


def test_writeback_failure_warning_malformed_dict_unknown_target() -> None:
    """A10: When both pr and issue are absent, renders 'unknown target'."""
    from voyager.bots.clearance.enrichment import build_clearance_comment

    automation = _automation_with_writeback_failure(
        pr=None,
        issue=None,
        thread_id=None,
    )
    comment = build_clearance_comment(
        _evaluation(status="clearance_blocked", label="clearance-2-blocked"),
        automation=automation,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "unknown target" in comment


def test_writeback_warning_after_automation_status_line_before_deadlock_warning(
    monkeypatch,
) -> None:
    """A9 + D3: Warning appears after automation status line and before deadlock warning."""
    from voyager.bots.clearance.constants import reset_review_request_users_cache
    from voyager.bots.clearance.enrichment import build_clearance_comment

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "pr-author")
    reset_review_request_users_cache()

    review_request = {
        "enabled": True,
        "applied": False,
        "requested": [],
        "already_requested": [],
        "planned": [],
        "skipped_author": ["pr-author"],
        "author_only_deadlock": True,
    }

    automation = _automation_with_writeback_failure()
    comment = build_clearance_comment(
        _evaluation(status="clearance_blocked", label="clearance-2-blocked"),
        automation=automation,
        review_request=review_request,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    lines = comment.split("\n")
    automation_line_idx = next(i for i, line in enumerate(lines) if "Automation:" in line)
    warning_line_idx = next(i for i, line in enumerate(lines) if "⚠️ Automation writeback:" in line)
    deadlock_line_idx = next(
        i for i, line in enumerate(lines) if "only configured reviewer" in line
    )
    assert warning_line_idx > automation_line_idx, (
        f"writeback warning at line {warning_line_idx} should be after automation status at {automation_line_idx}"
    )
    assert deadlock_line_idx > warning_line_idx, (
        f"deadlock warning at line {deadlock_line_idx} should be after writeback warning at {warning_line_idx}"
    )


@pytest.mark.parametrize(
    ("status", "label", "stage", "stage_name"),
    [
        ("clearance_pending", "clearance-1-pending", 1, "Pending"),
        ("clearance_blocked", "clearance-2-blocked", 2, "Blocked"),
        (
            "clearance_ready_for_approval",
            "clearance-3-ready-for-approval",
            3,
            "Ready for approval",
        ),
        ("clearance_ready", "clearance-4-ready-for-merge", 4, "Ready for merge"),
    ],
)
def test_compact_comment_renderer_covers_all_numbered_stages(
    monkeypatch, status: str, label: str, stage: int, stage_name: str
) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache
    from voyager.bots.clearance.enrichment import build_clearance_comment

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    review_request = None
    current_approvals = ["frankyxhl"] if status == "clearance_ready" else []
    blocking_reviewers = ["reviewer"] if status == "clearance_blocked" else []
    unresolved_count = 1 if status == "clearance_blocked" else 0
    if status == "clearance_ready_for_approval":
        review_request = {
            "enabled": True,
            "applied": True,
            "requested": ["frankyxhl"],
            "already_requested": [],
            "skipped_author": [],
        }

    comment = build_clearance_comment(
        _evaluation(
            status=status,
            label=label,
            current_approvals=current_approvals,
            blocking_reviewers=blocking_reviewers,
            unresolved_thread_count=unresolved_count,
        ),
        automation=_automation(),
        review_request=review_request,
        provenance={
            "event": "pull_request",
            "action": "synchronize",
            "delivery_id": "delivery-123",
            "updated_at": "2026-05-17T00:00:00Z",
        },
    )

    assert comment.startswith("<!-- iterwheel:clearance-readiness -->\n## Clearance")
    assert f"Stage: {stage} - {stage_name}" in comment
    assert f"`{label}`" in comment
    assert "<details>" in comment
    assert "</details>" in comment
    assert f"- Selected label: `{label}`" in comment
    assert "- Current approvals:" in comment
    assert "- Stale approvals:" in comment
    assert "- Changes requested:" in comment
    assert "- Unresolved threads:" in comment
    assert "- Automation: ready; thread sync actions: 2; dry-run: false" in comment
    assert (
        "- Last updated: 2026-05-17T00:00:00Z via pull_request.synchronize delivery delivery-123"
    ) in comment


def test_ready_for_approval_panel_surfaces_review_request_and_next_action(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache
    from voyager.bots.clearance.enrichment import build_clearance_comment

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()
    review_request = {
        "enabled": True,
        "applied": True,
        "requested": ["frankyxhl"],
        "already_requested": [],
        "skipped_author": [],
    }

    comment = build_clearance_comment(
        _evaluation(
            status="clearance_ready_for_approval",
            label="clearance-3-ready-for-approval",
            current_approvals=["someone-else"],
        ),
        automation=_automation(),
        review_request=review_request,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "👤 Review: requested @frankyxhl" in comment
    assert "⏳ Approval: waiting for @frankyxhl" in comment
    assert "Next: @frankyxhl review + approve." in comment
    assert "- Review request: requested @frankyxhl" in comment


def test_ready_for_approval_panel_does_not_wait_on_skipped_author(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache
    from voyager.bots.clearance.enrichment import build_clearance_comment

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "pr-author")
    reset_review_request_users_cache()
    review_request = {
        "enabled": True,
        "applied": False,
        "requested": [],
        "already_requested": [],
        "planned": [],
        "skipped_author": ["pr-author"],
        "author_only_deadlock": True,
    }

    comment = build_clearance_comment(
        _evaluation(
            status="clearance_ready_for_approval",
            label="clearance-3-ready-for-approval",
            current_approvals=["someone-else"],
        ),
        automation=_automation(),
        review_request=review_request,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "👤 Review: skipped PR author @pr-author" in comment
    assert "⏳ Approval: waiting for eligible reviewer" in comment
    assert "⚠️ Warning: @pr-author is the only configured reviewer" in comment
    assert "`VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS`" in comment
    assert "Next: add a non-author configured reviewer" in comment
    assert "Approval: waiting for @pr-author" not in comment
    assert "Next: @pr-author review + approve." not in comment
    assert "Stage: 4" not in comment


def test_ready_for_approval_panel_does_not_show_false_deadlock(monkeypatch) -> None:
    from voyager.bots.clearance.constants import reset_review_request_users_cache
    from voyager.bots.clearance.enrichment import build_clearance_comment

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "pr-author,eligible-reviewer")
    reset_review_request_users_cache()
    review_request = {
        "enabled": True,
        "applied": True,
        "requested": ["eligible-reviewer"],
        "already_requested": [],
        "planned": [],
        "skipped_author": ["pr-author"],
        "author_only_deadlock": False,
    }

    comment = build_clearance_comment(
        _evaluation(
            status="clearance_ready_for_approval",
            label="clearance-3-ready-for-approval",
            current_approvals=["someone-else"],
        ),
        automation=_automation(),
        review_request=review_request,
        provenance={"updated_at": "2026-05-17T00:00:00Z"},
    )

    assert "👤 Review: requested @eligible-reviewer; skipped PR author @pr-author" in comment
    assert "⏳ Approval: waiting for @eligible-reviewer" in comment
    assert "Next: @eligible-reviewer review + approve." in comment
    assert "only configured reviewer" not in comment


def test_compact_comment_details_include_debug_diagnostics() -> None:
    from voyager.bots.clearance.enrichment import build_clearance_comment

    comment = build_clearance_comment(
        _evaluation(
            status="clearance_blocked",
            label="clearance-2-blocked",
            current_approvals=["alice"],
            stale_approvals=["bob"],
            blocking_reviewers=["carol"],
            unresolved_thread_count=3,
            reasons=["Changes requested by: @carol."],
        ),
        automation={
            **_automation("blocked"),
            "reason": "3 Codex review threads still OPEN",
            "sync_actions_count": 0,
        },
        provenance={
            "event": "pull_request_review",
            "action": "submitted",
            "updated_at": "2026-05-17T00:00:00Z",
        },
    )

    assert "❌ Review: changes requested by @carol" in comment
    assert "❌ Threads: 3 unresolved" in comment
    assert "❌ Automation: blocked; thread sync actions: 0" in comment
    assert "- Current approvals: @alice" in comment
    assert "- Stale approvals: @bob" in comment
    assert "- Changes requested: @carol" in comment
    assert "- Unresolved threads: 3" in comment
    assert "- Reasons:" in comment
    assert "- Changes requested by: @carol." in comment
