"""Table-driven sentiment contract — FINAL auto-resolve contract (#334 ruling).

Auto-resolve accepts EXACTLY two signals:
1. the 👍 reaction from the comment's reaction FIELD (routing/clean-signal
   paths — never prose), and
2. the FORMAL Codex verdict comment ("Codex Review: Didn't find any major
   issues"), matched as the full verdict form.

ALL other prose — approval-sounding or not — returns None (the thread stays
OPEN for a human). Negative/concessive detection is fail-safe direction only
(negative → stays open / labels); it never closes anything. Fail-safe
asymmetry: a false OPEN costs one human click; a false RESOLVED closes an
unfixed defect.
"""

from __future__ import annotations

import pytest

from voyager.bots.clearance.judge import codex_followup_reaction

TABLE: list[tuple[str, str | None]] = [
    # the ONLY prose positive: formal verdict comments
    ("Codex Review: Didn't find any major issues. Nice work!", "positive"),
    ("codex review: no major issues found", "positive"),
    # approval-sounding prose: ALL leaves-open (human decides)
    ("The lgtm_status flag is now set; looks good.", None),
    ("LGTM? No.", "negative"),
    ("The concern is no longer unresolved; looks good.", None),
    ("This still looks good after retesting.", None),
    ("Doesn't regress anymore; looks good.", None),
    ("This has not regressed; the concern is resolved.", None),
    ("The regression was fixed; the concern is resolved.", None),
    ("This fixes the regression and looks good", None),
    ("Regression coverage was added, looks good", None),
    ("The `unresolved_threads` list is now empty; looks good.", None),
    ("No new issues. Nice work!", None),
    ("This isn't a regression. The concern is resolved.", None),
    ("I 👍 the effort", None),
    # negative/concessive (fail-safe direction)
    ("The issue is addressed, although the race can still occur", "negative"),
    ("The symptom may still reproduce under load", "negative"),
    ("The symptom is addressed, but the crash still reproduces.", "negative"),
    ("No new issues were introduced, but the original race persists.", "negative"),
    ("Nice work 👍 but the race persists", "negative"),
    ("the Windows path regressed", "negative"),
    ("The implementation is addressed, but no regression test covers it.", "negative"),
    ("The concern is addressed, but the behavior is still incorrect.", "negative"),
    ("The race still occurs at HEAD.", "negative"),
    ("No aspects of the reported issue were addressed", "negative"),
    ("The concern is resolved, but not the root cause.", "negative"),
    ("Looks fixed, not verified at HEAD.", "negative"),
    ("Resolved? No.", "negative"),
    ("Fixed? No—the race persists.", "negative"),
    # neutral
    ("I re-ran the analysis on the current head.", None),
]


@pytest.mark.parametrize(("body", "expected"), TABLE)
def test_sentiment_table(body: str, expected: str | None) -> None:
    assert codex_followup_reaction(body) == expected, body
