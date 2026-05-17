"""Stack bot — classification logic."""

from __future__ import annotations

import re
from typing import Any

from .constants import (
    ALL_STACK_LABELS,
    AREA_ALIASES,
    AREA_FIELD_NAMES,
    AREA_LABELS,
    AREA_SIGNALS,
    CONVENTIONAL_TYPE_TO_TYPE,
    ISSUE_KIND_TO_TYPE,
    RISK_KEYWORDS,
    RISK_LABELS,
    SIZE_LABELS,
    STACK_AREAS,
    STACK_CLASSIFIER_VERSION,
    STACK_NEEDS_REVIEW_LABEL,
    TYPE_ALIASES,
    TYPE_FIELD_NAMES,
    TYPE_LABELS,
)

AXIS_LABELS: dict[str, tuple[str, ...]] = {
    "type": TYPE_LABELS,
    "area": AREA_LABELS,
    "size": SIZE_LABELS,
    "risk": RISK_LABELS,
}

AXIS_PREFIXES: dict[str, str] = {
    "type": "stack-type-",
    "area": "stack-area-",
    "size": "stack-size-",
    "risk": "stack-risk-",
}


def normalize_text(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[_`>#\[\]():/.-]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def has_signal(normalized_text: str, signal: str) -> bool:
    normalized_signal = normalize_text(signal)
    if not normalized_signal:
        return False
    return re.search(rf"(?<!\w){re.escape(normalized_signal)}(?!\w)", normalized_text) is not None


def extract_inline_field(body: str, field_names: tuple[str, ...]) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        for field_name in field_names:
            match = re.match(
                rf"^(?:[-*]\s*)?(?:\*\*)?{re.escape(field_name)}(?:\*\*)?\s*[:：]\s*(?P<value>.+)$",  # noqa: RUF001
                stripped,
                flags=re.IGNORECASE,
            )
            if match:
                return match.group("value").strip()
    return ""


def extract_markdown_section(body: str, section_names: tuple[str, ...]) -> str:
    wanted = {normalize_text(name) for name in section_names}
    lines = body.splitlines()
    start = None
    heading_level = None
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$", line)
        if not match:
            continue
        if normalize_text(match.group("title")) in wanted:
            start = index + 1
            heading_level = len(match.group("marks"))
            break

    if start is None or heading_level is None:
        return ""

    collected: list[str] = []
    for line in lines[start:]:
        match = re.match(r"^\s{0,3}(?P<marks>#{1,6})\s+", line)
        if match and len(match.group("marks")) <= heading_level:
            break
        collected.append(line)
    return "\n".join(collected).strip()


def first_meaningful_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def extract_field_value(body: str, field_names: tuple[str, ...]) -> str:
    inline_value = extract_inline_field(body, field_names)
    if inline_value:
        return inline_value
    section_value = extract_markdown_section(body, field_names)
    return first_meaningful_line(section_value)


def match_alias(value: str, aliases: dict[str, tuple[str, ...]]) -> tuple[str | None, str | None]:
    normalized = f" {normalize_text(value)} "
    if not normalized.strip():
        return None, None
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            if has_signal(normalized, candidate):
                return canonical, candidate
    return None, None


def label_name(label: Any) -> str:
    if isinstance(label, str):
        return label
    if isinstance(label, dict):
        return str(label.get("name") or "")
    return ""


def existing_issue_axis_labels(target: dict[str, Any]) -> dict[str, str] | None:
    if target.get("target_kind", "issue") != "issue" or target.get("pull_request"):
        return None

    names = {name for raw_label in target.get("labels") or [] if (name := label_name(raw_label))}
    confirmed: dict[str, str] = {}
    for axis, valid_labels in AXIS_LABELS.items():
        matches = sorted(name for name in names if name in valid_labels)
        if len(matches) != 1:
            return None
        confirmed[axis] = matches[0]
    return confirmed


def classification_from_axis_labels(axis_labels: dict[str, str]) -> dict[str, str]:
    return {
        axis: axis_labels[axis].removeprefix(AXIS_PREFIXES[axis])
        for axis in ("type", "area", "size", "risk")
    }


def ordered_axis_labels(axis_labels: dict[str, str]) -> list[str]:
    return [axis_labels[axis] for axis in ("type", "area", "size", "risk")]


def area_tie_is_only_review_reason(reasons: list[str], area_result: dict[str, Any]) -> bool:
    ambiguous_reason = area_result.get("ambiguous_reason") or "Stack area is ambiguous."
    return bool(area_result.get("ambiguous") and reasons == [ambiguous_reason])


def classify_type(title: str, body: str) -> tuple[str, str]:
    field_value = extract_field_value(body, TYPE_FIELD_NAMES)
    explicit_type, _ = match_alias(field_value, TYPE_ALIASES)
    if explicit_type:
        return explicit_type, "explicit_field"

    issue_kind = re.match(r"^\[(?P<kind>[A-Za-z]+)\]:", title.strip())
    if issue_kind:
        mapped = ISSUE_KIND_TO_TYPE.get(issue_kind.group("kind").lower())
        if mapped:
            return mapped, "issue_title_kind"

    conventional = re.match(r"^(?P<kind>[a-z]+)(?:\([^)]+\))?!?:", title.strip().lower())
    if conventional:
        mapped = CONVENTIONAL_TYPE_TO_TYPE.get(conventional.group("kind"))
        if mapped:
            return mapped, "conventional_title"

    text = f" {normalize_text(title + chr(10) + body)} "
    if any(has_signal(text, word) for word in ("bug", "error", "failure", "broken", "regression")):
        return "bug", "keyword"
    if any(
        has_signal(text, word) for word in ("doc", "docs", "documentation", "readme", "sop", "adr")
    ):
        return "docs", "keyword"
    if any(has_signal(text, word) for word in ("test", "tests", "coverage", "fixture")):
        return "test", "keyword"
    if any(
        has_signal(text, word) for word in ("ci", "workflow", "github action", "github actions")
    ):
        return "ci", "keyword"
    if any(has_signal(text, word) for word in ("refactor", "cleanup", "rename")):
        return "refactor", "keyword"
    if any(has_signal(text, word) for word in ("feature", "add", "support", "enable")):
        return "feature", "keyword"
    return "task", "fallback"


def score_areas(title: str, body: str) -> tuple[list[tuple[str, int]], dict[str, list[str]]]:
    text = f" {normalize_text(title + chr(10) + body)} "
    scored: list[tuple[str, int]] = []
    hits_by_area: dict[str, list[str]] = {}
    for area, signals in AREA_SIGNALS.items():
        score = 0
        hits: list[str] = []
        for signal, weight in signals:
            if has_signal(text, signal):
                score += weight
                hits.append(signal)
        if score:
            scored.append((area, score))
            hits_by_area[area] = hits
    scored.sort(key=lambda item: (-item[1], STACK_AREAS.index(item[0])))
    return scored, hits_by_area


def classify_area(title: str, body: str) -> tuple[str, dict[str, Any]]:
    field_value = extract_field_value(body, AREA_FIELD_NAMES)
    explicit_area, explicit_signal = match_alias(field_value, AREA_ALIASES)
    scored, hits_by_area = score_areas(title, body)
    if explicit_area:
        return explicit_area, {
            "source": "explicit_field",
            "explicit_signal": explicit_signal,
            "scores": scored,
            "hits": hits_by_area,
            "ambiguous": False,
            "ambiguous_reason": "",
        }

    if not scored:
        return "unknown", {
            "source": "weighted_signals",
            "scores": [],
            "hits": {},
            "ambiguous": False,
            "ambiguous_reason": "",
        }

    top_area, top_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0
    ambiguous = bool(second_score and top_score == second_score)
    return top_area, {
        "source": "weighted_signals",
        "scores": scored,
        "hits": hits_by_area,
        "ambiguous": ambiguous,
        "ambiguous_reason": "Top Stack area scores are tied." if ambiguous else "",
    }


def classify_size(target: dict[str, Any], body: str) -> str:
    changed_files = int(target.get("changed_files") or 0)
    additions = int(target.get("additions") or 0)
    deletions = int(target.get("deletions") or 0)
    if changed_files or additions or deletions:
        churn = additions + deletions
        if changed_files <= 1 and churn <= 25:
            return "xs"
        if changed_files <= 3 and churn <= 120:
            return "s"
        if changed_files <= 8 and churn <= 450:
            return "m"
        if changed_files <= 20 and churn <= 1200:
            return "l"
        return "xl"

    checklist_items = len(re.findall(r"^\s*[-*]\s+\[[ xX]\]", body, flags=re.MULTILINE))
    meaningful_lines = [
        line for line in body.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    body_chars = len(normalize_text(body))
    if checklist_items <= 1 and body_chars <= 700:
        return "xs"
    if checklist_items <= 3 and body_chars <= 1800:
        return "s"
    if checklist_items <= 8 and body_chars <= 4500:
        return "m"
    if checklist_items <= 16 and len(meaningful_lines) <= 120:
        return "l"
    return "xl"


def classify_risk(classified_size: str, title: str, body: str) -> str:
    text = f" {normalize_text(title + chr(10) + body)} "
    if any(keyword in text for keyword in RISK_KEYWORDS["high"]):
        return "high"
    if classified_size in {"l", "xl"}:
        return "high"
    if any(keyword in text for keyword in RISK_KEYWORDS["medium"]):
        return "medium"
    if classified_size == "m":
        return "medium"
    return "low"


def review_reasons(
    *,
    title: str,
    body: str,
    type_source: str,
    area: str,
    area_result: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    normalized_title = normalize_text(title)
    normalized_body = normalize_text(body)
    if len(normalized_title) < 8:
        reasons.append("Title is too short for reliable Stack classification.")
    if type_source == "fallback" and len(normalized_body) < 120:
        reasons.append(
            "No explicit issue kind, conventional title, or useful body keywords were found."
        )
    if area == "unknown":
        reasons.append("No known Stack area keywords were found.")
    if area_result.get("ambiguous"):
        reasons.append(area_result.get("ambiguous_reason") or "Stack area is ambiguous.")
    if normalized_body in {"", "todo", "tbd", "n a", "n/a"}:
        reasons.append("Body is empty or placeholder-like.")
    return reasons


def classify_stack_target(target: dict[str, Any]) -> dict[str, Any]:
    title = str(target.get("title") or "")
    body = str(target.get("body") or "")
    stack_type, type_source = classify_type(title, body)
    area, area_result = classify_area(title, body)
    size = classify_size(target, body)
    risk = classify_risk(size, title, body)
    fresh_classification = {
        "type": stack_type,
        "area": area,
        "size": size,
        "risk": risk,
    }
    selected = [
        f"stack-type-{fresh_classification['type']}",
        f"stack-area-{fresh_classification['area']}",
        f"stack-size-{fresh_classification['size']}",
        f"stack-risk-{fresh_classification['risk']}",
    ]
    reasons = review_reasons(
        title=title,
        body=body,
        type_source=type_source,
        area=area,
        area_result=area_result,
    )
    needs_review = bool(reasons)
    human_override = False
    human_override_reason = ""
    confirmed_axis_labels = existing_issue_axis_labels(target)
    classification = fresh_classification
    if (
        needs_review
        and confirmed_axis_labels is not None
        and area_tie_is_only_review_reason(reasons, area_result)
    ):
        human_override = True
        human_override_reason = "Preserved existing human-confirmed classification because top Stack area scores are tied."
        classification = classification_from_axis_labels(confirmed_axis_labels)
        selected = ordered_axis_labels(confirmed_axis_labels)
        needs_review = False

    labels: dict[str, list[str]] = (
        {
            "add": [STACK_NEEDS_REVIEW_LABEL],
            "remove": [label for label in ALL_STACK_LABELS if label != STACK_NEEDS_REVIEW_LABEL],
        }
        if needs_review
        else {
            "add": selected,
            "remove": [label for label in ALL_STACK_LABELS if label not in selected],
        }
    )
    reactions: dict[str, list[str]] = (
        {"add": ["eyes"], "remove": ["rocket"]}
        if needs_review
        else {"add": ["rocket"], "remove": ["eyes"]}
    )

    return {
        "status": "stack_needs_review" if needs_review else "stack_classified",
        "conclusion": "neutral" if needs_review else "success",
        "issue_number": target.get("number"),
        "issue_url": target.get("html_url"),
        "target_kind": target.get("target_kind", "issue"),
        "classifier": STACK_CLASSIFIER_VERSION,
        "title": title,
        "classification": classification,
        "confidence": {
            "needs_review": needs_review,
            "reasons": [] if human_override else reasons,
            "type_source": type_source,
            "area_source": area_result.get("source"),
            "area_matches": [area_name for area_name, _ in area_result.get("scores", [])],
            "area_scores": dict(area_result.get("scores", [])),
            "area_hits": area_result.get("hits", {}),
            "area_ambiguous": area_result.get("ambiguous", False),
            "human_override": human_override,
            "human_override_reason": human_override_reason,
            "suggested_classification": fresh_classification,
        },
        "labels": labels,
        "reactions": reactions,
        "summary": (
            human_override_reason
            if human_override
            else "Stack needs a human review before writing classification axis labels."
            if needs_review
            else f"Stack classified this issue as {classification['type']}/{classification['area']}/{classification['size']}/{classification['risk']}."
        ),
    }
