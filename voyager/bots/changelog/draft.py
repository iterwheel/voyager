"""Pure changelog drafting helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .constants import CHANGELOG_RELEVANT_LABELS, CHANGELOG_SKIP_LABELS

_CHANGELOG_HEADING_RE = re.compile(r"^## \[(?P<name>[^\]]+)\]")
_CHANGELOG_SUBSECTION_RE = re.compile(r"^###\s+")
_BULLET_REFERENCE_RE = re.compile(r"\[#(?P<number>\d+)\]\([^)]+\)")
_BULLET_REFERENCE_SUFFIX_RE = re.compile(r"\s+\(\[[^\]]+\]\([^)]+\)\)\.?$")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ChangelogAppendResult:
    """Result from appending a source PR bullet to ``[Unreleased]``."""

    text: str
    changed: bool
    reason: str | None = None


def label_names(labels: list[Any] | tuple[Any, ...] | None) -> list[str]:
    """Return GitHub label names from either webhook dicts or plain strings."""
    names: list[str] = []
    for label in labels or []:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def _normalized_label_set(labels: list[Any] | tuple[Any, ...] | None) -> set[str]:
    return {name.strip().lower() for name in label_names(labels) if name.strip()}


def is_changelog_relevant(labels: list[Any] | tuple[Any, ...] | None) -> bool:
    """Return True when labels say the merged PR should get a changelog entry."""
    normalized = _normalized_label_set(labels)
    if normalized & CHANGELOG_SKIP_LABELS:
        return False
    return bool(normalized & CHANGELOG_RELEVANT_LABELS)


def build_changelog_bullet(*, pr_number: int, pr_title: str, pr_url: str) -> str:
    """Build the single ``[Unreleased]`` bullet for a merged PR."""
    title = _SPACE_RE.sub(" ", pr_title).strip()
    title = title.rstrip(".")
    return f"- {title} ([#{pr_number}]({pr_url}))."


def _normalized_bullet_summary(line: str) -> str:
    summary = line.strip()
    if summary.startswith("- "):
        summary = summary[2:]
    summary = _BULLET_REFERENCE_SUFFIX_RE.sub("", summary)
    summary = summary.rstrip(".")
    return _SPACE_RE.sub(" ", summary).strip().casefold()


def _references_source_number(line: str, source_pr_number: int) -> bool:
    return any(
        int(match.group("number")) == source_pr_number
        for match in _BULLET_REFERENCE_RE.finditer(line)
    )


def _has_source_entry(section_text: str, source_pr_number: int, bullet: str) -> bool:
    number = re.escape(str(source_pr_number))
    reference_re = re.compile(rf"/pull/{number}(?!\d)")
    if reference_re.search(section_text):
        return True

    source_summary = _normalized_bullet_summary(bullet)
    if not source_summary:
        return False
    for line in section_text.splitlines():
        if (
            line.lstrip().startswith("- ")
            and _references_source_number(line, source_pr_number)
            and _normalized_bullet_summary(line) == source_summary
        ):
            return True
    return False


def append_unreleased_bullet(
    changelog_text: str,
    *,
    bullet: str,
    source_pr_number: int,
) -> ChangelogAppendResult:
    """Append *bullet* to the ``## [Unreleased]`` section if not already present."""
    lines = changelog_text.splitlines()
    header_idx: int | None = None
    for idx, line in enumerate(lines):
        heading = _CHANGELOG_HEADING_RE.match(line.strip())
        if heading and heading.group("name").strip().lower() == "unreleased":
            header_idx = idx
            break

    if header_idx is None:
        return ChangelogAppendResult(changelog_text, changed=False, reason="missing_unreleased")

    section_end_idx = len(lines)
    for idx in range(header_idx + 1, len(lines)):
        if _CHANGELOG_HEADING_RE.match(lines[idx].strip()):
            section_end_idx = idx
            break

    section_text = "\n".join(lines[header_idx + 1 : section_end_idx])
    if _has_source_entry(section_text, source_pr_number, bullet):
        return ChangelogAppendResult(changelog_text, changed=False, reason="already_present")

    insert_boundary_idx = section_end_idx
    for idx in range(header_idx + 1, section_end_idx):
        if _CHANGELOG_SUBSECTION_RE.match(lines[idx].strip()):
            insert_boundary_idx = idx
            break

    insert_at = insert_boundary_idx
    while insert_at > header_idx + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1

    # Drop blank lines at the insertion boundary, then keep exactly one blank
    # line between the new bullet and the following subsection or release.
    del lines[insert_at:insert_boundary_idx]

    additions: list[str] = []
    if insert_at > header_idx + 1:
        additions.append("")
    additions.append(bullet)
    additions.append("")
    lines[insert_at:insert_at] = additions

    return ChangelogAppendResult("\n".join(lines) + "\n", changed=True)
