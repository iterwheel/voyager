"""Blueprint bot — issue intake validation and routing."""

from __future__ import annotations

import re
from typing import Any

BLUEPRINT_AGENT_SLUG = "iterwheel-blueprint"
BLUEPRINT_AGENT_ID = "github-blueprint-agent"
BLUEPRINT_COMMENT_MARKER = "<!-- iterwheel:blueprint-intake -->"
BLUEPRINT_NEEDED_LABEL = "blueprint-needed"
BLUEPRINT_READY_LABEL = "blueprint-ready"
BLUEPRINT_REVISION_LABEL = "blueprint-requests-revision"

ISSUE_TITLE_KINDS = (
    "Task",
    "Bug",
    "Feature",
    "Docs",
    "Refactor",
    "Chore",
    "CI",
    "Test",
    "Spike",
)
ISSUE_TITLE_RE = re.compile(
    rf"^\[(?P<kind>{'|'.join(ISSUE_TITLE_KINDS)})\]:\s+(?P<summary>.+)$",
    re.IGNORECASE,
)

REQUIRED_SECTIONS = (
    "Work Type",
    "Problem / Goal",
    "Context",
    "Expected Outcome",
    "Acceptance Criteria",
    "Reproduction Steps / Task Plan",
    "Priority",
    "Requester / Owner",
)

SECTION_ALIASES = {
    "work type": "Work Type",
    "problem goal": "Problem / Goal",
    "context": "Context",
    "expected outcome": "Expected Outcome",
    "acceptance criteria": "Acceptance Criteria",
    "reproduction steps task plan": "Reproduction Steps / Task Plan",
    "task plan": "Reproduction Steps / Task Plan",
    "reproduction steps": "Reproduction Steps / Task Plan",
    "priority": "Priority",
    "requester owner": "Requester / Owner",
    "owner": "Requester / Owner",
    "requester": "Requester / Owner",
}

MIN_MEANINGFUL_CHARS = {
    "Work Type": 2,
    "Problem / Goal": 20,
    "Context": 10,
    "Expected Outcome": 10,
    "Acceptance Criteria": 5,
    "Reproduction Steps / Task Plan": 5,
    "Priority": 2,
    "Requester / Owner": 2,
}

EMPTY_RESPONSES = {
    "",
    "-",
    ".",
    "na",
    "n a",
    "n/a",
    "none",
    "no response",
    "_no response_",
    "not applicable",
    "todo",
    "tbd",
    "later",
}


