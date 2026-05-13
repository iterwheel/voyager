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
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import httpx
import openai

if TYPE_CHECKING:
    from voyager.core.config import Profile
    from voyager.llm.deepseek import DeepSeekClient

_log = logging.getLogger(__name__)

# Models supported by DeepSeek's V4 lineup. Pro is the default; Flash is
# substantially cheaper but weaker at multi-step semantic reasoning, so we
# warn when the factory builds an investigator targeting it. DeepSeek M3
# review flag — the confidence threshold was tuned against Pro.
_KNOWN_PRO_MODELS = frozenset({"deepseek-v4-pro", "deepseek-reasoner"})
# Recognized Flash-tier models. When the operator picks one of these we know
# it's intentional; when they pick something *else* we surface a stronger
# "unknown model" warning in build_investigator_from_env.
_KNOWN_FLASH_MODELS = frozenset({"deepseek-v4-flash", "deepseek-chat"})

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
    """Coerced verdict the investigator returns to its caller.

    ``raw_text`` preserves the **model's** original output (post-coercion is
    not reflected). When V4 thinking mode produced ``reasoning_content``, it
    is appended after the content as ``content\\n\\n--- reasoning ---\\n…`` so
    downstream auditors can replay the model's chain-of-thought against
    the final ``verdict`` / ``confidence`` / ``reason`` fields, which may
    have been downgraded by ``_coerce_decision`` (e.g., RESOLVED below the
    min_confidence threshold becomes NEEDS_HUMAN_JUDGMENT but ``raw_text``
    still shows RESOLVED). DeepSeek N3 round-2 review flag.
    """

    verdict: InvestigatorVerdict
    confidence: float
    reason: str
    evidence: list[str]
    raw_text: str | None = None


class ThreadInvestigator(Protocol):
    max_diff_chars: int

    async def investigate(self, item: ThreadInvestigationInput) -> InvestigationDecision: ...


class InvestigationError(RuntimeError):
    """LLM/integration failure. Callers should fall back to deterministic logic."""


def _truthy(value: str | None) -> bool:
    return bool(value) and (value or "").lower() not in {"0", "false", "no", "off"}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]..."


def _strip_fenced_block(text: str) -> str:
    """Strip a single leading+trailing fenced code block, if present.

    Handles both ``​```json ... ```​`` and bare ``​``` ... ```​`` markers
    appearing anywhere in the text. DeepSeek L1 review flag — the original
    fenced-block stripper only triggered when the response *started* with a
    backtick, so a "Here is the verdict:\n```{...}```" response fell through
    to the regex fallback. With the brace-counting walker that fallback is
    safer, but a uniform strip keeps the direct-parse path warm.
    """
    return re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)


