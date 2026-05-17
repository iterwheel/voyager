"""Review-thread conclusion rendering."""

from __future__ import annotations

import re

from voyager.bots.clearance.models import Thread, ThreadSnapshot

_BACKTICK_RUN = re.compile(r"`+")


def _sanitize_markdown(value: str) -> str:
    """Strip markdown control characters that could break the comment layout.

    Currently only collapses runs of backticks to apostrophes — LLM-supplied
    evidence that quotes inline code with backticks would otherwise close the
    surrounding comment's formatting context. Other markdown chars (asterisks,
    underscores, brackets) are inert in our bullet-list context, so leaving
    them unescaped keeps the rendered comment readable.

    Codex round-1 review hygiene (Phase 7B-3 hardening #6).
    """
    return _BACKTICK_RUN.sub("'", value)


def has_llm_close_reason(thread: Thread, snapshot: ThreadSnapshot | None) -> bool:
    evidence = snapshot.evidence if snapshot else None
    return bool(thread.llm_reason or (evidence and evidence.llm_reason))


def _clip(value: str, limit: int = 600) -> str:
    value = _sanitize_markdown(value)  # strip backticks before whitespace collapse
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _verdict_value(thread: Thread) -> str:
    return getattr(thread.verdict, "value", thread.verdict)


def _status_heading(verdict: str) -> str:
    if verdict == "RESOLVED":
        return "✅ **Clearance: resolved**"
    if verdict == "NEEDS_HUMAN_JUDGMENT":
        return "⚠️ **Clearance: needs human judgment**"
    return "👀 **Clearance: still open**"


def _action_line(verdict: str) -> str:
    if verdict == "RESOLVED":
        return "✅ Action: conversation resolved"
    if verdict == "NEEDS_HUMAN_JUDGMENT":
        return "🧑 Action: left open for reviewer"
    return "⏳ Action: left open"


def _checker_line(*, llm_reason: str | None, model: str | None) -> str:
    if llm_reason:
        verifier = f"Clearance Investigator (`{model}`)" if model else "Clearance Investigator"
        return f"🤖 Check: {verifier}"
    return "🧭 Check: Clearance deterministic verifier"


def _location(thread: Thread) -> str:
    return _sanitize_markdown(f"{thread.path}:{thread.line}" if thread.line else thread.path)


def conclusion_marker(thread: Thread, *, head_sha: str) -> str:
    return f"clearance-thread-conclusion:{thread.id}:{head_sha[:12]}"


def close_reason_marker(thread: Thread, *, head_sha: str) -> str:
    return f"clearance-close-reason:{thread.id}:{head_sha[:12]}"


def existing_conclusion_markers(thread: Thread, *, head_sha: str) -> list[str]:
    if _verdict_value(thread) == "RESOLVED":
        return [close_reason_marker(thread, head_sha=head_sha)]
    return [conclusion_marker(thread, head_sha=head_sha)]


def _evidence_lines(thread: Thread, snapshot: ThreadSnapshot | None) -> list[str]:
    evidence = snapshot.evidence if snapshot else None
    if evidence and evidence.llm_evidence:
        return [_clip(item) for item in evidence.llm_evidence[:4]]

    lines: list[str] = []
    if evidence and evidence.thread_state:
        lines.append(f"Clearance thread state `{evidence.thread_state}`.")
    author_reply_id = getattr(thread, "author_reply_id", None)
    if author_reply_id:
        lines.append(f"Author reply observed at review comment `{author_reply_id}`.")
    if getattr(thread, "code_changed", None):
        lines.append("Current diff changed after the original review comment.")
    if snapshot and snapshot.github_state and snapshot.github_state.isOutdated:
        lines.append("GitHub marks the original review anchor as outdated on the current head.")
    if not lines:
        lines.append(
            f"Clearance's latest poll judged this review thread {_verdict_value(thread)} "
            f"on the current head."
        )
    return lines


