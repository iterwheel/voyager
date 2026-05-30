from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from voyager.bots.clearance.models import (
    Evidence,
    GitHubThreadState,
    Severity,
    Thread,
    ThreadSnapshot,
    Verdict,
)
from voyager.bots.clearance.pipeline import (
    _has_current_head_verdict_comment,
    _maybe_post_thread_verdict_comments,
    _maybe_sync_stage_15,
)


class _WritebackClient:
    def __init__(self) -> None:
        self.reply_calls: list[tuple[str, str, int, int, str]] = []
        self.resolve_calls: list[tuple[str, str, str]] = []

    async def create_review_thread_reply(
        self,
        app_slug: str,
        repository: str,
        pull_number: int,
        comment_id: int,
        *,
        body: str,
    ) -> dict[str, Any]:
        self.reply_calls.append((app_slug, repository, pull_number, comment_id, body))
        return {"html_url": "https://example/reply"}

    async def pull_request_review_threads(
        self, app_slug: str, repository: str, pull_number: int
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "PRRT_alpha",
                "viewerCanResolve": app_slug == "iterwheel-assembly",
            }
        ]

    async def check_head_repo_accessible(self, app_slug: str, head_repo: str) -> bool:
        return True

    async def resolve_review_thread(
        self, app_slug: str, repository: str, thread_id: str
    ) -> dict[str, Any]:
        self.resolve_calls.append((app_slug, repository, thread_id))
        return {
            "id": thread_id,
            "isResolved": True,
            "resolvedBy": {"login": f"{app_slug}[bot]"},
        }


def _thread(
    verdict: Verdict,
    *,
    existing_marker: bool = False,
    existing_close_reason_marker: bool = False,
) -> Thread:
    return Thread(
        id="PRRT_alpha",
        comment_id=100001,
        path="app.py",
        line=10,
        codex_severity=Severity.P1,
        effective_severity=Severity.P1,
        verdict=verdict,
        verdict_reason="unit-test verdict",
        github_isResolved=False,
        existing_thread_conclusion_marker=existing_marker,
        existing_close_reason_marker=existing_close_reason_marker,
    )


def _snapshot(
    *,
    viewer_can_resolve: bool = True,
    verdict: Verdict = Verdict.OPEN,
    evidence: Evidence | None = None,
) -> ThreadSnapshot:
    now = datetime.now(UTC).replace(microsecond=0)
    return ThreadSnapshot(
        thread_id="PRRT_alpha",
        repo="iterwheel/sandbox",
        pr=49,
        first_seen=now,
        last_polled=now,
        codex_comment_id=100001,
        path="app.py",
        current_line=10,
        codex_severity=Severity.P1,
        effective_severity=Severity.P1,
        verdict=verdict,
        evidence=evidence or Evidence(),
        github_state=GitHubThreadState(
            isResolved=False,
            isOutdated=False,
            viewerCanResolve=viewer_can_resolve,
        ),
    )


def test_current_head_verdict_comment_dedupe_is_verdict_specific() -> None:
    comments = [
        {
            "author": {"login": "iterwheel-clearance"},
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n- Verdict: `OPEN`"
            ),
        }
    ]

    assert _has_current_head_verdict_comment(
        comments,
        thread_id="PRRT_alpha",
        head_sha="head-sha-abc1234",
        verdict=Verdict.OPEN,
    )
    assert not _has_current_head_verdict_comment(
        comments,
        thread_id="PRRT_alpha",
        head_sha="head-sha-abc1234",
        verdict=Verdict.NEEDS_HUMAN_JUDGMENT,
    )
    assert not _has_current_head_verdict_comment(
        comments,
        thread_id="PRRT_alpha",
        head_sha="new-head-sha5678",
        verdict=Verdict.OPEN,
    )


@pytest.mark.asyncio
async def test_thread_verdict_comment_skips_existing_current_head_verdict() -> None:
    client = _WritebackClient()

    actions = await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.OPEN, existing_marker=True)],
        snapshots=[_snapshot()],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    assert client.reply_calls == []
    assert actions[0]["skipped"] is True
    assert actions[0]["skip_reason"] == "existing verdict reply for current head and verdict"


