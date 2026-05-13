"""Extract severity and finding-kind from Codex review thread comments.

Pure extraction logic (no I/O, no state mutation). Maps raw GitHub comment
bodies to Severity enum members and optional finding-kind strings.
"""

from __future__ import annotations

from voyager.bots.clearance.models import Severity


def extract_severity_and_kind(
    comments: list[dict] | None,
) -> tuple[Severity, str | None]:
    """Extract (codex_severity, finding_kind) from Codex review thread comments.

    Severity rule: scan first comment body for any of these markers:
      "![P1 Badge]", "![P2 Badge]", "![P3 Badge]"
      "|P1|", "|P2|", "|P3|"
      "**P1**", "**P2**", "**P3**"
    Returns Severity.P1/P2/P3 on first match; Severity.P3 if no match.

    Finding-kind rule (case-insensitive): if body contains ALL of "required",
    ("check" OR "status"), and "paths-ignore" → "required_check_coupling".
    Otherwise None.

    Empty comments / None comments → (Severity.P3, None).
    """
    if not comments:
        return Severity.P3, None

    body = comments[0].get("body") or ""

    severity = _extract_severity(body)
    kind = _extract_finding_kind(body)

    return severity, kind


def _extract_severity(body: str) -> Severity:
    """Extract severity from badge markers in comment body.

    Checks for literal case markers:
      "![P1 Badge]", "![P2 Badge]", "![P3 Badge]"
      "|P1|", "|P2|", "|P3|"
      "**P1**", "**P2**", "**P3**"

    Returns Severity.P1/P2/P3 on first match; defaults to Severity.P3.
    """
    for sev in ("P1", "P2", "P3"):
        if f"![{sev} Badge]" in body or f"|{sev}|" in body or f"**{sev}**" in body:
            return Severity(sev)
    return Severity.P3


def _extract_finding_kind(body: str) -> str | None:
    """Extract finding_kind from comment body.

    Returns "required_check_coupling" if body contains ALL of:
      - "required" (case-insensitive)
      - "check" OR "status" (case-insensitive)
      - "paths-ignore" (case-insensitive)

    Otherwise returns None.
    """
    body_lower = body.lower()
    if (
        "required" in body_lower
        and ("check" in body_lower or "status" in body_lower)
        and "paths-ignore" in body_lower
    ):
        return "required_check_coupling"
    return None
