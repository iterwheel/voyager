"""Assembly bot — progress comment renderer.

Per VOY-1817 Surface 8.  Renders the body for the upserted ``assembly``
progress comment with a stable marker so subsequent invocations on the
same issue / PR replace rather than duplicate.
"""

from __future__ import annotations

import re
from typing import Any

from voyager.core.redaction import sanitize_public_text

from .audit import lookup_hint
from .constants import ASSEMBLY_COMMENT_MARKER


def _format_failures(failures: list[dict[str, Any]] | None) -> list[str]:
    if not failures:
        return []
    lines = ["", "**Writeback failures:**"]
    for fail in failures:
        op = fail.get("operation", "unknown")
        cls = fail.get("error_class", "unknown")
        status = fail.get("status")
        status_part = f", HTTP {status}" if status is not None else ""
        suggested = fail.get("suggested_action", "")
        lines.append(f"- `{op}` failed ({cls}{status_part}). {suggested}")
    return lines


def _code_span(value: str) -> str:
    """Return a Markdown code span that cannot be broken by embedded backticks."""
    text = str(value)
    if "`" not in text:
        return f"`{text}`"
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    delimiter = "`" * (longest_run + 1)
    return f"{delimiter} {text} {delimiter}"


def _format_backend_failure(adapter_result: dict[str, Any]) -> list[str]:
    details = adapter_result.get("details")
    if not isinstance(details, dict):
        return []
    failure = details.get("failure_diagnostic")
    if not isinstance(failure, dict) or not failure:
        return []

    phase = sanitize_public_text(failure.get("phase") or "unknown", limit=80)
    category = sanitize_public_text(failure.get("command_category") or "unknown", limit=80)
    command = sanitize_public_text(failure.get("command") or "", limit=120)
    exit_code = failure.get("exit_code")
    timed_out = bool(failure.get("timed_out"))
    stderr_tail = sanitize_public_text(failure.get("stderr_tail") or "", limit=260)
    stdout_tail = sanitize_public_text(failure.get("stdout_tail") or "", limit=180)
    lines = [
        "",
        "**Backend failure diagnostics:**",
        f"- Phase: {_code_span(phase)}",
        f"- Command: {_code_span(category)}",
    ]
    if command:
        lines.append(f"- Check: {_code_span(command)}")
    if exit_code is not None:
        lines.append(f"- Exit code: {_code_span(str(exit_code))}")
    if timed_out:
        lines.append(f"- Timeout: {_code_span('true')}")
    if stderr_tail:
        lines.append(f"- Stderr tail: {_code_span(stderr_tail)}")
    elif stdout_tail:
        lines.append(f"- Stdout tail: {_code_span(stdout_tail)}")
    if details.get("failure_debug_bundle_path"):
        patch_left = details.get("patch_left_behind")
        if isinstance(patch_left, bool):
            lines.append(f"- Patch left behind: {_code_span(str(patch_left).lower())}")
        else:
            lines.append("- Debug bundle retained: `true`")
        lines.append("- Debug bundle: recorded in the private audit manifest.")
    return lines


def _format_refusal(refusal: dict[str, Any]) -> str:
    reason = refusal.get("reason", "unknown")

    # VOY-1818 Surface 5: unauthorized_actor has its own body shape.
    # D12: MUST NOT echo the allow-list contents or trusted-association set.
    if reason == "unauthorized_actor":
        actor_login = refusal.get("actor_login") or "unknown"
        actor_association = refusal.get("actor_association") or "none"
        return "\n".join(
            [
                ASSEMBLY_COMMENT_MARKER,
                "**Assembly refused this invocation.**",
                "",
                f"Reason: `{reason}`",
                "",
                f"Actor: `{actor_login}` (association: `{actor_association}`)",
                "",
                "Assembly only writes code when the triggering actor is authorized per",
                "VOY-1805 §Actor Authorization for Assembly. See VOY-1818 for the gate",
                "policy and how to add an actor to the allow-list.",
            ]
        ).strip()

    missing = refusal.get("missing_labels") or []
    outside = refusal.get("outside_allow_list")
    lines = [
        ASSEMBLY_COMMENT_MARKER,
        "**Assembly refused this invocation.**",
        "",
        f"Reason: `{reason}`",
    ]
    if missing:
        lines.append("")
        lines.append("Missing labels:")
        lines.extend(f"- `{label}`" for label in missing)
    if outside:
        lines.append("")
        lines.append(
            "Repository is not in the bridge allow-list "
            "(`BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY`)."
        )
    lines.extend(
        [
            "",
            "Assembly only writes code on issues that satisfy the VOY-1805 §5 ",
            "preconditions.  See `rules/VOY-1805` for the boundary table.",
        ]
    )
    return "\n".join(lines).strip()


