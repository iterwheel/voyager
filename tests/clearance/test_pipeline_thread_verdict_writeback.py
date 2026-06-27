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
    _has_current_head_final_verdict_comment,
    _has_current_head_verdict_comment,
    _latest_current_head_final_marker_state,
    _latest_manual_close_relevant_state,
    _maybe_post_thread_verdict_comments,
    _maybe_sync_stage_15,
)


class _WritebackClient:
    def __init__(self) -> None:
        self.reply_calls: list[tuple[str, str, int, int, str]] = []
        self.resolve_calls: list[tuple[str, str, str]] = []
        self.thread_comments: list[dict[str, Any]] = []
        self.fail_pull_request_review_threads = False
        self.resolver_viewer_can_resolve_by_app: dict[str, bool] = {
            "iterwheel-assembly": True,
        }
        self.resolver_viewer_can_reply_by_app: dict[str, bool] = {}

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
        database_id = 100100 + len(self.thread_comments)
        self.thread_comments.append(
            {
                "databaseId": database_id,
                "author": {"login": "iterwheel-clearance"},
                "body": body,
                "createdAt": f"2026-05-11T12:{45 + len(self.thread_comments):02d}:00Z",
            }
        )
        return {"html_url": "https://example/reply"}

    async def pull_request_review_threads(
        self, app_slug: str, repository: str, pull_number: int
    ) -> list[dict[str, Any]]:
        if self.fail_pull_request_review_threads:
            raise RuntimeError("simulated review thread fetch failure")
        return [
            {
                "id": "PRRT_alpha",
                "isResolved": False,
                "isOutdated": False,
                "viewerCanResolve": self.resolver_viewer_can_resolve_by_app.get(
                    app_slug,
                    app_slug == "iterwheel-assembly",
                ),
                "viewerCanReply": self.resolver_viewer_can_reply_by_app.get(app_slug, True),
                "comments": {"nodes": list(self.thread_comments)},
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
    thread_id: str = "PRRT_alpha",
    comment_id: int = 100001,
    path: str = "app.py",
    existing_marker: bool = False,
    existing_close_reason_marker: bool = False,
    existing_manual_close_marker: bool = False,
) -> Thread:
    return Thread(
        id=thread_id,
        comment_id=comment_id,
        path=path,
        line=10,
        codex_severity=Severity.P1,
        effective_severity=Severity.P1,
        verdict=verdict,
        verdict_reason="unit-test verdict",
        github_isResolved=False,
        existing_thread_conclusion_marker=existing_marker,
        existing_head_verdict_marker=existing_marker or existing_close_reason_marker,
        existing_close_reason_marker=existing_close_reason_marker,
        existing_manual_close_marker=existing_manual_close_marker,
    )


def _snapshot(
    *,
    thread_id: str = "PRRT_alpha",
    path: str = "app.py",
    viewer_can_resolve: bool = True,
    verdict: Verdict = Verdict.OPEN,
    evidence: Evidence | None = None,
) -> ThreadSnapshot:
    now = datetime.now(UTC).replace(microsecond=0)
    return ThreadSnapshot(
        thread_id=thread_id,
        repo="iterwheel/sandbox",
        pr=49,
        first_seen=now,
        last_polled=now,
        codex_comment_id=100001,
        path=path,
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
    assert _has_current_head_final_verdict_comment(
        comments,
        thread_id="PRRT_alpha",
        head_sha="head-sha-abc1234",
    )
    assert not _has_current_head_verdict_comment(
        comments,
        thread_id="PRRT_alpha",
        head_sha="new-head-sha5678",
        verdict=Verdict.OPEN,
    )


def test_latest_manual_close_state_uses_created_at_not_array_order() -> None:
    comments = [
        {
            "databaseId": 2,
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-11T12:10:00Z",
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:new-head-sha -->\n- Verdict: `OPEN`"
            ),
        },
        {
            "databaseId": 1,
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-11T12:00:00Z",
            "body": (
                "<!-- clearance-close-reason:PRRT_alpha:old-head-sha -->\n"
                "<!-- clearance-manual-close:PRRT_alpha:old-head-sha -->\n"
                "- Verdict: `RESOLVED`"
            ),
        },
    ]

    assert _latest_manual_close_relevant_state(comments, thread_id="PRRT_alpha") == "open"


def test_latest_manual_close_state_ignores_normal_close_reason() -> None:
    comments = [
        {
            "databaseId": 1,
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-11T12:00:00Z",
            "body": (
                "<!-- clearance-close-reason:PRRT_alpha:old-head-sha -->\n- Verdict: `RESOLVED`"
            ),
        }
    ]

    assert _latest_manual_close_relevant_state(comments, thread_id="PRRT_alpha") is None


def test_latest_current_head_final_marker_state_uses_created_at() -> None:
    comments = [
        {
            "databaseId": 2,
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-11T12:10:00Z",
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n- Verdict: `OPEN`"
            ),
        },
        {
            "databaseId": 1,
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-11T12:00:00Z",
            "body": (
                "<!-- clearance-close-reason:PRRT_alpha:head-sha-abc -->\n"
                "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->\n"
                "- Verdict: `RESOLVED`"
            ),
        },
    ]

    assert (
        _latest_current_head_final_marker_state(
            comments,
            thread_id="PRRT_alpha",
            head_sha="head-sha-abc1234",
        )
        == "thread-conclusion"
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
    assert actions[0]["skip_reason"] == "existing final verdict reply for current head"


@pytest.mark.asyncio
async def test_open_verdict_can_supersede_same_head_manual_close_marker() -> None:
    client = _WritebackClient()
    client.thread_comments.append(
        {
            "databaseId": 1,
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-11T12:00:00Z",
            "body": (
                "<!-- clearance-close-reason:PRRT_alpha:head-sha-abc -->\n"
                "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->\n"
                "- Verdict: `RESOLVED`"
            ),
        }
    )

    actions = await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[
            _thread(
                Verdict.OPEN,
                existing_close_reason_marker=True,
                existing_manual_close_marker=True,
            )
        ],
        snapshots=[_snapshot()],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    assert len(client.reply_calls) == 1
    assert actions[0]["posted"] is True
    assert "Clearance: still open" in client.reply_calls[0][4]


@pytest.mark.asyncio
async def test_open_verdict_skips_snapshot_manual_close_marker_when_refresh_fails() -> None:
    client = _WritebackClient()
    client.fail_pull_request_review_threads = True

    actions = await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[
            _thread(
                Verdict.OPEN,
                existing_close_reason_marker=True,
                existing_manual_close_marker=True,
            )
        ],
        snapshots=[_snapshot()],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    assert client.reply_calls == []
    assert actions[0]["skipped"] is True
    assert actions[0]["skip_reason"] == "existing final verdict reply for current head"


@pytest.mark.asyncio
async def test_open_verdict_still_skips_same_head_normal_close_reason_marker() -> None:
    client = _WritebackClient()

    actions = await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.OPEN, existing_close_reason_marker=True)],
        snapshots=[_snapshot()],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    assert client.reply_calls == []
    assert actions[0]["skipped"] is True
    assert actions[0]["skip_reason"] == "existing final verdict reply for current head"


