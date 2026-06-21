"""Tests for Clearance review-thread conclusion comment rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from voyager.bots.clearance.close_reason import (
    build_delegated_close_reason_comment,
    build_manual_close_required_comment,
    build_thread_conclusion_comment,
    manual_close_marker,
)
from voyager.bots.clearance.models import (
    Evidence,
    GitHubThreadState,
    Severity,
    Thread,
    ThreadSnapshot,
    Verdict,
)


def _thread(
    verdict: Verdict,
    *,
    verdict_reason: str | None = None,
    llm_reason: str | None = None,
    llm_model: str | None = None,
    llm_confidence: float | None = None,
) -> Thread:
    return Thread(
        id="PRRT_compact",
        comment_id=3254250516,
        path="src/foo.py",
        line=42,
        codex_severity=Severity.P2,
        effective_severity=Severity.P2,
        verdict=verdict,
        verdict_reason=verdict_reason,
        author_reply_id=3254250516,
        code_changed=True,
        llm_reason=llm_reason,
        llm_model=llm_model,
        llm_confidence=llm_confidence,
    )


def _snapshot(*, evidence: Evidence) -> ThreadSnapshot:
    now = datetime(2026, 5, 17, tzinfo=UTC)
    return ThreadSnapshot(
        thread_id="PRRT_compact",
        repo="iterwheel/voyager",
        pr=40,
        first_seen=now,
        last_polled=now,
        codex_comment_id=3254250000,
        path="src/foo.py",
        current_line=42,
        original_line=42,
        codex_severity=Severity.P2,
        effective_severity=Severity.P2,
        verdict=evidence.llm_verdict or Verdict.RESOLVED,
        evidence=evidence,
        github_state=GitHubThreadState(isResolved=False),
    )


def test_deterministic_resolved_comment_uses_compact_card() -> None:
    thread = _thread(
        Verdict.RESOLVED,
        verdict_reason="author reply cites concrete identifier and addresses the review concern",
    )
    snapshot = _snapshot(
        evidence=Evidence(
            thread_state="C",
            author_reply_id=3254250516,
            author_reply_substantive=True,
        )
    )

    body = build_thread_conclusion_comment(thread, snapshot, head_sha="1716c0062a37abcdef")

    assert body.startswith("<!-- clearance-close-reason:PRRT_compact:1716c0062a37 -->")
    assert "✅ **Clearance: resolved**" in body
    assert "🧭 Check: Clearance deterministic verifier" in body
    assert "📍 Location: `src/foo.py:42`" in body
    assert "🔖 Head: `1716c0062a37`" in body
    assert "💡 Why: author reply cites concrete identifier" in body
    assert "✅ Action: conversation resolved" in body
    assert "<details>" in body
    assert "- Verdict: `RESOLVED`" in body
    assert "- Rule: SWM-1101 step 4-5" in body
    assert "- Thread state: `C`" in body
    assert "- Author reply: review comment `3254250516`" in body


def test_manual_close_required_comment_distinguishes_verified_from_closed() -> None:
    thread = _thread(
        Verdict.RESOLVED,
        verdict_reason="author reply cites concrete identifier and addresses the review concern",
    )
    snapshot = _snapshot(
        evidence=Evidence(
            thread_state="C",
            author_reply_id=3254250516,
            author_reply_substantive=True,
        )
    )

    body = build_manual_close_required_comment(thread, snapshot, head_sha="1716c0062a37abcdef")

    assert body.startswith("<!-- clearance-close-reason:PRRT_compact:1716c0062a37 -->")
    assert "<!-- clearance-manual-close:PRRT_compact:1716c0062a37 -->" in body
    assert "✅ **Clearance: resolved**" in body
    assert "⚠️ Action: verified resolved" in body
    assert "does not allow Clearance" in body
    assert "resolve it manually" in body
    assert "✅ Action: conversation resolved" not in body


def test_manual_close_marker_encodes_thread_id_and_head_sha_prefix() -> None:
    thread = _thread(Verdict.RESOLVED)

    assert manual_close_marker(thread, head_sha="1716c0062a37abcdef") == (
        "clearance-manual-close:PRRT_compact:1716c0062a37"
    )


def test_delegated_close_reason_comment_names_resolver_identity() -> None:
    thread = _thread(
        Verdict.RESOLVED,
        verdict_reason="author reply cites concrete identifier and addresses the review concern",
    )
    snapshot = _snapshot(
        evidence=Evidence(
            thread_state="C",
            author_reply_id=3254250516,
            author_reply_substantive=True,
        )
    )

    body = build_delegated_close_reason_comment(
        thread,
        snapshot,
        head_sha="1716c0062a37abcdef",
        resolver_login="iterwheel-assembly[bot]",
    )

    assert body.startswith("<!-- clearance-close-reason:PRRT_compact:1716c0062a37 -->")
    assert "✅ **Clearance: resolved**" in body
    assert "Clearance verified the fix" in body
    assert "`iterwheel-assembly[bot]`" in body
    assert "does not allow Clearance" not in body


def test_investigator_resolved_comment_uses_compact_card() -> None:
    thread = _thread(
        Verdict.RESOLVED,
        llm_reason="diff removes the stale branch and updates the failing test",
        llm_confidence=0.91,
    )
    snapshot = _snapshot(
        evidence=Evidence(
            llm_verdict="RESOLVED",
            llm_confidence=0.91,
            llm_reason="diff removes the stale branch and updates the failing test",
            llm_evidence=[
                "Reviewer concern: stale branch remained",
                "Diff evidence: branch removed",
            ],
        )
    )

    body = build_thread_conclusion_comment(
        thread, snapshot, head_sha="abc1234def567890", model="deepseek-v4-flash"
    )

    assert "✅ **Clearance: resolved**" in body
    assert "🤖 Check: Clearance Investigator (`deepseek-v4-flash`)" in body
    assert "🎯 Confidence: `0.91`" in body
    assert "✅ Action: conversation resolved" in body
    assert "- Model: `deepseek-v4-flash`" in body
    assert "- Reviewer concern: stale branch remained" in body
    assert "- Diff evidence: branch removed" in body


def test_investigator_open_comment_uses_compact_card() -> None:
    thread = _thread(
        Verdict.OPEN,
        llm_reason="the diff changes nearby code but does not add the requested guard",
        llm_confidence=0.84,
    )
    snapshot = _snapshot(
        evidence=Evidence(
            llm_verdict="OPEN",
            llm_confidence=0.84,
            llm_reason="the diff changes nearby code but does not add the requested guard",
            llm_evidence=["Missing fix: requested guard is absent"],
        )
    )

    body = build_thread_conclusion_comment(
        thread, snapshot, head_sha="abc1234def567890", model="deepseek-v4-flash"
    )

    assert body.startswith("<!-- clearance-thread-conclusion:PRRT_compact:abc1234def56 -->")
    assert "👀 **Clearance: still open**" in body
    assert "🤖 Check: Clearance Investigator (`deepseek-v4-flash`)" in body
    assert "🎯 Confidence: `0.84`" in body
    assert "⏳ Action: left open" in body
    assert "- Verdict: `OPEN`" in body
    assert "- Missing fix: requested guard is absent" in body


def test_investigator_comment_uses_persisted_llm_model_without_explicit_model() -> None:
    thread = _thread(
        Verdict.OPEN,
        llm_reason="the diff changes nearby code but does not add the requested guard",
        llm_model="deepseek-v4-flash",
        llm_confidence=0.84,
    )
    snapshot = _snapshot(
        evidence=Evidence(
            llm_verdict="OPEN",
            llm_model="deepseek-v4-flash",
            llm_confidence=0.84,
            llm_reason="the diff changes nearby code but does not add the requested guard",
            llm_evidence=["Missing fix: requested guard is absent"],
        )
    )

    body = build_thread_conclusion_comment(thread, snapshot, head_sha="abc1234def567890")

    assert "🤖 Check: Clearance Investigator (`deepseek-v4-flash`)" in body
    assert "- Model: `deepseek-v4-flash`" in body


def test_needs_human_judgment_comment_uses_compact_card() -> None:
    thread = _thread(
        Verdict.NEEDS_HUMAN_JUDGMENT,
        llm_reason="evidence is ambiguous and the requested change is subjective",
        llm_confidence=0.63,
    )
    snapshot = _snapshot(
        evidence=Evidence(
            llm_verdict="NEEDS_HUMAN_JUDGMENT",
            llm_confidence=0.63,
            llm_reason="evidence is ambiguous and the requested change is subjective",
            llm_evidence=["Ambiguous evidence: reviewer asked for judgment call"],
        )
    )

    body = build_thread_conclusion_comment(
        thread, snapshot, head_sha="abc1234def567890", model="deepseek-v4-flash"
    )

    assert "⚠️ **Clearance: needs human judgment**" in body
    assert "🤖 Check: Clearance Investigator (`deepseek-v4-flash`)" in body
    assert "🎯 Confidence: `0.63`" in body
    assert "🧑 Action: left open for reviewer" in body
    assert "- Verdict: `NEEDS_HUMAN_JUDGMENT`" in body
    assert "- Ambiguous evidence: reviewer asked for judgment call" in body
