"""Stack bot — comment body builder."""

from __future__ import annotations

from typing import Any

from .constants import STACK_AREAS, STACK_CLASSIFIER_VERSION, STACK_COMMENT_MARKER


def stack_label(axis: str, value: str) -> str:
    return f"stack-{axis}-{value}"


def append_area_scores(lines: list[str], area_scores: dict[str, Any]) -> None:
    if not area_scores:
        return
    lines.extend(["", "- Area scores:"])
    for area, score in sorted(
        area_scores.items(), key=lambda item: (-item[1], STACK_AREAS.index(item[0]))
    )[:4]:
        lines.append(f"  - `stack-area-{area}`: {score}")


def append_label_list(lines: list[str], heading: str, labels: list[str]) -> None:
    lines.extend(["", f"- {heading}:"])
    lines.extend(f"  - `{label}`" for label in labels)


def append_review_reasons(lines: list[str], reasons: list[str]) -> None:
    if not reasons:
        return
    lines.extend(["", "- Review reasons:"])
    lines.extend(f"  - {reason}" for reason in reasons)


def build_stack_comment(classification: dict[str, Any]) -> str:
    values = classification["classification"]
    labels = classification["labels"]
    confidence = classification["confidence"]
    selected_labels = [
        stack_label("type", values["type"]),
        stack_label("area", values["area"]),
        stack_label("size", values["size"]),
        stack_label("risk", values["risk"]),
    ]
    classified = classification["status"] == "stack_classified"
    status_line = "✅ Status: classified" if classified else "👀 Status: needs review"
    next_line = (
        "Next: ready for pickup."
        if classified
        else "Next: confirm one label per Stack axis or adjust the issue metadata."
    )
    lines = [
        STACK_COMMENT_MARKER,
        "## Stack",
        "",
        f"🏷️ Type: {values['type']} (`{selected_labels[0]}`)",
        f"🛰️ Area: {values['area']} (`{selected_labels[1]}`)",
        f"📏 Size: {values['size']} (`{selected_labels[2]}`)",
        f"🔥 Risk: {values['risk']} (`{selected_labels[3]}`)",
        status_line,
        "",
        next_line,
        "",
        "<details>",
        "<summary>Details</summary>",
        "",
        f"- Classifier: {classification.get('classifier', STACK_CLASSIFIER_VERSION)}",
        f"- Type source: {confidence.get('type_source') or 'unknown'}",
        f"- Area source: {confidence.get('area_source') or 'unknown'}",
    ]

    if classified:
        append_label_list(lines, "Applied labels", labels["add"])
        if confidence.get("human_override", False):
            lines.extend(
                [
                    "",
                    "- Preserved existing human-confirmed classification.",
                    f"- Preservation reason: {confidence['human_override_reason']}",
                ]
            )
    else:
        append_label_list(lines, "Suggested labels", selected_labels)
        append_review_reasons(lines, confidence.get("reasons") or [])

    append_area_scores(lines, confidence.get("area_scores") or {})
    lines.extend(["", "</details>"])
    return "\n".join(lines).strip()