def _detail_lines(
    thread: Thread,
    snapshot: ThreadSnapshot | None,
    *,
    verdict: str,
    model: str | None,
    llm_reason: str | None,
) -> list[str]:
    evidence = snapshot.evidence if snapshot else None
    lines = [f"- Verdict: `{verdict}`"]

    if llm_reason:
        if model:
            lines.append(f"- Model: `{_sanitize_markdown(model)}`")
        if evidence and evidence.llm_verdict:
            lines.append(f"- LLM verdict: `{_sanitize_markdown(evidence.llm_verdict)}`")
    else:
        lines.append("- Rule: SWM-1101 step 4-5")

    thread_state = evidence.thread_state if evidence else None
    if thread_state:
        lines.append(f"- Thread state: `{_sanitize_markdown(str(thread_state))}`")

    author_reply_id = (
        evidence.author_reply_id
        if evidence and evidence.author_reply_id
        else thread.author_reply_id
    )
    if author_reply_id:
        lines.append(f"- Author reply: review comment `{author_reply_id}`")

    if evidence and evidence.llm_evidence:
        lines.extend(f"- {_clip(item)}" for item in evidence.llm_evidence[:4])
    else:
        lines.extend(f"- {_clip(item)}" for item in _evidence_lines(thread, snapshot))

    return lines


def build_thread_conclusion_comment(
    thread: Thread,
    snapshot: ThreadSnapshot | None,
    *,
    head_sha: str,
    model: str | None = None,
) -> str:
    """Build the public GitHub reply posted under a Codex review thread.

    ``model`` is the model identifier that actually produced the LLM verdict
    (when present). Callers should pass the model from the investigator they
    used, not let it default — otherwise the comment can claim a model that
    is different from the one whose verdict it is rendering. The earlier
    implementation fell back to ``os.environ`` at render time, which under
    multi-model dispatch (Pro/Flash) could mislabel verdicts. GLM-5.1 H2 +
    MiniMax M2.7 M4 review flag.
    """
    evidence = snapshot.evidence if snapshot else None
    llm_reason = thread.llm_reason or (evidence.llm_reason if evidence else None)
    verdict = _verdict_value(thread)
    resolved = verdict == "RESOLVED"
    reason = (
        llm_reason
        or thread.verdict_reason
        or (
            "Clearance judged this review thread RESOLVED."
            if resolved
            else "Clearance did not find enough evidence to close this review thread."
        )
    )
    confidence = thread.llm_confidence or (evidence.llm_confidence if evidence else None)
    marker_name = (
        close_reason_marker(thread, head_sha=head_sha)
        if resolved
        else conclusion_marker(thread, head_sha=head_sha)
    )
    detail_lines = "\n".join(
        _detail_lines(thread, snapshot, verdict=verdict, model=model, llm_reason=llm_reason)
    )
    confidence_line = f"\n🎯 Confidence: `{confidence:.2f}`" if confidence is not None else ""
    return (
        f"<!-- {marker_name} -->\n"
        f"{_status_heading(verdict)}\n\n"
        f"{_checker_line(llm_reason=llm_reason, model=model)}"
        f"{confidence_line}\n"
        f"📍 Location: `{_location(thread)}`\n"
        f"🔖 Head: `{head_sha[:12]}`\n"
        f"💡 Why: {_clip(reason)}\n"
        f"{_action_line(verdict)}\n\n"
        "<details>\n"
        "<summary>Evidence</summary>\n\n"
        f"{detail_lines}\n\n"
        "</details>"
    )


def build_close_reason_comment(
    thread: Thread,
    snapshot: ThreadSnapshot | None,
    *,
    head_sha: str,
    model: str | None = None,
) -> str:
    """Build the public GitHub reply posted before resolving a review thread."""
    return build_thread_conclusion_comment(
        thread,
        snapshot,
        head_sha=head_sha,
        model=model,
    )
