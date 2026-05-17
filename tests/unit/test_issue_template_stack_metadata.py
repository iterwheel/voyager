from __future__ import annotations

from pathlib import Path

from voyager.bots.blueprint import extract_sections, validate_blueprint_issue
from voyager.bots.stack.classifier import classify_area
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


def test_issue_template_keeps_stack_metadata_out_of_work_type_section() -> None:
    sections = extract_sections(TEMPLATE.read_text())

    assert "Stack Type:" not in sections.get("Work Type", "")
    assert "Stack Area:" not in sections.get("Work Type", "")


def test_blank_stack_metadata_does_not_satisfy_work_type() -> None:
    body = """## Work Type

## Stack Metadata

Stack Type:
Stack Area:

## Problem / Goal

Ship the issue template without false Blueprint readiness.

## Context

The optional Stack metadata placeholders are blank.

## Expected Outcome

Blueprint asks for an actual Work Type description.

## Acceptance Criteria

- [ ] Blank optional metadata does not count as Work Type content.

## Reproduction Steps / Task Plan

1. Open the issue with blank metadata.

## Priority

P2 - template correctness.

## Requester / Owner

Requester: @frankyxhl
Owner: Iterwheel Stack
"""

    result = validate_blueprint_issue({"title": "[Task]: Validate blank metadata", "body": body})

    assert result["status"] == "blueprint_requests_revision"
    assert "Work Type" in result["missing"] or "Work Type" in result["weak"]


def test_stack_ignores_template_html_comment_hint_lists_when_metadata_blank() -> None:
    body = """## Work Type

Template guidance.

## Stack Metadata

<!--
Allowed Stack Area values:
github, automation, docs, ci, tests, frontend, backend, infra
-->

Stack Area:

## Problem / Goal

No real area signal exists outside the comment.
"""

    area, details = classify_area("[Task]: Triage placeholder", body)

    assert area == "unknown"
    assert details["scores"] == []
