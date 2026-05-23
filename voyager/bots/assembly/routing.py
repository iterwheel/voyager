"""Assembly bot — webhook routing.

Per VOY-1817 Surface 9 and D2: Assembly listens on ``issue_comment.created``
only (no ``issues.*``).  The route shape carries a ``dynamic`` marker
(``assembly_implementation``) so the writeback dispatcher knows to use
the new code path rather than the generic ``apply_route_writeback``.
"""

from __future__ import annotations

from typing import Any

from .branch import make_branch_name
from .commands import AssemblyCommand, parse_assembly_command
from .constants import (
    ASSEMBLY_AGENT_ID,
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_COMMENT_MARKER,
)
from .job_contract import build_job_contract
from .preconditions import validate_preconditions


def should_run_assembly(event: str, payload: dict[str, Any]) -> bool:
    """Return True when the webhook event is an Assembly command comment."""
    if event != "issue_comment":
        return False
    if (payload.get("action") or "") != "created":
        return False
    body = str((payload.get("comment") or {}).get("body") or "")
    return parse_assembly_command(body) is not None


def _command_or_none(payload: dict[str, Any]) -> AssemblyCommand | None:
    body = str((payload.get("comment") or {}).get("body") or "")
    return parse_assembly_command(body)


def route_assembly_event(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the Assembly route list for an incoming webhook payload."""
    if not should_run_assembly(event, payload):
        return []

    command = _command_or_none(payload)
    if command is None:  # defensive — should_run already returned True
        return []

    issue = dict(payload.get("issue") or {})
    repository_name: str = ((payload.get("repository") or {}).get("full_name")) or ""

    # D4: validate preconditions at routing time.  The dispatcher will
    # re-validate against the live issue before writing anything.
    pre = validate_preconditions(issue, allow_missing_stack=command.allow_missing_stack)

    issue_label_names: list[str] = []
    for label in issue.get("labels") or []:
        if isinstance(label, str):
            issue_label_names.append(label)
        elif isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                issue_label_names.append(name)

    validation: dict[str, Any] = {
        "status": "assembly_ready" if pre.ok else "assembly_refused",
        "conclusion": "success" if pre.ok else "neutral",
        "issue_number": issue.get("number"),
        "issue_url": issue.get("html_url"),
        "issue_labels": issue_label_names,
        "issue_state": issue.get("state"),
        "command": command.command,
        "command_flags": {
            "dry_run": command.dry_run,
            "allow_missing_stack": command.allow_missing_stack,
        },
    }
    if not pre.ok:
        validation["refusal"] = pre.as_refusal_dict()

    contract_dict: dict[str, Any] | None = None
    branch_name: str | None = None
    if pre.ok:
        branch_name = make_branch_name(int(issue.get("number") or 0), issue.get("title"))
        contract_dict = build_job_contract(
            issue=issue,
            repository=repository_name,
            branch_name=branch_name,
            delivery_id="",  # filled in by the server before dispatch
        ).to_dict()

    writeback: dict[str, Any] = {
        "dynamic": "assembly_implementation",
        "command": command.command,
        "command_flags": validation["command_flags"],
        "contract": contract_dict,
        "branch_name": branch_name,
        "refusal": pre.as_refusal_dict(),
        "comment_marker": ASSEMBLY_COMMENT_MARKER,
        "issue_labels": issue_label_names,
        "issue_state": issue.get("state"),
    }

    return [
        {
            "agent": ASSEMBLY_AGENT_SLUG,
            "agent_id": ASSEMBLY_AGENT_ID,
            "kind": "assembly_implementation",
            "event": event,
            "action": payload.get("action"),
            "validation": validation,
            "writeback": writeback,
        }
    ]
