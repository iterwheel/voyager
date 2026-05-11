"""Stack bot — comment body builder."""

from __future__ import annotations

from typing import Any

from .constants import STACK_AREAS, STACK_CLASSIFIER_VERSION, STACK_COMMENT_MARKER


def build_stack_comment(classification: dict[str, Any]) -> str:
    values = classification["classification"]
    labels = classification["labels"]
    lines = [
        STACK_COMMENT_MARKER,
        "Stack classification",
        "",
        f"Classifier: {classification.get('classifier', STACK_CLASSIFIER_VERSION)}",
        "",
    ]
    if classification["status"] == "stack_classified":
        lines.extend(
            [
                "Status: stack-classified",
                "",
                "Stack applied one issue classification label per axis.",
                "",
                "Applied labels:",
            ]
        )
        lines.extend(f"- `{label}`" for label in labels["add"])
        return "\n".join(lines).strip()

    lines.extend(
        [
            "Status: stack-needs-review",
            "",
            "Stack could not classify this issue confidently. Please choose or confirm the classification labels.",
            "",
            "Review reasons:",
        ]
    )
    lines.extend(f"- {reason}" for reason in classification["confidence"]["reasons"])
    area_scores = classification["confidence"].get("area_scores") or {}
    if area_scores:
        lines.extend(
            [
                "",
                "Top area scores:",
            ]
        )
        for area, score in sorted(
            area_scores.items(), key=lambda item: (-item[1], STACK_AREAS.index(item[0]))
        )[:4]:
            lines.append(f"- `stack-area-{area}`: {score}")
    lines.extend(
        [
            "",
            "Suggested classification:",
            f"- Type: `stack-type-{values['type']}`",
            f"- Area: `stack-area-{values['area']}`",
            f"- Size: `stack-size-{values['size']}`",
            f"- Risk: `stack-risk-{values['risk']}`",
        ]
    )
    return "\n".join(lines).strip()
