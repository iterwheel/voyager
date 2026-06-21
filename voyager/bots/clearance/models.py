"""Pydantic models for Clearance watchdog state.

Mirrors the JSON shapes from sweeping-monk SWM-1101 (thread verdict shape).
Validation here is the single source of truth for both JSONL writes and reads.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from voyager.bots.clearance.classify import CodexBodySignal, ThreadState


class Status(StrEnum):
    READY = "ready"
    READY_WITH_LOW_PRIORITY = "ready_with_low_priority"
    BLOCKED = "blocked"
    PENDING = "pending"
    ERROR = "error"
    SKIPPED = "skipped"


class Verdict(StrEnum):
    RESOLVED = "RESOLVED"
    OPEN = "OPEN"
    NEEDS_HUMAN_JUDGMENT = "NEEDS_HUMAN_JUDGMENT"


class Severity(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class CIConclusion(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING = "PENDING"
    SKIPPED = "SKIPPED"
    NEUTRAL = "NEUTRAL"
    CANCELLED = "CANCELLED"


class Stage15Mutation(StrEnum):
    RESOLVE_REVIEW_THREAD = "resolveReviewThread"
    UNRESOLVE_REVIEW_THREAD = "unresolveReviewThread"


class Stage15Action(BaseModel):
    """Record of a Stage 1.5 GraphQL mutation the watchdog performed."""

    mutation: Stage15Mutation
    threadId: str  # noqa: N815 — mirrors GitHub GraphQL field name
    result: dict


class Thread(BaseModel):
    """One Codex review thread on a PR."""

    model_config = ConfigDict(extra="allow")

    id: str
    comment_id: int
    path: str
    line: int | None = None
    codex_severity: Severity
    effective_severity: Severity
    verdict: Verdict
    title: str | None = None
    verdict_reason: str | None = None
    github_isResolved: bool = False  # noqa: N815 — mirrors GitHub GraphQL field name
    author_reply_id: int | None = None
    author_reply_substantive: bool | None = None
    code_changed: bool | None = None
    new_commit_sha: str | None = None
    demotion_reason: str | None = None
    github_resolvedBy: str | None = None  # noqa: N815 — mirrors GitHub GraphQL field name
    stage15_synced_at: datetime | None = None
    llm_verdict: str | None = None
    llm_model: str | None = None
    llm_confidence: float | None = None
    llm_reason: str | None = None
    clean_codex_review_id: int | None = None
    clean_codex_signal_source: str | None = None
    existing_head_verdict_marker: bool = False
    existing_close_reason_marker: bool = False
    existing_manual_close_marker: bool = False
    existing_thread_conclusion_marker: bool = False
    known_limitation_link: str | None = None


class PollRecord(BaseModel):
    """One snapshot in state/polls.jsonl. Append-only; never mutated."""

    model_config = ConfigDict(extra="allow")

    ts: datetime
    repo: str
    pr: int
    title: str | None = None
    head_sha: str
    status: Status
    ci: dict[str, CIConclusion] = Field(default_factory=dict)
    merge_state: str | None = None
    codex_open: int = 0
    codex_resolved: int = 0
    codex_last_review_at: datetime | None = None
    codex_last_review_head: str | None = None
    codex_pr_body_signal: CodexBodySignal | None = None
    threads: list[Thread] = Field(default_factory=list)
    summary: str | None = None
    trigger: str | None = None
    stage15_actions: list[Stage15Action] = Field(default_factory=list)

    def state_key(self) -> tuple:
        """Comparison key — short-circuit when state is unchanged between polls."""
        return (
            self.pr,
            self.head_sha,
            tuple(sorted((k, v.value) for k, v in self.ci.items())),
            self.codex_open,
            self.status.value,
        )


class VerdictHistoryEntry(BaseModel):
    ts: datetime
    verdict: Verdict
    reason: str


class Evidence(BaseModel):
    """Evidence chain backing a thread verdict — what the watchdog observed."""

    model_config = ConfigDict(extra="allow")

    thread_state: ThreadState | None = None
    author_reply_id: int | None = None
    author_reply_substantive: bool | None = None
    author_reply_summary: str | None = None
    code_changed: bool | None = None
    code_change_commit: str | None = None
    code_change_summary: str | None = None
    codex_followed_up: bool | None = None
    codex_reaction: str | None = None
    llm_verdict: str | None = None
    llm_model: str | None = None
    llm_confidence: float | None = None
    llm_reason: str | None = None
    llm_evidence: list[str] | None = None
    llm_error: str | None = None
    clean_codex_review_id: int | None = None
    clean_codex_review_head: str | None = None
    clean_codex_review_submitted_at: str | None = None
    clean_codex_signal_source: str | None = None
    demotion_reason: str | None = None
    synced_via: str | None = None
    synced_at: datetime | None = None


class GitHubThreadState(BaseModel):
    isResolved: bool  # noqa: N815 — mirrors GitHub GraphQL field name
    isOutdated: bool = False  # noqa: N815 — mirrors GitHub GraphQL field name
    viewerCanResolve: bool = True  # noqa: N815 — mirrors GitHub GraphQL field name
    resolvedBy: str | None = None  # noqa: N815 — mirrors GitHub GraphQL field name
    synced_via: str | None = None
    synced_at: datetime | None = None


class BoxMiss(BaseModel):
    """One skipped-box observation. Feeds rule-coverage visibility."""

    model_config = ConfigDict(extra="allow")

    ts: datetime
    repo: str
    pr: int
    head_sha: str
    box_text: str
    rule_id: str | None = None
    reason: str


class LedgerAction(StrEnum):
    SUBMIT_REVIEW_APPROVE = "submit_review_approve"
    EDIT_PR_BODY_CHECK_BOXES = "edit_pr_body_check_boxes"


class LedgerEntry(BaseModel):
    """One Stage-3+ write the watchdog made under SWM-1103 authorization.

    Append-only — never mutated. Older hand-written entries may carry extra
    top-level fields; ``extra="allow"`` keeps them readable.
    """

    model_config = ConfigDict(extra="allow")

    ts: datetime
    repo: str
    pr: int
    head_sha: str
    action: LedgerAction
    actor: str
    authorized_by: str
    reason: str
    evidence: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)


class ThreadSnapshot(BaseModel):
    """One thread's full living state — appended as new JSONL line on each poll."""

    model_config = ConfigDict(extra="allow")

    thread_id: str
    repo: str
    pr: int
    first_seen: datetime
    last_polled: datetime
    codex_comment_id: int
    path: str
    current_line: int | None = None
    original_line: int | None = None
    codex_severity: Severity
    effective_severity: Severity
    demotion_reason: str | None = None
    verdict: Verdict
    verdict_history: list[VerdictHistoryEntry] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
    github_state: GitHubThreadState | None = None
