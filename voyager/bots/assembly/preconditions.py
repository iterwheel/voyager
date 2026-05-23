"""Assembly bot — precondition gates.

Per VOY-1817 Surface 4 and D4: preconditions are checked at routing time
and re-checked at writeback-dispatcher time.  The same helper is reused on
both sides so the rules cannot drift.

The allow-list / repository gate is *not* checked here — the bridge's
``_repository_allowed_for_agent`` filter runs first in
``voyager/server.py`` before any route is dispatched, so this module
focuses on issue-shape gates.  The repository gate is enforced upstream
per D13.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import (
    BLUEPRINT_READY_LABEL,
    REFUSAL_ISSUE_CLOSED,
    REFUSAL_MISSING_STACK_TYPE,
    REFUSAL_NOT_BLUEPRINT_READY,
    REFUSAL_PR_NOT_ISSUE,
    STACK_TYPE_LABEL_PREFIX,
)


@dataclass(frozen=True)
class PreconditionResult:
    """Outcome of the Assembly precondition checks."""

    ok: bool
    reason: str | None = None
    missing_labels: list[str] = field(default_factory=list)

    def as_refusal_dict(self) -> dict[str, Any] | None:
        """Return the refusal payload shape for the writeback contract, or None on success."""
        if self.ok:
            return None
        return {
            "reason": self.reason,
            "missing_labels": list(self.missing_labels),
            "outside_allow_list": False,
        }


def _issue_labels(issue: dict[str, Any]) -> list[str]:
    raw = issue.get("labels") or []
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def validate_preconditions(
    issue: dict[str, Any] | None,
    *,
    allow_missing_stack: bool = False,
) -> PreconditionResult:
    """Validate the live issue meets Assembly's preconditions.

    Rules
    -----
    * The payload must be a real issue, not a PR (``issue.pull_request``
      must be absent — GitHub sets that key on PR-shaped issue payloads).
    * The issue must carry the ``blueprint-ready`` label.
    * The issue must carry at least one ``stack-type-*`` label, unless the
      caller passed ``allow_missing_stack=True`` (the ``--allow-missing-stack``
      flag).
    * Closed issues are refused — Assembly should not write code against a
      closed plan.
    """
    if not issue:
        return PreconditionResult(
            ok=False,
            reason=REFUSAL_PR_NOT_ISSUE,
            missing_labels=[],
        )

    if issue.get("pull_request"):
        return PreconditionResult(
            ok=False,
            reason=REFUSAL_PR_NOT_ISSUE,
            missing_labels=[],
        )

    state = (issue.get("state") or "").lower()
    if state == "closed":
        return PreconditionResult(
            ok=False,
            reason=REFUSAL_ISSUE_CLOSED,
            missing_labels=[],
        )

    labels = _issue_labels(issue)
    missing: list[str] = []
    if BLUEPRINT_READY_LABEL not in labels:
        missing.append(BLUEPRINT_READY_LABEL)

    has_stack_type = any(label.startswith(STACK_TYPE_LABEL_PREFIX) for label in labels)
    if not has_stack_type and not allow_missing_stack:
        missing.append(f"{STACK_TYPE_LABEL_PREFIX}*")

    if missing:
        # Surface the first failing label class as the reason; downstream
        # writeback inspects ``missing_labels`` for the full list.
        if BLUEPRINT_READY_LABEL in missing:
            reason = REFUSAL_NOT_BLUEPRINT_READY
        else:
            reason = REFUSAL_MISSING_STACK_TYPE
        return PreconditionResult(ok=False, reason=reason, missing_labels=missing)

    return PreconditionResult(ok=True)
