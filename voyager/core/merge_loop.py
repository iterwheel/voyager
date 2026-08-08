"""Countdown merge-loop: autonomously rebase-merge fully-green agent PRs.

Spec: rules/VOY-1839-PRP-Countdown-Merge-Loop-Autonomous-Agent-PR-Merge.md.
Mirrors the resolve-loop skeleton (countdown_loop.py); single mutation type:
mergePullRequest (REBASE, expectedHeadOid-guarded). Fail-closed throughout.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voyager.core.resolve_conversation import ResolveConversationError

AGENT_PR_AUTHORS = frozenset({"ryosaeba1985"})
CLEARANCE_BOT_LOGINS = frozenset({"iterwheel-clearance", "iterwheel-clearance[bot]"})
REQUIRED_READINESS_STAGE = 3

MERGE_ALLOWED_REPOS = frozenset({"iterwheel/voyager-sandbox"})
_RAW_IDENTIFIER_REPOS = frozenset({"iterwheel/voyager-sandbox"})
_EXTRA_REPOS_ENV = "VOYAGER_MERGE_EXTRA_REPOS"
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")

DEFAULT_MERGE_LOCK_PATH = Path.home() / ".voyager" / "merge-loop.lock"
DEFAULT_MERGE_AUDIT_PATH = Path.home() / ".voyager" / "merge-loop.audit.jsonl"


def merge_allowed_repos() -> frozenset[str]:
    """Effective merge ceiling: built-in sandbox plus operator-local extras.

    FAIL-CLOSED parse, same contract as resolve_allowed_repos(): a malformed
    entry raises instead of being skipped.
    """
    raw = os.environ.get(_EXTRA_REPOS_ENV, "")
    extras: set[str] = set()
    for idx, token in enumerate(raw.replace(",", " ").split(), start=1):
        if not _REPO_PATTERN.match(token):
            raise ResolveConversationError(
                f"{_EXTRA_REPOS_ENV} entry #{idx} is not a valid owner/repo path"
            )
        extras.add(token.lower())
    return MERGE_ALLOWED_REPOS | frozenset(extras)


@dataclass(frozen=True)
class PrSnapshot:
    """One open PR's merge-relevant state, read in a single scan pass."""

    pr_id: str
    number: int
    author: str
    is_draft: bool
    head_oid: str
    checks_state: str | None  # statusCheckRollup.state; None = missing/unreadable
    unresolved_threads: int | None  # None = thread read failed (fail closed)
    readiness_stage: int | None  # parsed clearance readiness stage
    readiness_head: str | None  # head SHA the readiness comment was computed for


@dataclass(frozen=True)
class MergeDecision:
    repo: str
    pr: int
    action: str  # merged | would_merge | skipped | merge_failed
    reason: str = ""

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"repo": self.repo, "action": self.action}
        if self.repo in _RAW_IDENTIFIER_REPOS:
            out["pr"] = self.pr
            out["reason"] = self.reason
        else:
            out["redacted"] = True
        return out


@dataclass(frozen=True)
class MergeLoopSummary:
    repos_scanned: tuple[str, ...]
    repos_skipped: tuple[str, ...]
    prs_scanned: int
    decisions: tuple[MergeDecision, ...]
    capped: bool
    dry_run: bool
    errors: tuple[tuple[str, str], ...] = ()  # (public target, message)

    @property
    def merged(self) -> int:
        return sum(1 for d in self.decisions if d.action == "merged")

    @property
    def would_merge(self) -> int:
        return sum(1 for d in self.decisions if d.action == "would_merge")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "repos_scanned": list(self.repos_scanned),
            "repos_skipped": list(self.repos_skipped),
            "prs_scanned": self.prs_scanned,
            "merged": self.merged,
            "would_merge": self.would_merge,
            "decision_count": len(self.decisions),
            "capped": self.capped,
            "dry_run": self.dry_run,
            "errors": [{"target": t, "message": m} for t, m in self.errors],
            "decisions": [d.public() for d in self.decisions],
        }
