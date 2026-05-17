"""Regression tests for Stack issue-label preservation."""

from __future__ import annotations

from voyager.bots.stack import route_stack_event
from voyager.bots.stack.classifier import classify_stack_target

TIED_BODY = "This task updates SOP rules and workflow loop bot details for the Stack classifier."
CONFIRMED_LABELS = [
    {"name": "stack-type-docs"},
    {"name": "stack-area-automation"},
    {"name": "stack-size-l"},
    {"name": "stack-risk-high"},
    {"name": "stack-needs-review"},
]


def test_tied_area_preserves_confirmed_issue_payload_labels() -> None:
    routes = route_stack_event(
        "issues",
        {
            "action": "opened",
            "issue": {
                "number": 37,
                "title": "[Task]: Classify mixed governance work",
                "body": TIED_BODY,
                "html_url": "https://github.test/issues/37",
                "labels": CONFIRMED_LABELS,
            },
        },
    )

    validation = routes[0]["validation"]
    writeback = routes[0]["writeback"]

    assert validation["status"] == "stack_classified"
    assert validation["confidence"]["human_override"] is True
    assert validation["classification"] == {
        "type": "docs",
        "area": "automation",
        "size": "l",
        "risk": "high",
    }
    assert writeback["labels"]["add"] == [
        "stack-type-docs",
        "stack-area-automation",
        "stack-size-l",
        "stack-risk-high",
    ]
    assert "stack-needs-review" in writeback["labels"]["remove"]


def test_tied_area_ignores_confirmed_labels_on_pr_targets() -> None:
    result = classify_stack_target(
        {
            "number": 37,
            "target_kind": "pull_request",
            "title": "[Task]: Classify mixed governance work",
            "body": TIED_BODY,
            "html_url": "https://github.test/pull/37",
            "labels": CONFIRMED_LABELS,
        }
    )

    assert result["status"] == "stack_needs_review"
    assert result["confidence"]["human_override"] is False


def test_stack_route_skips_pr_conversation_labels() -> None:
    routes = route_stack_event(
        "issue_comment",
        {
            "action": "created",
            "comment": {"body": "/stack"},
            "issue": {
                "number": 37,
                "title": "[Task]: Classify mixed governance work",
                "body": TIED_BODY,
                "html_url": "https://github.test/pull/37",
                "pull_request": {"url": "https://api.github.test/pulls/37"},
                "labels": CONFIRMED_LABELS,
            },
        },
    )

    assert routes == []