def normalize_heading(value: str) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("&amp;", "&")
    normalized = re.sub(r"`", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def strip_markdown_noise(value: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    lines = [line.strip() for line in without_comments.replace("\r\n", "\n").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalized_response(value: str) -> str:
    compact = strip_markdown_noise(value).lower()
    compact = re.sub(r"[*_`>#\[\]():]+", " ", compact)
    compact = re.sub(r"[^a-z0-9/@._-]+", " ", compact)
    return re.sub(r"\s+", " ", compact).strip()


def is_meaningful(value: str, *, min_chars: int) -> bool:
    compact = strip_markdown_noise(value)
    if not compact:
        return False
    normalized = normalized_response(compact)
    if normalized in EMPTY_RESPONSES:
        return False
    alnum_count = len(re.sub(r"[^a-z0-9]", "", normalized))
    return alnum_count >= min_chars


def extract_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    current_level: int | None = None

    for line in body.replace("\r\n", "\n").splitlines():
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            heading = SECTION_ALIASES.get(normalize_heading(match.group(2)))
            if heading:
                current = heading
                current_level = level
                sections.setdefault(current, [])
                continue
            if current and current_level is not None and level > current_level:
                sections[current].append(line)
                continue
            current = None
            current_level = None
            continue
        if current:
            sections[current].append(line)

    return {name: strip_markdown_noise("\n".join(lines)) for name, lines in sections.items()}


def validate_issue_title(title: str) -> dict[str, Any]:
    clean = title.strip()
    if not clean:
        return {"missing": ["Title"], "weak": [], "parsed": {}}

    match = ISSUE_TITLE_RE.match(clean)
    if not match:
        if not is_meaningful(clean, min_chars=8):
            return {"missing": ["Title"], "weak": [], "parsed": {}}
        return {"missing": [], "weak": ["Title format"], "parsed": {}}

    summary = match.group("summary").strip()
    parsed = {"kind": match.group("kind"), "summary": summary}
    if not is_meaningful(summary, min_chars=8):
        return {"missing": [], "weak": ["Title"], "parsed": parsed}
    return {"missing": [], "weak": [], "parsed": parsed}


def acceptance_criteria_are_meaningful(value: str) -> bool:
    compact = strip_markdown_noise(value)
    if not is_meaningful(compact, min_chars=MIN_MEANINGFUL_CHARS["Acceptance Criteria"]):
        return False

    item_pattern = re.compile(r"^\s*(?:[-*]\s+(?:\[[ xX]\]\s*)?|\d+\.\s+)(.+?)\s*$")
    for line in compact.splitlines():
        match = item_pattern.match(line)
        if match and is_meaningful(match.group(1), min_chars=5):
            return True
    return False


def validate_blueprint_issue(issue: dict[str, Any]) -> dict[str, Any]:
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    sections = extract_sections(body)

    missing: list[str] = []
    weak: list[str] = []

    title_check = validate_issue_title(title)
    missing.extend(title_check["missing"])
    weak.extend(title_check["weak"])

    for section in REQUIRED_SECTIONS:
        value = sections.get(section, "")
        if not value:
            missing.append(section)
            continue
        if section == "Acceptance Criteria":
            if not acceptance_criteria_are_meaningful(value):
                weak.append(section)
            continue
        if not is_meaningful(value, min_chars=MIN_MEANINGFUL_CHARS[section]):
            weak.append(section)

    status = "blueprint_ready" if not missing and not weak else "blueprint_requests_revision"
    conclusion = "success" if status == "blueprint_ready" else "failure"
    labels: dict[str, list[str]] = (
        {
            "add": [BLUEPRINT_READY_LABEL],
            "remove": [BLUEPRINT_NEEDED_LABEL, BLUEPRINT_REVISION_LABEL],
        }
        if status == "blueprint_ready"
        else {
            "add": [BLUEPRINT_REVISION_LABEL],
            "remove": [BLUEPRINT_NEEDED_LABEL, BLUEPRINT_READY_LABEL],
        }
    )
    reactions: dict[str, list[str]] = (
        {"add": ["rocket"], "remove": []}
        if status == "blueprint_ready"
        else {"add": [], "remove": ["rocket"]}
    )

    return {
        "status": status,
        "conclusion": conclusion,
        "issue_number": issue.get("number"),
        "issue_url": issue.get("html_url"),
        "title": title,
        "missing": missing,
        "weak": weak,
        "labels": labels,
        "reactions": reactions,
        "title_check": title_check,
        "sections_found": sorted(sections.keys()),
        "summary": (
            "Issue satisfies the Blueprint intake template."
            if status == "blueprint_ready"
            else "Issue needs more intake detail before it is Blueprint-ready."
        ),
    }


def build_blueprint_comment(validation: dict[str, Any]) -> str:
    lines = [
        BLUEPRINT_COMMENT_MARKER,
        "Blueprint intake check",
        "",
    ]
    if validation["status"] == "blueprint_ready":
        lines.extend(
            [
                "Status: blueprint-ready",
                "",
                "This issue has the required intake fields and at least one concrete acceptance criterion.",
            ]
        )
        return "\n".join(lines).strip()

    lines.extend(
        [
            "Status: blueprint-requests-revision",
            "",
            "Please complete the missing or weak intake fields before this is ready for agent work.",
        ]
    )

    if validation["missing"]:
        lines.extend(["", "Missing fields:"])
        lines.extend(f"- {field}" for field in validation["missing"])

    if validation["weak"]:
        lines.extend(["", "Fields that need more detail:"])
        lines.extend(f"- {field}" for field in validation["weak"])

    if (
        "Title" in validation["missing"]
        or "Title" in validation["weak"]
        or "Title format" in validation["weak"]
    ):
        lines.extend(
            [
                "",
                "Issue title should start with a Blueprint kind and a concrete summary:",
                "",
                "`[Task]: Add Blueprint issue template`",
                "",
                "Allowed kinds:",
                "",
                ", ".join(f"`{kind}`" for kind in ISSUE_TITLE_KINDS),
            ]
        )

    if (
        "Acceptance Criteria" in validation["missing"]
        or "Acceptance Criteria" in validation["weak"]
    ):
        lines.extend(
            [
                "",
                "Acceptance Criteria should include at least one concrete, verifiable item, for example:",
                "",
                "- [ ] The expected behavior is verified by a reproducible test or manual check.",
                "- [ ] The expected output is visible in the UI, API response, logs, or generated artifact.",
            ]
        )

    return "\n".join(lines).strip()


def should_run_blueprint(event: str, payload: dict[str, Any]) -> bool:
    action = payload.get("action")
    if event == "issues" and action in {"opened", "edited", "reopened"}:
        return True
    if event == "issue_comment" and action == "created":
        body = str((payload.get("comment") or {}).get("body") or "")
        return "/blueprint" in body.lower()
    return False


def route_blueprint_event(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not should_run_blueprint(event, payload):
        return []

    issue = payload.get("issue") or {}
    if issue.get("pull_request"):
        return []

    validation = validate_blueprint_issue(issue)
    comment_body = build_blueprint_comment(validation)
    return [
        {
            "agent": BLUEPRINT_AGENT_SLUG,
            "agent_id": BLUEPRINT_AGENT_ID,
            "kind": "issue_blueprint_validation",
            "event": event,
            "action": payload.get("action"),
            "validation": validation,
            "writeback": {
                "comment_marker": BLUEPRINT_COMMENT_MARKER,
                "comment_body": comment_body,
                "labels": validation["labels"],
                "reactions": validation["reactions"],
            },
        }
    ]