@pytest.mark.asyncio
async def test_open_verdict_skips_fresh_same_head_conclusion_after_manual_close() -> None:
    client = _WritebackClient()
    client.thread_comments.extend(
        [
            {
                "databaseId": 1,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:00:00Z",
                "body": (
                    "<!-- clearance-close-reason:PRRT_alpha:head-sha-abc -->\n"
                    "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `RESOLVED`"
                ),
            },
            {
                "databaseId": 2,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:10:00Z",
                "body": (
                    "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `OPEN`"
                ),
            },
        ]
    )

    actions = await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.OPEN)],
        snapshots=[_snapshot()],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    assert client.reply_calls == []
    assert actions[0]["skipped"] is True
    assert actions[0]["skip_reason"] == (
        "existing final verdict reply for current head after refresh"
    )


@pytest.mark.asyncio
async def test_different_open_verdict_skips_fresh_same_head_conclusion_after_manual_close() -> None:
    client = _WritebackClient()
    client.thread_comments.extend(
        [
            {
                "databaseId": 1,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:00:00Z",
                "body": (
                    "<!-- clearance-close-reason:PRRT_alpha:head-sha-abc -->\n"
                    "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `RESOLVED`"
                ),
            },
            {
                "databaseId": 2,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:10:00Z",
                "body": (
                    "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `OPEN`"
                ),
            },
        ]
    )

    actions = await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.NEEDS_HUMAN_JUDGMENT)],
        snapshots=[_snapshot(verdict=Verdict.NEEDS_HUMAN_JUDGMENT)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    assert client.reply_calls == []
    assert actions[0]["skipped"] is True
    assert actions[0]["skip_reason"] == (
        "existing final verdict reply for current head after refresh"
    )


@pytest.mark.asyncio
async def test_thread_verdict_comment_skips_conflicting_head_after_refresh() -> None:
    client = _WritebackClient()
    client.thread_comments.append(
        {
            "author": {"login": "iterwheel-clearance"},
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n- Verdict: `OPEN`"
            ),
        }
    )

    actions = await _maybe_post_thread_verdict_comments(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.NEEDS_HUMAN_JUDGMENT)],
        snapshots=[_snapshot(verdict=Verdict.NEEDS_HUMAN_JUDGMENT)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
    )

    assert client.reply_calls == []
    assert actions[0]["skipped"] is True
    assert actions[0]["skip_reason"] == (
        "existing final verdict reply for current head after refresh"
    )


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


