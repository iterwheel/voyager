"""Verdict assignment per SWM-1101 (Decision Tree steps 3-6).

The 'substantively reasonable' heuristic deliberately stays conservative:
we only return RESOLVED when there is concrete evidence (specific identifier,
sufficient length, no obvious deflection pattern). Borderline cases collapse to
NEEDS_HUMAN_JUDGMENT so the maintainer sees them rather than a false RESOLVED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from voyager.bots.clearance.models import Verdict

ThreadState = Literal["A", "B", "C"]

_COMMIT_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_FILE_RE = re.compile(r"\b[\w./-]+\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|sh|yml|yaml|toml|md)\b")
_IDENTIFIER_RE = re.compile(
    r"`[^`\n]{2,}`|\b(?:gh|git|npm|cargo|make|pip|api|graphql|mutation)\b", re.I
)
_DEFLECT_RE = re.compile(r"\b(thanks|won't fix|wontfix|ack|noted|will look)\b", re.I)


def is_substantive_reply(body: str | None) -> bool:
    """True when the reply (a) is at least ~50 chars, (b) names a concrete identifier,
    and (c) is not predominantly a deflection phrase.
    """
    if not body:
        return False
    text = body.strip()
    if len(text) < 50:
        return False
    has_identifier = bool(
        _COMMIT_SHA_RE.search(text) or _FILE_RE.search(text) or _IDENTIFIER_RE.search(text)
    )
    if not has_identifier:
        return False
    if _DEFLECT_RE.search(text) and len(text) < 100 and not _COMMIT_SHA_RE.search(text):  # noqa: SIM103
        return False
    return True


def codex_followup_reaction(followup_body: str | None) -> str | None:
    """Detect 👍 / 👎 / textual approval signals in a Codex follow-up. Returns
    'positive' / 'negative' / None.

    Negative phrases are checked **first** because positive substrings would
    otherwise match inside an explicit negation: ``"not addressed"`` contains
    the substring ``"addressed"``, ``"still not resolved"`` contains the
    substring ``"resolved"``. Codex automated review on PR #8 flagged this
    misclassification — a Codex follow-up rejecting the fix was treated as
    approval and would have produced a wrong RESOLVED verdict downstream.
    """
    if not followup_body:
        return None
    text = followup_body.lower()
    if any(
        token in text
        for token in [
            "not addressed",
            "not resolved",
            "still not",
            "still has",
            "still ",
            "concern remains",
            "👎",
        ]
    ):
        return "negative"
    if any(
        token in text for token in ["looks good", "no new issues", "addressed", "resolved", "👍"]
    ):
        return "positive"
    return None


@dataclass(frozen=True)
class VerdictDecision:
    verdict: Verdict
    reason: str
    substantive: bool | None = None


def judge(
    *,
    classification: ThreadState,
    author_reply_body: str | None,
    code_changed: bool,
    codex_followup_body: str | None,
    github_isResolved: bool = False,  # noqa: N803 — matches GitHub GraphQL field name
) -> VerdictDecision:
    """Apply SWM-1101 steps 3-6 in order, returning the final verdict.

    ``github_isResolved`` is the system-of-record fast-path: if GitHub says
    the thread is resolved (manual UI resolve, prior Stage 1.5 sync, or
    SWM-1103 maintainer override), trust it over the local classifier.
    Otherwise, step 6 (Codex follow-up) overrides steps 3-5.
    """
    if github_isResolved:
        return VerdictDecision(
            Verdict.RESOLVED,
            "GitHub reports isResolved=true (external resolve / Stage 1.5 sync / maintainer override)",
        )

    reaction = codex_followup_reaction(codex_followup_body)
    if reaction == "positive":
        return VerdictDecision(Verdict.RESOLVED, "Codex follow-up signaled approval (step 6)")
    if reaction == "negative":
        return VerdictDecision(Verdict.OPEN, "Codex follow-up restated concern (step 6)")

    if classification == "B":
        if code_changed:
            return VerdictDecision(
                Verdict.RESOLVED,
                "thread outdated; author commit changed the lines Codex anchored to (SWM-1101 step 3)",
            )
        # SWM-1101 §Decision Tree step 3 enumerates three outcomes for state B:
        #   addresses-the-failure  → RESOLVED
        #   touches-but-not-addressing → OPEN
        #   makes-worse → OPEN (with severity escalation, handled elsewhere)
        # `code_changed=False` collapses the last two: the diff anchor was
        # invalidated (isOutdated=true) but our diff comparator did not see the
        # change as addressing the named concern. Per spec, that is OPEN — the
        # original concern still applies at the new anchor. The faithful port
        # from sweeping-monk returned NEEDS_HUMAN_JUDGMENT here, which deviated
        # from the SOP; MiniMax M2.7 flagged it and we align with the spec.
        return VerdictDecision(
            Verdict.OPEN,
            "thread outdated by unrelated edit; original concern still applies in new diff (SWM-1101 step 3)",
        )

    if classification == "C":
        substantive = is_substantive_reply(author_reply_body)
        if substantive:
            return VerdictDecision(
                Verdict.RESOLVED,
                "author reply substantive (cites concrete identifier, ≥50 chars) per SWM-1101 step 4-5",
                substantive=True,
            )
        return VerdictDecision(
            Verdict.OPEN,
            "author reply non-substantive or borderline; defer to maintainer",
            substantive=False,
        )

    return VerdictDecision(Verdict.OPEN, "no author response and no code change (SWM-1101 step 5)")
