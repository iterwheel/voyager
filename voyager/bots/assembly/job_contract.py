"""Assembly bot — Job Contract dataclass + builder.

Per VOY-1817 Surface 6 and §Assembly Job Contract Schema.  D14 governs the
acceptance-criteria and task-summary fallbacks.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .constants import FORBIDDEN_OPERATIONS, VERIFICATION_COMMANDS


@dataclass(frozen=True)
class AssemblyJobContract:
    """The structured contract handed to the execution adapter."""

    repository: str
    issue_number: int
    issue_url: str
    issue_title: str
    issue_body: str
    branch_name: str
    base_branch: str
    task_summary: str
    acceptance_criteria: list[str]
    forbidden_operations: tuple[str, ...]
    verification_commands: tuple[str, ...]
    delivery_id: str
    requested_at: str
    # D14 provenance — "section" when extracted from the marked-up section,
    # "title_fallback" when Blueprint allowed it through but the section was
    # later removed (non-empty title), "empty_fallback" when both the section
    # and the title are empty (CHG-1819 F4: acceptance_criteria-only; the
    # task_summary path keeps the two-state section/title_fallback semantics
    # because an empty summary string renders harmlessly).
    acceptance_criteria_source: str = "section"
    task_summary_source: str = "section"
    # Used by tests + the writeback dispatcher for serialisation; kept off
    # the equality comparison.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict (lists, tuples coerced to lists)."""
        data = asdict(self)
        data["forbidden_operations"] = list(self.forbidden_operations)
        data["verification_commands"] = list(self.verification_commands)
        return data


# ---------------------------------------------------------------------------
# Section extraction — mirrors Blueprint's heading parser but is purpose-
# built for the two sections Assembly cares about (Problem / Goal and
# Acceptance Criteria).  Re-implemented locally to avoid creating a hard
# coupling between Assembly and the Blueprint module.
# ---------------------------------------------------------------------------


def _normalize_heading(value: str) -> str:
    value = value.lower().strip().replace("&amp;", "&")
    value = re.sub(r"`", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_PROBLEM_HEADINGS = {"problem goal", "problem", "goal"}
_AC_HEADINGS = {"acceptance criteria"}


def _extract_section(body: str, headings: set[str]) -> str:
    """Return the raw text of the first heading whose normalized name matches."""
    if not body:
        return ""
    current: list[str] = []
    capturing = False
    current_level: int | None = None
    out: list[str] = []
    for line in body.replace("\r\n", "\n").splitlines():
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            heading = _normalize_heading(match.group(2))
            if heading in headings:
                if capturing:
                    # Codex round-3 P2: docstring contract is "first matching
                    # heading". Return immediately rather than concatenating
                    # multiple matching sections (e.g., quoted issue templates
                    # that repeat the heading).
                    out.extend(current)
                    return "\n".join(out).strip()
                current = []
                capturing = True
                current_level = level
                continue
            if capturing and current_level is not None and level <= current_level:
                # Sibling or higher heading closes the section
                out.extend(current)
                return "\n".join(out).strip()
        if capturing:
            current.append(line)
    if capturing:
        out.extend(current)
    return "\n".join(out).strip()


_BULLET_RE = re.compile(r"^\s*(?:[-*]\s+(?:\[[ xX]\]\s*)?|\d+\.\s+)(.+?)\s*$")


def _extract_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    for line in text.splitlines():
        match = _BULLET_RE.match(line)
        if match:
            item = match.group(1).strip()
            if item:
                bullets.append(item)
    return bullets


def _extract_task_summary(body: str, title: str) -> tuple[str, str]:
    """Return (summary, source) per D14."""
    section = _extract_section(body, _PROBLEM_HEADINGS)
    if section:
        # First non-empty line of the section, or the whole section if
        # there is no list / heading structure.
        first_line = next(
            (line.strip() for line in section.splitlines() if line.strip()),
            "",
        )
        if first_line:
            return first_line, "section"
    return (title or "").strip(), "title_fallback"


def _extract_acceptance_criteria(body: str, title: str) -> tuple[list[str], str]:
    """Return (criteria_list, source) per D14.

    Three distinct sources per CHG-1819 F4 / D7-D8:
      - ``"section"``: bullets were extracted from the ``## Acceptance Criteria``
        section in the issue body.
      - ``"title_fallback"``: section absent or empty, but the issue title is
        non-empty — title is used as a single criterion.
      - ``"empty_fallback"``: section absent AND title is empty — returns ``[]``
        rather than ``[""]`` to avoid rendering a blank bullet in the
        Assembly progress comment.
    """
    section = _extract_section(body, _AC_HEADINGS)
    bullets = _extract_bullets(section)
    if bullets:
        return bullets, "section"
    title_clean = (title or "").strip()
    if title_clean:
        return [title_clean], "title_fallback"
    return [], "empty_fallback"


def build_job_contract(
    *,
    issue: dict[str, Any],
    repository: str,
    branch_name: str,
    delivery_id: str,
    base_branch: str = "main",
) -> AssemblyJobContract:
    """Build an :class:`AssemblyJobContract` from the live issue payload."""
    issue_title = str(issue.get("title") or "").strip()
    issue_body = str(issue.get("body") or "")
    task_summary, task_source = _extract_task_summary(issue_body, issue_title)
    criteria, ac_source = _extract_acceptance_criteria(issue_body, issue_title)
    return AssemblyJobContract(
        repository=repository,
        issue_number=int(issue.get("number") or 0),
        issue_url=str(issue.get("html_url") or ""),
        issue_title=issue_title,
        issue_body=issue_body,
        branch_name=branch_name,
        base_branch=base_branch,
        task_summary=task_summary,
        acceptance_criteria=criteria,
        forbidden_operations=FORBIDDEN_OPERATIONS,
        verification_commands=VERIFICATION_COMMANDS,
        delivery_id=delivery_id,
        requested_at=datetime.now(UTC).isoformat(),
        acceptance_criteria_source=ac_source,
        task_summary_source=task_source,
    )
