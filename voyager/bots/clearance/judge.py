"""Verdict assignment per SWM-1101 (Decision Tree steps 3-6).

The 'substantively reasonable' heuristic deliberately stays conservative:
we only return RESOLVED when there is concrete evidence (specific identifier,
sufficient length, no obvious deflection pattern). Borderline cases collapse to
NEEDS_HUMAN_JUDGMENT so the maintainer sees them rather than a false RESOLVED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from voyager.bots.clearance.classify import ThreadState
from voyager.bots.clearance.models import Verdict

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

    Issue #249: the fixed token list missed phrasings like ``"has not been
    addressed"``, ``"remains unresolved"`` and ``"unaddressed"`` — the
    negated positive then classified as approval. Positive tokens now only
    count when no negator (``not``, ``n't`` forms, ``never``, ``no``, ``un-``,
    ``remains``, ``still`` …) precedes them within a word window; a negated
    positive is itself a negative signal.
    """
    if not followup_body:
        return None
    text = followup_body.lower()
    # Codex P2 round 14: a NEGATED regression ('has not regressed', 'No
    # regression introduced') is an approval, not a rejection — only
    # affirmative regression statements reject.
    if _has_affirmative_regression(text):
        return "negative"
    if any(
        token in text
        for token in [
            "not addressed",
            "not resolved",
            "still not",
            "still has",
            "concern remains",
            "unaddressed",
            "unresolved",
            "unfixed",
            "persists",
            "persist.",
            "still present",
            "still occurs",
            "still happens",
            "still broken",
            "still fails",
            "still failing",
            "still missing",
            "still open",
            "still reproduces",
            "still reproduce",
            "still seen",
            "still observed",
            "still triggers",
            "still triggered",
            "still recurs",
            "no regression test",
            "no test covers",
            "no tests cover",
            "not covered by any test",
            "without a regression test",
            "without any test",
            "regression remains",
            "partially addressed",
            "partially resolved",
            "partially fixed",
            "only partially",
            "not fully",
            "partially fixed",
            "incomplete",
            "partially",
            "👎",
        ]
    ):
        return "negative"
    # "fixed" is negation-only (Codex P1 on #335): bare "fixed" is too weak to
    # approve on its own, but "hasn't been fixed" must still classify negative.
    positives = ["looks good", "no new issues", "addressed", "resolved", "👍"]
    negation_only_tokens = ["fixed"]
    any_positive = False
    any_negated_positive = False
    for token in positives + negation_only_tokens:
        start = 0
        while True:
            pos = text.find(token, start)
            if pos < 0:
                break
            if _positive_is_negated(text, pos, len(token)):
                any_negated_positive = True
            elif token not in negation_only_tokens:
                any_positive = True
            start = pos + len(token)
    # Codex P1 on #254/#334: an explicit negation anywhere in the follow-up is
    # stronger evidence than an unnegated positive — "the leak is fixed but
    # the race is not addressed" rejects the fix. Negative wins.
    if any_negated_positive:
        return "negative"
    if any_positive:
        return "positive"
    return None


_NEGATOR_RE = re.compile(
    r"\b(?:"
    r"not|never|no|none|nobody|nothing|"
    r"isn['\u2019]?t|aren['\u2019]?t|wasn['\u2019]?t|weren['\u2019]?t|won['\u2019]?t|"
    r"don['\u2019]?t|doesn['\u2019]?t|didn['\u2019]?t|hasn['\u2019]?t|haven['\u2019]?t|"
    r"can['\u2019]?t|cannot|cant|dont|doesnt|didnt|hasnt|havent|"
    r"remains?|yet|without|lack(?:s|ing|ed)?|missing"
    r")\b"
)
_NEGATION_WINDOW = 48
# Affirmative regression statement vs negated regression (an approval).
# Every common inflection (Codex P1 round 15: 'regresses' read as approval).
_REGRESSION_RE = re.compile(r"\bregress(?:ed|es|ing|ion|ions)?\b(?!\s+tests?\b)")
_NEGATED_REGRESSION_RE = re.compile(
    r"\b(?:not|no|never|isn['\u2019]?t|aren['\u2019]?t|wasn['\u2019]?t|weren['\u2019]?t|"
    r"hasn['\u2019]?t|haven['\u2019]?t|didn['\u2019]?t)\s+"
    r"(?:\w+\s+){0,2}regress(?:ed|es|ing|ion|ions)?\b"
)


def _has_affirmative_regression(text: str) -> bool:
    """True when ANY regression occurrence is not locally negated (Codex P1
    round 15: a negated occurrence elsewhere must not mask this one)."""
    for match in _REGRESSION_RE.finditer(text):
        start = match.start()
        # The negated form binds to the verb within ~3 words; look at the
        # 24 chars before this occurrence, same sentence only.
        before = text[max(0, start - 24) : start]
        before = before.rsplit(".", 1)[-1].rsplit("!", 1)[-1].rsplit("?", 1)[-1]
        if _NEGATED_REGRESSION_LEAD_RE.search(before):
            continue
        # Codex P2 round 16: merely DISCUSSING a regression (fixing/preventing
        # one, or referencing a regression test) is not an asserted regression.
        if _REGRESSION_HANDLING_LEAD_RE.search(before):
            continue
        return True
    return False