def build_assembly_comment(
    *,
    status: str,
    contract: dict[str, Any] | None = None,
    adapter_result: dict[str, Any] | None = None,
    refusal: dict[str, Any] | None = None,
    branch: dict[str, Any] | None = None,
    pull_request: dict[str, Any] | None = None,
    writeback_failures: list[dict[str, Any]] | None = None,
    audit_id: str | None = None,
    session: dict[str, Any] | None = None,
    dry_run: bool = False,
    surface: str = "issue",
    phase_mode: str | None = None,
    testpilot_result: dict[str, Any] | None = None,
) -> str:
    """Return the body of the upserted Assembly progress comment.

    Parameters
    ----------
    status:
        High-level status string surfaced as the comment heading.
    surface:
        ``"issue"`` or ``"pr"``.  The two surfaces share the marker but
        carry slightly different per-surface text per VOY-1817 Open
        Question 2.
    phase_mode:
        ``"single"`` (default) or ``"two-phase"``. When ``"two-phase"``,
        a compact phase-status section is rendered.
    testpilot_result:
        Optional adapter-result dict for the testpilot phase. Rendered
        only when ``phase_mode`` is ``"two-phase"``.
    """
    if refusal:
        return _format_refusal(refusal)

    contract = contract or {}
    adapter_result = adapter_result or {}
    branch = branch or {}
    pull_request = pull_request or {}
    session = session or {}

    heading = "Assembly progress" if surface == "pr" else "Assembly acknowledgement"

    lines: list[str] = [
        ASSEMBLY_COMMENT_MARKER,
        f"**{heading} — status: `{status}`**",
        "",
        f"- Branch: `{branch.get('name') or 'pending'}`",
    ]
    pr_number = pull_request.get("number")
    if pr_number:
        action = pull_request.get("action") or "opened"
        lines.append(f"- Pull request: #{pr_number} ({action})")
    else:
        pr_action = pull_request.get("action") or "pending"
        lines.append(f"- Pull request: {pr_action}")

    backend = (adapter_result.get("status") or "unknown").lower()
    summary = adapter_result.get("summary") or ""
    lines.append(f"- Adapter: `{backend}`")
    if summary:
        lines.append(f"  > {summary}")

    # Phase status section (two-phase mode)
    if phase_mode == "two-phase":
        lines.append("")
        lines.append("**Phase status:**")
        tp = testpilot_result or {}
        tp_status = (tp.get("status") or "pending").lower()
        tp_summary = tp.get("summary") or ""
        imp_status = (adapter_result.get("status") or "unknown").lower()
        if imp_status == "executed":
            lines.append("- 🔧 Implementer: completed")
        elif imp_status == "no_changes":
            lines.append("- ⚪ Implementer: no changes needed")
        elif imp_status in ("failed", "dry_run"):
            lines.append(f"- 🔴 Implementer: `{imp_status}`")
        else:
            lines.append(f"- ⚪ Implementer: `{imp_status}`")

        if tp_status == "blocked":
            lines.append("- 🔴 TestPilot: blocked — gaps found")
        elif tp_status == "executed":
            lines.append("- ✅ TestPilot: passed")
        elif tp_status == "no_changes":
            lines.append("- ✅ TestPilot: reviewed (no issues found)")
        elif tp_status == "failed":
            lines.append(f"- 🔴 TestPilot: `{tp_status}`")
        elif tp_status == "pending":
            lines.append("- ⚪ TestPilot: pending")
        else:
            lines.append(f"- ⚪ TestPilot: `{tp_status}`")
        if tp_summary:
            lines.append(f"  > {tp_summary}")

    session_mode = session.get("mode")
    if session_mode:
        lines.append(f"- Session: `{session_mode}`")
        fallback_reason = session.get("fallback_reason")
        if fallback_reason:
            lines.append(f"  > {fallback_reason}")

    if dry_run:
        lines.append("- Dry-run mode: no GitHub mutations performed.")

    issue_number = contract.get("issue_number")
    if issue_number:
        lines.append(f"- Issue: #{issue_number}")

    repository = contract.get("repository") or ""
    if audit_id and repository and issue_number:
        lines.append(f"- {lookup_hint(audit_id, str(repository), int(issue_number))}")

    lines.extend(_format_backend_failure(adapter_result))

    criteria = contract.get("acceptance_criteria") or []
    if criteria:
        lines.append("")
        lines.append("**Acceptance criteria captured:**")
        lines.extend(f"- {item}" for item in criteria)

    lines.extend(_format_failures(writeback_failures))
    lines.append("")
    lines.append("Assembly never merges, approves, or resolves review threads.")
    return "\n".join(lines).strip()
