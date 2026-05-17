"""Focused tests for the Stack comment renderer."""

from __future__ import annotations

from voyager.bots.stack.comment import build_stack_comment
from voyager.bots.stack.constants import STACK_COMMENT_MARKER


def test_classified_comment_uses_compact_emoji_panel() -> None:
    body = build_stack_comment(
        {
            "status": "stack_classified",
            "classifier": "stack-v2",
            "classification": {
                "type": "docs",
                "area": "automation",
                "size": "l",
                "risk": "high",
            },
            "labels": {
                "add": [
                    "stack-type-docs",
                    "stack-area-automation",
                    "stack-size-l",
                    "stack-risk-high",
                ]
            },
            "confidence": {
                "type_source": "issue_title_kind",
                "area_source": "weighted_signals",
                "area_scores": {"automation": 8, "docs": 4},
                "human_override": False,
            },
        }
    )

    assert body.startswith(f"{STACK_COMMENT_MARKER}\n## Stack")
    assert "🏷️ Type: docs (`stack-type-docs`)" in body
    assert "🛰️ Area: automation (`stack-area-automation`)" in body
    assert "📏 Size: l (`stack-size-l`)" in body
    assert "🔥 Risk: high (`stack-risk-high`)" in body
    assert "✅ Status: classified" in body
    assert "Next: ready for pickup." in body
    assert "<details>" in body
    assert "Applied labels:" in body


def test_classified_comment_explains_preserved_human_override() -> None:
    body = build_stack_comment(
        {
            "status": "stack_classified",
            "classifier": "stack-v2",
            "classification": {
                "type": "docs",
                "area": "automation",
                "size": "l",
                "risk": "high",
            },
            "labels": {
                "add": [
                    "stack-type-docs",
                    "stack-area-automation",
                    "stack-size-l",
                    "stack-risk-high",
                ]
            },
            "confidence": {
                "type_source": "issue_title_kind",
                "area_source": "weighted_signals",
                "area_scores": {"automation": 8, "docs": 8},
                "human_override": True,
                "human_override_reason": "Preserved existing human-confirmed classification because top Stack area scores are tied.",
            },
        }
    )

    assert "Preserved existing human-confirmed classification." in body
    assert (
        "Preservation reason: Preserved existing human-confirmed classification because top Stack area scores are tied."
        in body
    )


def test_needs_review_comment_uses_compact_emoji_panel() -> None:
    body = build_stack_comment(
        {
            "status": "stack_needs_review",
            "classifier": "stack-v2",
            "classification": {
                "type": "feature",
                "area": "github",
                "size": "m",
                "risk": "high",
            },
            "labels": {"add": ["stack-needs-review"]},
            "confidence": {
                "reasons": ["Top Stack area scores are tied."],
                "type_source": "issue_title_kind",
                "area_source": "weighted_signals",
                "area_scores": {"github": 10, "docs": 10, "tests": 10, "automation": 8},
                "human_override": False,
            },
        }
    )

    assert body.startswith(f"{STACK_COMMENT_MARKER}\n## Stack")
    assert "👀 Status: needs review" in body
    assert "Next: confirm one label per Stack axis or adjust the issue metadata." in body
    assert "Suggested labels:" in body
    assert "Review reasons:" in body
    assert "Top Stack area scores are tied." in body
    assert "Area scores:" in body