@pytest.mark.asyncio
async def test_thread_verdict_comment_uses_persisted_investigator_model() -> None:
    client = _WritebackClient()
    thread = _thread(Verdict.OPEN)
    thread.llm_verdict = "OPEN"
    thread.llm_model = "deepseek-v4-flash"
    thread.llm_reason = "the diff does not add the requested guard"
    thread.llm_confidence = 0.84
    snapshot = _snapshot(
        verdict=Verdict.OPEN,
        evidence=Evidence(
            llm_verdict="OPEN",
            llm_model="deepseek-v4-flash",
            llm_reason="the diff does not add the requested guard",
            llm_confidence=0.84,
            llm_evidence=["Missing fix: requested guard is absent"],
        ),
    )

    await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[thread],
        snapshots=[snapshot],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    body = client.reply_calls[0][4]
    assert "Clearance Investigator (`deepseek-v4-flash`)" in body
    assert "`pro`" not in body


@pytest.mark.asyncio
async def test_assembly_author_resolver_fallback_closes_resolved_thread() -> None:
    client = _WritebackClient()
    thread = _thread(Verdict.RESOLVED)
    snapshot = _snapshot(viewer_can_resolve=False)

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[thread],
        snapshots=[snapshot],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert client.resolve_calls == [("iterwheel-assembly", "iterwheel/sandbox", "PRRT_alpha")]
    assert actions[0].result["fallback"] is True
    assert actions[0].result["resolver_app"] == "iterwheel-assembly"
    assert thread.github_isResolved is True


def test_freshness_allows_verdict_transition_with_newer_evidence() -> None:
    """PR author reply after Clearance OPEN verdict → marker is stale."""
    comments = [
        {
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-30T10:00:00Z",
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n- Verdict: `OPEN`"
            ),
        },
        {
            "author": {"login": "ryosaeba1985"},
            "createdAt": "2026-05-30T10:05:00Z",
            "body": "I've addressed this feedback in the latest commit.",
        },
    ]

    # Same verdict (OPEN) with newer non-Clearance evidence → stale, allow new
    assert not _has_current_head_verdict_comment(
        comments,
        thread_id="PRRT_alpha",
        head_sha="head-sha-abc1234",
        verdict=Verdict.OPEN,
    )


def test_freshness_blocks_duplicate_without_newer_evidence() -> None:
    """Clearance OPEN verdict with no newer thread comments → marker is active."""
    comments = [
        {
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-30T10:00:00Z",
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n- Verdict: `OPEN`"
            ),
        },
    ]

    assert _has_current_head_verdict_comment(
        comments,
        thread_id="PRRT_alpha",
        head_sha="head-sha-abc1234",
        verdict=Verdict.OPEN,
    )

    # Codex re-review (Clearance) at same time → still active (Clearance isn't "newer evidence")
    comments_with_same_clearance = [
        {
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-30T10:00:00Z",
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n- Verdict: `OPEN`"
            ),
        },
        {
            "author": {"login": "chatgpt-codex-connector"},
            "createdAt": "2026-05-30T10:00:00Z",
            "body": "Codex Review: clean",
        },
    ]

    assert _has_current_head_verdict_comment(
        comments_with_same_clearance,
        thread_id="PRRT_alpha",
        head_sha="head-sha-abc1234",
        verdict=Verdict.OPEN,
    )


@pytest.mark.asyncio
async def test_resolver_fallback_success_suppresses_manual_close_required() -> None:
    """Resolver fallback success → existing close-reason marker suppresses
    manual-close-required output for the same thread/head."""
    client = _WritebackClient()
    # Thread has verdict=RESOLVED, viewerCanResolve=False, no resolver.
    # But existing_close_reason_marker=True (delegated close-reason already posted
    # by a previous run's resolver fallback).
    thread = _thread(
        Verdict.RESOLVED,
        existing_close_reason_marker=True,
    )
    snapshot = _snapshot(viewer_can_resolve=False)

    # No resolver available (no pr_author_login or no matching resolver)
    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[thread],
        snapshots=[snapshot],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
    )

    # No in-thread reply should be posted because existing_close_reason_marker
    # suppresses it in the manual-close-required path.
    assert client.reply_calls == []
    # The action records the skip with the existing-marker reason.
    assert len(actions) == 1
    in_thread_reply = actions[0].result.get("in_thread_reply", {})
    assert in_thread_reply.get("posted") is False
    assert "existing close-reason reply" in (in_thread_reply.get("skipped") or "")
