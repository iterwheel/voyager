"""Table-driven sentiment matcher contract (class-closing, #334 + #335 rulings).

The precedence ladder (top decides first):
1. structured signal   — the 👍 reaction
2. concessive clauses  — "can/could/may/might/does still occur/reproduce/…"
3. approval words      — looks good / no new issues / nice work / lgtm,
                         unless a hard negator is attached after them
4. negative keywords   — asserted regressions, still-* failure forms,
                         unresolved/unaddressed/unfixed/persists,
                         missing test coverage
5. token attachment    — negators before/after addressed/resolved/fixed

Every historical finding family from PRs #334/#335 lives here as a row.
New English paraphrases of an existing row's sentiment are NOT a defect of
this heuristic (see the module docstring: structured-signal protocol is the
long-term fix).
"""

from __future__ import annotations

import pytest

from voyager.bots.clearance.judge import codex_followup_reaction

TABLE: list[tuple[str, str | None]] = [
    # 1. structured signal — reaction FIELD only; a 👍 glyph in prose is an
    # ordinary character (scope ruling counter-example) and never approves.
    ("Nice work 👍 but the race persists", "negative"),
    ("I 👍 the effort", None),
    # 2. concessive still-occurrence
    ("The issue is addressed, although the race can still occur", "negative"),
    ("The symptom may still reproduce under load", "negative"),
    # 3. approval words
    ("The concern is no longer unresolved; looks good.", "positive"),
    ("This still looks good after retesting.", "positive"),
    ("Doesn't regress anymore; looks good.", "positive"),
    ("This has not regressed; the concern is resolved.", "positive"),
    ("The regression was fixed; the concern is resolved.", "positive"),
    ("This fixes the regression and looks good", "positive"),
    ("Regression coverage was added, looks good", "positive"),
    ("The `unresolved_threads` list is now empty; looks good.", "positive"),
    ("No new issues. Nice work!", "positive"),
    # 4. negative keywords
    ("The symptom is addressed, but the crash still reproduces.", "negative"),
    ("No new issues were introduced, but the original race persists.", "negative"),
    ("the Windows path regressed", "negative"),
    ("The implementation is addressed, but no regression test covers it.", "negative"),
    ("The concern is addressed, but the behavior is still incorrect.", "negative"),
    ("The race still occurs at HEAD.", "negative"),
    ("No aspects of the reported issue were addressed", "negative"),
    # 5. token attachment
    ("The concern is resolved, but not the root cause.", "negative"),
    ("Looks fixed, not verified at HEAD.", "negative"),
    ("Resolved? No.", "negative"),
    ("Fixed? No—the race persists.", "negative"),
    ("This isn't a regression. The concern is resolved.", "positive"),
    # neutral
    ("I re-ran the analysis on the current head.", None),
]


@pytest.mark.parametrize(("body", "expected"), TABLE)
def test_sentiment_table(body: str, expected: str | None) -> None:
    assert codex_followup_reaction(body) == expected, body
