"""Optional LLM investigator for review-thread verdicts.

The investigator never writes to GitHub. It only turns a review-thread evidence
bundle into a structured verdict that upstream logic can accept or ignore.

Backend: voyager.llm.deepseek.DeepSeekClient (replaces sweeping-monk's Codex
CLI subprocess). The public interfaces — ThreadInvestigationInput,
InvestigationDecision, ThreadInvestigator, InvestigationError — are preserved
from the sweeping-monk source so Phase B wiring requires no interface changes.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from voyager.llm.deepseek import DeepSeekClient

InvestigatorVerdict = Literal["RESOLVED", "OPEN", "NEEDS_HUMAN_JUDGMENT"]


@dataclass(frozen=True)
class ThreadInvestigationInput:
    repo: str
    pr: int
    pr_title: str | None
    head_sha: str
    path: str
    line: int | None
    classification: Literal["B", "C"]
    codex_comment_body: str
    author_reply_body: str | None
    diff_excerpt: str
    heuristic_verdict: str
    heuristic_reason: str


@dataclass(frozen=True)
class InvestigationDecision:
    verdict: InvestigatorVerdict
    confidence: float
    reason: str
    evidence: list[str]
    raw_text: str | None = None


class ThreadInvestigator(Protocol):
    async def investigate(self, item: ThreadInvestigationInput) -> InvestigationDecision: ...


class InvestigationError(RuntimeError):
    """LLM/integration failure. Callers should fall back to deterministic logic."""


def _truthy(value: str | None) -> bool:
    return bool(value) and (value or "").lower() not in {"0", "false", "no", "off"}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]..."


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return dict(json.loads(stripped))
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise
        return dict(json.loads(match.group(0)))


def _coerce_decision(raw: dict, *, min_confidence: float, raw_text: str) -> InvestigationDecision:
    verdict = str(raw.get("verdict") or "").upper()
    if verdict not in {"RESOLVED", "OPEN", "NEEDS_HUMAN_JUDGMENT"}:
        raise InvestigationError(f"invalid investigator verdict: {verdict!r}")
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError) as exc:
        raise InvestigationError("invalid investigator confidence") from exc
    confidence = max(0.0, min(1.0, confidence))
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        raise InvestigationError("investigator reason is empty")
    evidence_raw = raw.get("evidence")
    evidence = (
        [str(item).strip() for item in evidence_raw if str(item).strip()]
        if isinstance(evidence_raw, list)
        else []
    )
    if verdict == "RESOLVED" and confidence < min_confidence:
        verdict = "NEEDS_HUMAN_JUDGMENT"
        reason = f"LLM confidence {confidence:.2f} below threshold {min_confidence:.2f}: {reason}"
    return InvestigationDecision(
        verdict=verdict,  # type: ignore[arg-type]
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        raw_text=raw_text,
    )


def _build_prompt(item: ThreadInvestigationInput, *, max_diff_chars: int) -> str:
    payload = {
        "repo": item.repo,
        "pr": item.pr,
        "pr_title": item.pr_title,
        "head_sha": item.head_sha,
        "thread_location": {"path": item.path, "line": item.line},
        "thread_classification": item.classification,
        "codex_review_comment": item.codex_comment_body,
        "author_reply": item.author_reply_body,
        "heuristic": {
            "verdict": item.heuristic_verdict,
            "reason": item.heuristic_reason,
        },
        "diff_excerpt": _truncate(item.diff_excerpt, max_diff_chars),
    }
    return (
        "You are the Clearance investigator for a GitHub PR review thread. "
        "Decide whether the review concern is actually fixed in the current head.\n"
        "Use only the provided PR diff excerpt, review comment, and author reply. "
        "Do not assume fixes that are not evidenced. If the evidence is partial, "
        "ambiguous, outside the diff excerpt, or requires running code, choose "
        "NEEDS_HUMAN_JUDGMENT.\n"
        "Return exactly one JSON object with this schema:\n"
        '{"verdict":"RESOLVED|OPEN|NEEDS_HUMAN_JUDGMENT","confidence":0.0,'
        '"reason":"short factual reason","evidence":["quoted or paraphrased evidence"]}\n'
        "Input:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


_SYSTEM_PROMPT = (
    "You are a semantic code-review verifier. Given a Codex review comment, an author "
    "reply, and a PR diff excerpt, determine whether the author's fix genuinely addresses "
    "the reviewer's concern. Think step by step before deciding."
)


class DeepSeekInvestigator:
    """Investigator backend using voyager.llm.deepseek.DeepSeekClient.

    Replaces the Codex CLI subprocess from sweeping-monk. Same interface,
    same JSON contract — only the transport layer changes.
    """

    def __init__(
        self,
        *,
        client: DeepSeekClient,
        max_diff_chars: int = 20000,
        min_confidence: float = 0.78,
    ) -> None:
        self._client = client
        self.max_diff_chars = max_diff_chars
        self.min_confidence = min_confidence

    async def investigate(self, item: ThreadInvestigationInput) -> InvestigationDecision:
        from voyager.llm.deepseek import Message

        prompt = _build_prompt(item, max_diff_chars=self.max_diff_chars)
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=prompt),
        ]
        try:
            turn = await self._client.complete(messages, thinking=True)
        except Exception as exc:
            raise InvestigationError(f"DeepSeek call failed: {exc}") from exc

        raw_text = turn.content or ""
        try:
            raw = _extract_json_object(raw_text)
        except Exception as exc:
            raise InvestigationError(f"could not parse investigator JSON: {exc}") from exc
        return _coerce_decision(raw, min_confidence=self.min_confidence, raw_text=raw_text)


def build_investigator_from_env() -> DeepSeekInvestigator | None:
    """Build a DeepSeekInvestigator from environment variables, or return None if disabled.

    Environment variables:
        VOYAGER_INVESTIGATOR_ENABLED   — set to "1" / "true" / "yes" to enable
        VOYAGER_INVESTIGATOR_MODEL     — model name (default: deepseek-v4-pro)
        VOYAGER_DEEPSEEK_API_KEY       — API key (required when enabled)
        VOYAGER_INVESTIGATOR_MAX_DIFF_CHARS  — max diff chars (default: 20000)
        VOYAGER_INVESTIGATOR_MIN_CONFIDENCE  — min confidence threshold (default: 0.78)
    """
    if not _truthy(os.environ.get("VOYAGER_INVESTIGATOR_ENABLED")):
        return None
    api_key = os.environ.get("VOYAGER_DEEPSEEK_API_KEY", "")
    if not api_key:
        raise InvestigationError("VOYAGER_DEEPSEEK_API_KEY is not set")
    model = os.environ.get("VOYAGER_INVESTIGATOR_MODEL", "deepseek-v4-pro")
    max_diff = int(os.environ.get("VOYAGER_INVESTIGATOR_MAX_DIFF_CHARS", "20000"))
    min_confidence = float(os.environ.get("VOYAGER_INVESTIGATOR_MIN_CONFIDENCE", "0.78"))

    from voyager.llm.deepseek import DeepSeekClient

    client = DeepSeekClient(api_key=api_key, model=model)
    return DeepSeekInvestigator(
        client=client,
        max_diff_chars=max_diff,
        min_confidence=min_confidence,
    )
