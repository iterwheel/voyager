"""Conservative acceptance-criteria exact-token spot checks.

This is intentionally not a semantic verifier. It only blocks when an issue
states exact machine-readable tokens and the changed text misses those tokens.
Uncertain prose remains non-blocking and falls through to normal review.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*[A-Za-z0-9]$")
_BOUNDARY_CHARS = r"A-Za-z0-9_.:-"
_HYPHEN_VALUE_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$")
_VALUES_HINT_RE = re.compile(
    r"\b(?:allowed\s+values|(?:three|3)\s+values)\b|\bvalues\s*:",
    re.I,
)
_SECTION_HEADING_RE = re.compile(r"^#{2,6}\s+")
_BULLET_LINE_RE = re.compile(r"^(\s*)(?:[-*]\s+(?:\[[ xX]\]\s*)?|\d+\.\s+)(.+?)\s*$")
_REMOVAL_STATUS_TOKENS = frozenset(
    {"deleted", "deprecated", "forbidden", "removed", "renamed", "replaced", "retired"}
)
_REMOVAL_PREFIX_RE = re.compile(
    r"\b(?:delete|deprecat(?:e|ed|es|ing)|disallow|do\s+not|don['\u2019]t|drop|"
    r"forbid|forbidden|must\s+not|no\s+longer|prohibit|remove|rename|"
    r"replace|retire|should\s+not)\b",
    re.I,
)
_REMOVAL_STATUS_PREFIX_RE = re.compile(
    r"\b(?:value|mode|token|instance|project|entry|item)\s+"
    r"(?:is|are|becomes?|should\s+be|must\s+be)\s*$",
    re.I,
)
_REPLACEMENT_SOURCE_PREFIX_RE = re.compile(
    r"\b(?:chang(?:e|ed|es|ing)|updat(?:e|ed|es|ing))\b",
    re.I,
)
_REPLACEMENT_SOURCE_SUFFIX_RE = re.compile(r"^\s*(?:as|to|with)\s+`[^`\n]+`", re.I)
_REQUIRED_TARGET_PREFIX_RE = re.compile(
    r"(?:\b(?:and|then|but)|[;,.])\s+"
    r"(?:add|create|emit|include|introduce|keep|register|require|set|support|use|write)\b|"
    r"\b(?:as|to|with)\s*$",
    re.I,
)
_REQUIRED_CRITERION_RE = re.compile(
    r"\b(?:add|chang(?:e|ed|es|ing)|create|describe|document|emit|ensure|include|"
    r"introduce|keep|register|require|set|support|updat(?:e|ed|es|ing)|use|"
    r"validat(?:e|ed|es|ing)|verify|write)\b",
    re.I,
)
_REMOVAL_SUFFIX_RE = re.compile(
    r"^\s*(?:[-\u2014:;,().]|\s)*(?:(?:the\s+)?(?:value|mode|token)\s+)?"
    r"(?:(?:that|which)\s+)?(?:(?:should|must)\s+be\s+"
    r"`?(?:deleted|forbidden|removed|renamed|replaced|retired)`?|"
    r"(?:is|are|becomes?)\s+"
    r"(?:`?(?:deleted|deprecated|forbidden|removed|retired)`?|"
    r"not\s+(?:accepted|allowed|supported))|"
    r"(?:is|are)\s+no\s+longer\s+(?:accepted|allowed|supported)|"
    r"no\s+longer\s+(?:accepted|allowed|supported))\b",
    re.I,
)


@dataclass(frozen=True)
class AcceptanceSpotCheckFinding:
    """One unmet exact-token group from the issue contract."""

    source: str
    criterion: str
    required_tokens: tuple[str, ...]
    missing_tokens: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AcceptanceSpotCheckResult:
    """Result of the conservative exact-token spot-check."""

    findings: tuple[AcceptanceSpotCheckFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return "Acceptance spot-check passed."
        first = self.findings[0]
        missing = ", ".join(first.missing_tokens)
        return f"Acceptance spot-check failed: missing exact token(s): {missing}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def check_acceptance_exact_tokens(
    *,
    issue_body: str,
    acceptance_criteria: list[str],
    changed_text: str,
) -> AcceptanceSpotCheckResult:
    """Return exact-token findings that should block Assembly publication.

    The check is deliberately narrow:
    - inline-code tokens in extracted acceptance criteria are required;
    - hyphenated inline-code tokens in "values" acceptance-criteria text are required as a
      group, catching enum/value-list substitutions like the Alfred #204
      ``mandatory-bind`` / ``inherit-only`` miss;
    - tokens with spaces, paths, angle-bracket placeholders, or prose-only
      examples are ignored.
    """
    findings: list[AcceptanceSpotCheckFinding] = []
    for source, criterion, tokens in _required_token_groups(issue_body, acceptance_criteria):
        missing = tuple(token for token in tokens if not _token_present(token, changed_text))
        if missing:
            findings.append(
                AcceptanceSpotCheckFinding(
                    source=source,
                    criterion=criterion,
                    required_tokens=tokens,
                    missing_tokens=missing,
                )
            )
    return AcceptanceSpotCheckResult(tuple(findings))


def _required_token_groups(
    issue_body: str,
    acceptance_criteria: list[str],
) -> list[tuple[str, str, tuple[str, ...]]]:
    groups: list[tuple[str, str, tuple[str, ...]]] = []
    seen: set[tuple[str, ...]] = set()
    removal_contexts = _removal_contexts_for_criteria(issue_body, acceptance_criteria)

    for criterion, removal_context in zip(acceptance_criteria, removal_contexts, strict=False):
        contextual_criterion = criterion
        if removal_context is not None:
            contextual_criterion = f"{removal_context} {criterion}"

        tokens = _unique_tokens(_required_inline_tokens(contextual_criterion))
        _append_required_token_group(groups, seen, "acceptance_criterion", criterion, tokens)

    ac_section = _acceptance_section(issue_body)
    for criterion, tokens in _value_groups(ac_section):
        _append_required_token_group(groups, seen, "issue_value_group", criterion, tokens)

    return groups


def _removal_contexts_for_criteria(
    issue_body: str,
    acceptance_criteria: list[str],
) -> list[str | None]:
    bullet_contexts = _removal_contexts_by_bullet(_acceptance_section(issue_body))
    contexts: list[str | None] = []
    bullet_idx = 0
    for criterion in acceptance_criteria:
        context = None
        while bullet_idx < len(bullet_contexts):
            text, candidate_context = bullet_contexts[bullet_idx]
            bullet_idx += 1
            if text == criterion:
                context = candidate_context
                break
        contexts.append(context)
    return contexts


def _removal_contexts_by_bullet(ac_section: str) -> list[tuple[str, str | None]]:
    contexts: list[tuple[str, str | None]] = []
    stack: list[tuple[int, str, bool]] = []
    for line in (ac_section or "").splitlines():
        match = _BULLET_LINE_RE.match(line)
        if match is None:
            continue
        indent = len(match.group(1).replace("\t", "    "))
        criterion = match.group(2).strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        removal_parent = next((text for _, text, is_removal in reversed(stack) if is_removal), None)
        context = (
            removal_parent
            if removal_parent is not None and _inherits_removal_list_context(criterion)
            else None
        )
        contexts.append((criterion, context))
        stack.append((indent, criterion, _starts_removal_list_context(criterion)))
    return contexts


def _starts_removal_list_context(criterion: str) -> bool:
    text = (criterion or "").strip()
    if not text or _REMOVAL_PREFIX_RE.search(text) is None:
        return False
    return text.endswith(":") or _INLINE_CODE_RE.search(text) is None


def _inherits_removal_list_context(criterion: str) -> bool:
    text = criterion or ""
    return bool(_INLINE_CODE_RE.search(text)) and _REQUIRED_CRITERION_RE.search(text) is None


def _append_required_token_group(
    groups: list[tuple[str, str, tuple[str, ...]]],
    seen: set[tuple[str, ...]],
    source: str,
    criterion: str,
    tokens: tuple[str, ...],
) -> None:
    if not tokens or tokens in seen:
        return
    seen.add(tokens)
    groups.append((source, criterion, tokens))


def _value_groups(issue_body: str) -> list[tuple[str, tuple[str, ...]]]:
    lines = (issue_body or "").replace("\r\n", "\n").splitlines()
    groups: list[tuple[str, tuple[str, ...]]] = []
    for idx, line in enumerate(lines):
        if not _VALUES_HINT_RE.search(line):
            continue
        window: list[str] = [line]
        for follow in lines[idx + 1 : idx + 10]:
            if _SECTION_HEADING_RE.match(follow):
                break
            if not follow.strip() and len(window) > 1:
                break
            window.append(follow)
        tokens = tuple(
            token
            for token in _unique_tokens(
                [token for value_line in window for token in _required_inline_tokens(value_line)]
            )
            if _HYPHEN_VALUE_RE.match(token)
        )
        if len(tokens) >= 2:
            groups.append((line.strip(), tokens))
    return groups


def _acceptance_section(issue_body: str) -> str:
    lines = (issue_body or "").replace("\r\n", "\n").splitlines()
    capturing = False
    level = 0
    out: list[str] = []
    for line in lines:
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if match:
            heading_level = len(match.group(1))
            heading = re.sub(r"[^a-z0-9]+", " ", match.group(2).lower()).strip()
            if heading == "acceptance criteria":
                capturing = True
                level = heading_level
                continue
            if capturing and heading_level <= level:
                break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _required_inline_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _INLINE_CODE_RE.finditer(text or ""):
        token = _normalize_token(match.group(1))
        if token and not _token_marked_for_removal(text, match.start(), match.end(), token):
            tokens.append(token)
    return tokens


def _token_marked_for_removal(text: str, start: int, end: int, token: str) -> bool:
    before = text[:start]
    after = text[end:]
    if token.lower() in _REMOVAL_STATUS_TOKENS and _REMOVAL_STATUS_PREFIX_RE.search(before):
        return True
    if _REPLACEMENT_SOURCE_PREFIX_RE.search(before) and _REPLACEMENT_SOURCE_SUFFIX_RE.search(after):
        return True
    scoped_before = _removal_prefix_scope(before)
    return (
        _REMOVAL_PREFIX_RE.search(scoped_before) is not None
        or _REMOVAL_SUFFIX_RE.search(after) is not None
    )


def _removal_prefix_scope(before: str) -> str:
    last_match = None
    for candidate in _REQUIRED_TARGET_PREFIX_RE.finditer(before):
        last_match = candidate
    if last_match is None:
        return before
    return before[last_match.start() :]


def _token_present(token: str, text: str) -> bool:
    field_suffix = ":?" if ":" not in token else ""
    pattern = rf"(?<![{_BOUNDARY_CHARS}]){re.escape(token)}{field_suffix}(?![{_BOUNDARY_CHARS}])"
    return re.search(pattern, text or "") is not None


def _normalize_token(raw: str) -> str | None:
    token = raw.strip().replace("*", "").strip()
    if token.endswith(":"):
        token = token[:-1].strip()
    if not token or len(token) < 2:
        return None
    if any(char.isspace() for char in token):
        return None
    if any(char in token for char in "/\\<>#"):
        return None
    if token.lower() in {"true", "false", "none", "null", "yes", "no"}:
        return None
    if not _TOKEN_RE.fullmatch(token):
        return None
    return token


def _unique_tokens(tokens: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)
