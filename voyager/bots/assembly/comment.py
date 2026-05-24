"""Assembly bot — progress comment renderer.

Per VOY-1817 Surface 8.  Renders the body for the upserted ``assembly``
progress comment with a stable marker so subsequent invocations on the
same issue / PR replace rather than duplicate.
"""

from __future__ import annotations

from typing import Any

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

    criteria = contract.get("acceptance_criteria") or []
    if criteria:
        lines.append("")
        lines.append("**Acceptance criteria captured:**")
        lines.extend(f"- {item}" for item in criteria)

    lines.extend(_format_failures(writeback_failures))
    lines.append("")
    lines.append("Assembly never merges, approves, or resolves review threads.")
    return "\n".join(lines).strip()