@pytest.mark.asyncio
async def test_resolved_verdict_can_supersede_existing_open_same_head_reply() -> None:
    client = _WritebackClient()
    client.thread_comments.append(
        {
            "author": {"login": "iterwheel-clearance"},
            "body": (
                "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n- Verdict: `OPEN`"
            ),
        }
    )

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=True, verdict=Verdict.RESOLVED)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
    )

    assert client.resolve_calls == [("iterwheel-clearance", "iterwheel/sandbox", "PRRT_alpha")]
    assert len(client.reply_calls) == 1
    assert "Clearance: resolved" in client.reply_calls[0][4]
    assert actions[0].result["in_thread_reply"]["posted"] is True


@pytest.mark.asyncio
async def test_assembly_fallback_success_suppresses_later_manual_close_reply() -> None:
    client = _WritebackClient()

    await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=False)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False
    second_actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=False)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert len(client.reply_calls) == 1
    assert "GitHub conversation closed by `iterwheel-assembly[bot]`" in client.reply_calls[0][4]
    assert "does not allow Clearance" not in client.reply_calls[0][4]
    assert second_actions[0].result["in_thread_reply"]["skipped"] == (
        "existing resolved verdict reply for current head after refresh"
    )


@pytest.mark.asyncio
async def test_manual_close_reply_is_deduped_across_heads_while_resolved() -> None:
    client = _WritebackClient()
    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False

    await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=False)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )
    second_actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=False)],
        pr=49,
        head_sha="new-head-sha5678",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert len(client.reply_calls) == 1
    assert "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->" in client.reply_calls[0][4]
    assert second_actions[0].result["in_thread_reply"]["skipped"] == (
        "existing manual-close resolved reply after refresh"
    )


@pytest.mark.asyncio
async def test_manual_close_reply_posts_again_after_later_open_state() -> None:
    client = _WritebackClient()
    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False
    client.thread_comments.extend(
        [
            {
                "databaseId": 2,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:10:00Z",
                "body": (
                    "<!-- clearance-thread-conclusion:PRRT_alpha:head-bbb22222 -->\n"
                    "- Verdict: `OPEN`"
                ),
            },
            {
                "databaseId": 1,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:00:00Z",
                "body": (
                    "<!-- clearance-close-reason:PRRT_alpha:head-aaa11111 -->\n"
                    "<!-- clearance-manual-close:PRRT_alpha:head-aaa11111 -->\n"
                    "- Verdict: `RESOLVED`"
                ),
            },
        ]
    )

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=False)],
        pr=49,
        head_sha="head-ccc33333",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert len(client.reply_calls) == 1
    assert actions[0].result["in_thread_reply"]["posted"] is True


