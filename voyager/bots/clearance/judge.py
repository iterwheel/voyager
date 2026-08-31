"""Verdict assignment per SWM-1101 (Decision Tree steps 3-6).

Heuristic contract (class-closing, per the #334 scope ruling): this module
parses Codex follow-up prose with a PRECEDENCE-ORDERED heuristic —

1. DECISIVE APPROVAL first: explicit approval phrases (looks good /
   no new issues / nice work / lgtm) and the 👍 reaction decide
   RESOLVED before any negative-state keyword scan runs, unless a hard
   negator is directly attached after the approval phrase itself.
2. NEGATIVE-STATE keywords second (asserted regressions, still-* failure
   forms, unresolved/unaddressed/unfixed/persists, missing coverage...).
3. Token attachment analysis last (negators before/after addressed /
   resolved / fixed).

Prose is unbounded: new English paraphrases of the same sentiment are
NOT defects of this heuristic — they are the reason a structured-signal
protocol (machine-readable verdicts instead of prose parsing) is the
long-term fix, tracked separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from voyager.bots.clearance.classify import ThreadState
from voyager.bots.clearance.models import Verdict

# The GitHub thumbs-up reaction VALUE, consumed ONLY from the structured
# reaction field on the comment object (routing's reaction events and the
# clean-signal path) — never detected inside prose bodies, where a 👍 glyph
# is an ordinary character (scope ruling on #334). Also NOT a credential
# (bandit B105 false-positived on inline comparisons).
APPROVAL_REACTION = "\U0001f44d"

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


# Decisive approval phrases (precedence 1). "addressed"/"resolved"/"fixed"
# are NOT here — they are weaker tokens analyzed at precedence 3.
_APPROVAL_PHRASES = ("looks good", "no new issues", "nice work", "lgtm")
# Concessive still-occurrence: "the race can still occur" — outranks approval
# words because the reviewer explicitly says the failure mode remains.
_CONCESSIVE_STILL_RE = re.compile(
    r"\b(?:can|could|may|might|will|would|does|do)\s+still\s+"
    r"(?:occur|reproduce|happen|persist|remain|be\s+(?:present|seen|triggered|hit))\b"
)


def _decisive_approval(text: str) -> bool:
    """True when an explicit approval phrase stands without an attached
    hard negator (``looks good, but not verified`` is not decisive)."""
    for phrase in _APPROVAL_PHRASES:
        start = 0
        while True:
            idx = text.find(phrase, start)
            if idx < 0:
                break
            after = text[idx + len(phrase) : idx + len(phrase) + 48]
            after = re.split(r"[.!?]", after)[0]
            if not _AFTER_NEGATOR_RE.match(after):
                return True
            start = idx + len(phrase)
    return False  # the 👍 reaction is a structured signal read from the
    # reaction field (routing/clean-signal paths) — never from prose bodies


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
    # Ruling ladder (class-closing, #334 + #335): structured signal >
    # concessive still-occurrence > approval words > negative keywords >
    # token attachment.
    # 1. structured signal (👍 reaction) is NOT detectable from prose — it is
    # read from the reaction field in routing and the clean-signal path.
    if _CONCESSIVE_STILL_RE.search(text):  # 2. concessive still-occurrence
        return "negative"
    if _decisive_approval(text):  # 3. approval words
        return "positive"

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
            "is still incorrect",
            "still incorrect",
            "still wrong",
            "behavior is incorrect",
            "remains incorrect",
            "remains wrong",
            "result is incorrect",
            "output is wrong",
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
    ) or _NEGATIVE_WORDS_RE.search(text):
        return "negative"
    # "fixed" is negation-only (Codex P1 on #335): bare "fixed" is too weak to
    # approve on its own, but "hasn't been fixed" must still classify negative.
    positives = ["looks good", "no new issues", "addressed", "resolved"]
    negation_only_tokens = ["fixed"]
    any_positive = False
    any_negated_positive = False
    for token in positives + negation_only_tokens:
        # Single WORDS match on word boundaries: identifiers stay neutral
        # ('addressed_threads' contains neither an approval nor a rejection).
        token_re = re.compile(rf"\b{re.escape(token)}\b") if " " not in token else None
        start = 0
        while True:
            if token_re is not None:
                m = token_re.search(text, start)
                pos = m.start() if m else -1
            else:
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
# Standalone negative words (word-bounded — identifiers like
# `unresolved_threads` stay neutral, Codex P2 round 19).
_NEGATIVE_WORDS_RE = re.compile(r"\b(?:unaddressed|unresolved|unfixed|persists?)\b")
_REGRESSION_RE = re.compile(r"\bregress(?:ed|es|ing|ion|ions)?\b(?!\s+(?:tests?|coverage|suite)\b)")
_NEGATED_REGRESSION_RE = re.compile(
    r"\b(?:not|no|never|isn['\u2019]?t|aren['\u2019]?t|wasn['\u2019]?t|weren['\u2019]?t|"
    r"hasn['\u2019]?t|haven['\u2019]?t|didn['\u2019]?t)\s+"
    r"(?:\w+\s+){0,2}regress(?:ed|es|ing|ion|ions)?\b"
)


# Attachment breakers inside a no-phrase: sentence punctuation (already
# clipped), another approval phrase, or a positive token.
_APPROVAL_OR_BREAK_RE = re.compile(
    r"looks good|no new issues|nice work|lgtm|\baddressed\b|\bresolved\b|\bfixed\b"
)

# Passive repaired tail: "regression was fixed / has been resolved ..." —
# the regression is being repaired, not reported.
_REGRESSION_REPAIRED_TAIL_RE = re.compile(
    r"regress(?:ed|es|ing|ion|ions)?\b[\s,]*(?:was|is|are|has been|have been|"
    r"gets|got|now)\s+(?:fixed|resolved|covered|prevented|addressed|mitigated|added|gone)\b"
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
        # Passive repaired form ("the regression was fixed"): not asserted.
        if _REGRESSION_REPAIRED_TAIL_RE.search(text[max(0, start - 4) : start + 64]):
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
    window = re.split(r"[.!?;]", window)[-1]
    if _NEGATOR_WIDE_RE.search(window):
        return True
    # Bare "no" attaches across a noun phrase when nothing breaks the span:
    # "No aspects of the reported issue were addressed" negates; the
    # affirmative "no new issues … looks good" carries its own approval
    # phrase in the span and does not reach this precedence level.
    no_match = re.search(r"\bno\b", window)
    if no_match and not _APPROVAL_OR_BREAK_RE.search(window[no_match.end() :]):
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
    r"incorrect|wrong|broken|fails?|failing|"
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
