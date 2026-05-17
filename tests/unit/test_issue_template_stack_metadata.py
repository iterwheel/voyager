from __future__ import annotations

from pathlib import Path

from voyager.bots.stack.constants import STACK_AREAS, STACK_TYPES

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / ".github/ISSUE_TEMPLATE/iterwheel_issue.md"


def test_issue_template_documents_optional_stack_metadata_fields() -> None:
    text = TEMPLATE.read_text()

    assert "Stack Type:" in text
    assert "Stack Area:" in text
    assert "Leave blank when unsure." in text

    for stack_type in STACK_TYPES:
        assert stack_type in text
    for stack_area in STACK_AREAS:
        if stack_area != "unknown":
            assert stack_area in text