def _iter_balanced_objects(text: str) -> Iterator[str]:
    """Yield each top-level balanced ``{...}`` substring, in document order.

    Single forward pass, string-literal aware: braces inside a JSON string
    literal do not affect depth, and backslash escapes are honoured so that
    ``\\"`` does not prematurely close the string. Replaces a greedy
    ``re.search(r"\\{.*\\}", text, re.S)`` matcher — that pattern would
    match from the first ``{`` to the *last* ``}`` and produce a single
    invalid union of any pair of objects (DeepSeek H1 review flag).

    String-state is only tracked **inside** a ``{...}`` region (``depth > 0``).
    A bare quote in reasoning preamble (e.g., ``The author said "I fixed it.``)
    used to flip ``in_string=True`` and never flip back, leaving subsequent
    braces at the wrong depth and silently swallowing the real verdict object —
    MiniMax M2.7 N2 + GLM-5.1 §3 round-2 review flagged it as a regression
    against the prior greedy matcher.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if depth > 0 and ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start : i + 1]
                start = -1


def _extract_json_object(text: str) -> dict:
    """Extract one JSON object from a possibly-noisy LLM response.

    Strategy, in order of decreasing assumption:
      1. Try ``json.loads`` directly on the whitespace-stripped text. A model
         that emitted exactly one JSON object — including ``"evidence":[…]``
         strings whose values contain literal backticks — parses cleanly and
         neither the fence-strip nor the walker runs. DeepSeek N2 round-2
         review flag: this MUST come before any backtick handling, otherwise
         legitimate JSON with inline-code evidence is corrupted.
      2. On failure, strip fenced code blocks anywhere and retry ``json.loads``.
         The fenced-block stripper uses non-greedy matching so two adjacent
         fenced blocks are not collapsed across.
      3. On failure, walk every top-level balanced ``{...}`` in document
         order (over the fence-stripped text) and return the first fragment
         that parses as valid JSON. This tolerates reasoning preambles that
         contain brace-not-JSON fragments (e.g., ``{a:1, b:2}``) and
         multi-object outputs.

    The original ``JSONDecodeError`` from the direct parse is re-raised if no
    candidate fragment parses — it preserves the most useful position
    information for debugging.
    """
    text = text.strip()
    try:
        return dict(json.loads(text))
    except json.JSONDecodeError as primary_exc:
        unfenced = _strip_fenced_block(text).strip()
        if unfenced != text:
            try:
                return dict(json.loads(unfenced))
            except json.JSONDecodeError:
                pass
        for fragment in _iter_balanced_objects(unfenced):
            try:
                return dict(json.loads(fragment))
            except json.JSONDecodeError:
                continue
        raise primary_exc from None


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
    """Build the user message — pure data payload, no instructions.

    DeepSeek M4 review flag — instructions belong in the system prompt so the
    output schema is part of the model's persona, not co-resident with the
    payload data. With ``thinking=True``, V4 processes the system prompt
    first; mixing instructions into the user message made them compete with
    the JSON payload for attention.
    """
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
    return f"Input:\n{json.dumps(payload, ensure_ascii=False)}"


_SYSTEM_PROMPT = (
    "You are the Clearance investigator: a semantic code-review verifier for "
    "GitHub PR review threads. Given a Codex review comment, an author reply, "
    "and a PR diff excerpt, decide whether the author's fix genuinely addresses "
    "the reviewer's concern in the current head.\n"
    "Use only the provided PR diff excerpt, review comment, and author reply. "
    "Do not assume fixes that are not evidenced. If the evidence is partial, "
    "ambiguous, outside the diff excerpt, or requires running code, choose "
    "NEEDS_HUMAN_JUDGMENT.\n"
    "Return exactly one JSON object with this schema and no extra prose:\n"
    '{"verdict":"RESOLVED|OPEN|NEEDS_HUMAN_JUDGMENT","confidence":0.0,'
    '"reason":"short factual reason","evidence":["quoted or paraphrased evidence"]}'
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
        thinking: bool = True,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client
        self.max_diff_chars = max_diff_chars
        self.min_confidence = min_confidence
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort

    async def investigate(self, item: ThreadInvestigationInput) -> InvestigationDecision:
        from voyager.llm.deepseek import Message

        prompt = _build_prompt(item, max_diff_chars=self.max_diff_chars)
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=prompt),
        ]
        try:
            turn = await self._client.complete(
                messages,
                thinking=self._thinking,
                reasoning_effort=self._reasoning_effort,
            )
        except (httpx.HTTPError, openai.APIError, ValueError) as exc:
            # Narrow the catch to integration exceptions — DeepSeekClient
            # normalizes server errors to openai.APIError / httpx.HTTPError /
            # ValueError. A bare ``except Exception`` (DeepSeek M2 review
            # flag) would also swallow AttributeError/TypeError from a future
            # refactor and surface them as "DeepSeek call failed", obscuring
            # programming bugs.
            raise InvestigationError(f"DeepSeek call failed: {exc}") from exc

        content = (turn.content or "").strip()
        reasoning = (turn.reasoning_content or "").strip()
        if not content:
            # GLM M4 + DeepSeek H2 review flag — silently collapsing
            # ``content=None`` to "" lost the diagnostic that V4 thinking
            # mode produced reasoning but no final content (typically a
            # token-budget exhaustion during thinking).
            diagnostic = "DeepSeek returned empty content"
            if reasoning:
                diagnostic += (
                    " (reasoning-only response — likely max_tokens reached during thinking; "
                    "raise the budget or shrink the prompt)"
                )
            raise InvestigationError(diagnostic)

        # Preserve reasoning_content in the audit trail so a downstream
        # reviewer can replay the model's chain-of-thought against the
        # verdict. DeepSeek M1 review flag — dropping the reasoning is the
        # observability gap on an automated path that influences merges.
        audit_text = content if not reasoning else f"{content}\n\n--- reasoning ---\n{reasoning}"
        try:
            raw = _extract_json_object(content)
        except json.JSONDecodeError as exc:
            raise InvestigationError(f"could not parse investigator JSON: {exc}") from exc
        return _coerce_decision(raw, min_confidence=self.min_confidence, raw_text=audit_text)


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

    # MiniMax M2.7 + DeepSeek M3 review flag: routing Pro vs Flash should be a
    # deliberate per-deployment choice. Flash is ~4x cheaper but materially
    # weaker on multi-step semantic reasoning, and the 0.78 min_confidence
    # threshold was calibrated against Pro. Warn loudly when a non-Pro model
    # is wired up via env so the operator notices in logs. The Flash allowlist
    # lets us tailor the warning: "this is a known Flash, did you mean it?"
    # vs "we don't recognize this model at all".
    if model not in _KNOWN_PRO_MODELS:
        # Carry the article in the tier phrase so the rendered sentence
        # reads "is a Flash-tier model" / "is an unrecognized model"
        # — DeepSeek round-3 R3-N2 grammar fix.
        tier = "a Flash-tier" if model in _KNOWN_FLASH_MODELS else "an unrecognized"
        _log.warning(
            "investigator: VOYAGER_INVESTIGATOR_MODEL=%r is %s model "
            "(known Pro: %s). The min_confidence=%.2f threshold was tuned against "
            "Pro, so verdicts may bypass the gate with lower-quality reasoning. "
            "Re-evaluate the threshold or pin to a Pro model.",
            model,
            tier,
            ", ".join(sorted(_KNOWN_PRO_MODELS)),
            min_confidence,
        )

    from voyager.llm.deepseek import DeepSeekClient

    client = DeepSeekClient(api_key=api_key, model=model)
    return DeepSeekInvestigator(
        client=client,
        max_diff_chars=max_diff,
        min_confidence=min_confidence,
    )


def build_investigator_from_profile(
    profile: Profile,
    *,
    api_key: str,
) -> DeepSeekInvestigator:
    """Build a DeepSeekInvestigator from a config-loaded Profile.

    The Profile carries all the DeepSeek-specific knobs (model, thinking,
    reasoning_effort) plus the investigator-level thresholds (max_diff_chars,
    min_confidence). The ``api_key`` stays a separate parameter — it lives in
    ``VOYAGER_DEEPSEEK_API_KEY`` and shouldn't be persisted in the TOML config
    (secrets out of files).

    Phase 7B-3 will use this from the webhook dispatch layer; 7B-2 just exposes
    the construction path.
    """
    if profile.model not in _KNOWN_PRO_MODELS:
        tier = "a Flash-tier" if profile.model in _KNOWN_FLASH_MODELS else "an unrecognized"
        _log.warning(
            "investigator: profile %r uses %s model %r "
            "(known Pro: %s). The min_confidence=%.2f threshold was tuned against "
            "Pro, so verdicts may bypass the gate with lower-quality reasoning. "
            "Re-evaluate the threshold or pin to a Pro model.",
            profile.name,
            tier,
            profile.model,
            ", ".join(sorted(_KNOWN_PRO_MODELS)),
            profile.min_confidence,
        )

    from voyager.llm.deepseek import DeepSeekClient

    client = DeepSeekClient(api_key=api_key, model=profile.model)
    return DeepSeekInvestigator(
        client=client,
        max_diff_chars=profile.max_diff_chars,
        min_confidence=profile.min_confidence,
        thinking=profile.thinking,
        reasoning_effort=profile.reasoning_effort,
    )
