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
    assert "Next: request review from an eligible non-author reviewer." in comment
    assert "Approval: waiting for @pr-author" not in comment
    assert "Next: @pr-author review + approve." not in comment


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