@pytest.mark.asyncio
async def test_manual_close_reply_posts_again_after_same_head_open_state() -> None:
    client = _WritebackClient()
    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False
    client.thread_comments.extend(
        [
            {
                "databaseId": 1,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:00:00Z",
                "body": (
                    "<!-- clearance-close-reason:PRRT_alpha:head-sha-abc -->\n"
                    "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `RESOLVED`"
                ),
            },
            {
                "databaseId": 2,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:10:00Z",
                "body": (
                    "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `OPEN`"
                ),
            },
        ]
    )

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[
            _thread(
                Verdict.RESOLVED,
                existing_close_reason_marker=True,
                existing_manual_close_marker=True,
            )
        ],
        snapshots=[_snapshot(viewer_can_resolve=False, verdict=Verdict.RESOLVED)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert len(client.reply_calls) == 1
    assert "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->" in client.reply_calls[0][4]
    assert actions[0].result["in_thread_reply"]["posted"] is True


@pytest.mark.asyncio
async def test_manual_close_reply_skips_after_fresh_same_head_resolved_reply() -> None:
    client = _WritebackClient()
    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False
    client.thread_comments.extend(
        [
            {
                "databaseId": 1,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:00:00Z",
                "body": (
                    "<!-- clearance-close-reason:PRRT_alpha:head-sha-abc -->\n"
                    "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `RESOLVED`"
                ),
            },
            {
                "databaseId": 2,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:10:00Z",
                "body": (
                    "<!-- clearance-thread-conclusion:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `OPEN`"
                ),
            },
            {
                "databaseId": 3,
                "author": {"login": "iterwheel-clearance"},
                "createdAt": "2026-05-11T12:20:00Z",
                "body": (
                    "<!-- clearance-close-reason:PRRT_alpha:head-sha-abc -->\n"
                    "<!-- clearance-manual-close:PRRT_alpha:head-sha-abc -->\n"
                    "- Verdict: `RESOLVED`"
                ),
            },
        ]
    )

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[
            _thread(
                Verdict.RESOLVED,
                existing_close_reason_marker=True,
                existing_manual_close_marker=True,
            )
        ],
        snapshots=[_snapshot(viewer_can_resolve=False, verdict=Verdict.RESOLVED)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert client.reply_calls == []
    assert actions[0].result["in_thread_reply"]["skipped"] == (
        "existing resolved verdict reply for current head after refresh"
    )


@pytest.mark.asyncio
async def test_manual_close_reply_skips_snapshot_same_head_marker_when_refresh_fails() -> None:
    client = _WritebackClient()
    client.fail_pull_request_review_threads = True
    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[
            _thread(
                Verdict.RESOLVED,
                existing_close_reason_marker=True,
                existing_manual_close_marker=True,
            )
        ],
        snapshots=[_snapshot(viewer_can_resolve=False, verdict=Verdict.RESOLVED)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert client.reply_calls == []
    assert actions[0].result["in_thread_reply"]["skipped"] == (
        "existing resolved verdict reply for current head"
    )


@pytest.mark.asyncio
async def test_normal_close_reason_does_not_suppress_later_manual_close_reply() -> None:
    client = _WritebackClient()
    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False
    client.thread_comments.append(
        {
            "databaseId": 1,
            "author": {"login": "iterwheel-clearance"},
            "createdAt": "2026-05-11T12:00:00Z",
            "body": (
                "<!-- clearance-close-reason:PRRT_alpha:head-aaa11111 -->\n- Verdict: `RESOLVED`"
            ),
        }
    )

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=False)],
        pr=49,
        head_sha="head-bbb22222",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert len(client.reply_calls) == 1
    assert actions[0].result["in_thread_reply"]["posted"] is True


@pytest.mark.asyncio
async def test_manual_close_dedupe_fetch_failure_fails_open() -> None:
    client = _WritebackClient()
    client.resolver_viewer_can_resolve_by_app["iterwheel-assembly"] = False
    client.fail_pull_request_review_threads = True

    actions = await _maybe_sync_stage_15(
        client=client,  # type: ignore[arg-type]
        repository="iterwheel/sandbox",
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=False)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert len(client.reply_calls) == 1
    assert actions[0].result["in_thread_reply"]["posted"] is True