# Handling verbs directly before the noun: the regression is being fixed /
# prevented / tested, not reported.
_REGRESSION_HANDLING_LEAD_RE = re.compile(
    r"\b(?:fix(?:es|ed)?|prevent(?:s|ed)?|address(?:es|ed)?|resolv(?:es|ed)?|"
    r"handles?|handled|mitigat(?:es|ed)?|covers?|covered|tests?)\s+(?:the\s+|a\s+|this\s+)?$"
)

_NEGATED_REGRESSION_LEAD_RE = re.compile(
    r"(?:not|no|never|isn['\u2019]?t|aren['\u2019]?t|wasn['\u2019]?t|weren['\u2019]?t|"
    r"hasn['\u2019]?t|haven['\u2019]?t|didn['\u2019]?t)\s+(?:\w+\s+){0,2}$"
)


def _positive_is_negated(text: str, pos: int, token_len: int) -> bool:
    """True when a negator word attaches to the positive token at ``pos``.

    Conservative by design (#249): a negator anywhere in the short window
    BEFORE a positive token negates it — a missed true positive keeps the
    thread open for a human, while a missed negation auto-resolves a rejected
    finding. Also catches the glued ``un-`` prefix (``unaddressed``), and a
    hard negator immediately AFTER the token ("looks fixed, not verified" —
    Codex P1 on #334). Bare "no" after a token is affirmative in Codex's
    closing idiom ("addressed. No further action needed") and is NOT an
    after-negator.
    """
    if pos >= 2 and text[pos - 2 : pos] == "un" and (pos == 2 or not text[pos - 3].isalnum()):
        return True
    window = text[max(0, pos - _NEGATION_WINDOW) : pos]
    # A negator in a COMPLETED sentence does not govern a later positive
    # token (Codex P2 round 10): clip at the last sentence boundary.
    window = re.split(r"[.!?]", window)[-1]
    if _NEGATOR_WIDE_RE.search(window):
        return True
    # Bare "no" negates only in immediate proximity (nothing but short filler
    # between it and the token): "no issues were addressed" negates, while
    # the affirmative idiom "no new issues introduced, looks good" keeps the
    # distant token positive (Codex P1 rounds on #335).
    if _CLOSE_NO_RE.search(window[-24:]):
        return True
    after_full = text[pos + token_len : pos + token_len + 48]
    # "Resolved? No." — the question-No form legitimately crosses the '?'.
    if _AFTER_QUESTION_NO_RE.match(after_full):
        return True
    # A negator in a LATER completed sentence must not negate this token
    # (Codex P2 round 15): stop the scan at the first sentence end.
    after = re.split(r"[.!?]", after_full)[0]
    return bool(_AFTER_NEGATOR_RE.match(after))


_AFTER_NEGATOR_RE = re.compile(
    r"^[\s,.!?;:]{0,6}(?:but|however|though|yet)?[^.!?]{0,44}?"
    r"(?:not|never|nor|remains?|persists?|reproduces?|recurs?|"
    r"isn['\u2019]?t|aren['\u2019]?t|wasn['\u2019]?t|"
    r"won['\u2019]?t|don['\u2019]?t|doesn['\u2019]?t|didn['\u2019]?t|"
    r"hasn['\u2019]?t|haven['\u2019]?t|can['\u2019]?t|cannot)\b"
)
_AFTER_QUESTION_NO_RE = re.compile(r"^[?\s]{0,4}no(?:[\s\u2014\u2013-]|[.!?]|$)")

# Wide negator set WITHOUT bare "no" (proximity-handled separately).
_NEGATOR_WIDE_RE = re.compile(
    r"\b(?:"
    r"not|never|none|nobody|nothing|"
    r"isn['\u2019]?t|aren['\u2019]?t|wasn['\u2019]?t|weren['\u2019]?t|won['\u2019]?t|"
    r"don['\u2019]?t|doesn['\u2019]?t|didn['\u2019]?t|hasn['\u2019]?t|haven['\u2019]?t|"
    r"can['\u2019]?t|cannot|cant|dont|doesnt|didnt|hasnt|havent|"
    r"remains?|yet|without|lack(?:s|ing|ed)?|missing"
    r")\b"
)
_CLOSE_NO_RE = re.compile(r"\bno\b[^.!?]{0,16}$")


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
            # Issue #253: the length/identifier heuristic alone must not
            # mutate thread state — a fork-PR author can satisfy it with
            # crafted prose. Route to NEEDS_HUMAN_JUDGMENT; the pipeline sends
            # substantive state-C threads to the investigator for corroboration
            # and only a corroborated verdict may resolve.
            return VerdictDecision(
                Verdict.NEEDS_HUMAN_JUDGMENT,
                "author reply substantive but uncorroborated; requires investigator "
                "verdict or head-SHA change (SWM-1101 step 4-5, issue #253)",
                substantive=True,
            )
        return VerdictDecision(
            Verdict.OPEN,
            "author reply non-substantive or borderline; defer to maintainer",
            substantive=False,
        )

    return VerdictDecision(Verdict.OPEN, "no author response and no code change (SWM-1101 step 5)")
