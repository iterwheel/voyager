"""Countdown merge-loop: autonomously rebase-merge fully-green agent PRs.

Spec: rules/VOY-1839-PRP-Countdown-Merge-Loop-Autonomous-Agent-PR-Merge.md.
Mirrors the resolve-loop skeleton (countdown_loop.py); single mutation type:
mergePullRequest (REBASE, expectedHeadOid-guarded). Fail-closed throughout.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from voyager.bots.clearance.constants import CLEARANCE_COMMENT_MARKER
from voyager.core.countdown_loop import gate_repos
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


_STAGE_RE = re.compile(r"Stage:\s*(\d+)")
_HEAD_RE = re.compile(r"Head SHA:\s*`([0-9a-f]{40})`")


def parse_readiness(body: str) -> tuple[int, str] | None:
    """Parse a clearance readiness comment into (stage, head_sha).

    Fail-closed: anything not carrying the marker, a stage, AND a full
    40-hex head SHA returns None.
    """
    if CLEARANCE_COMMENT_MARKER not in body:
        return None
    stage_m = _STAGE_RE.search(body)
    head_m = _HEAD_RE.search(body)
    if not stage_m or not head_m:
        return None
    return int(stage_m.group(1)), head_m.group(1)


def should_merge(s: PrSnapshot) -> str:
    """Deterministic merge predicate. Returns "ok" or a stable skip reason.

    Order matters only for reporting; every condition is independently
    fail-closed. Stage >= REQUIRED_READINESS_STAGE accepts both
    "3 - Ready for approval" and "4 - Ready for merge".
    """
    if s.author not in AGENT_PR_AUTHORS:
        return "not_agent_author"
    if s.is_draft:
        return "draft"
    if s.checks_state != "SUCCESS":
        return "checks_not_green"
    if s.unresolved_threads is None:
        return "threads_unreadable"
    if s.unresolved_threads > 0:
        return "threads_unresolved"
    if s.readiness_stage is None or s.readiness_head is None:
        return "readiness_missing"
    if s.readiness_stage < REQUIRED_READINESS_STAGE:
        return "readiness_not_ready"
    if s.readiness_head != s.head_oid:
        return "readiness_stale_head"
    return "ok"


GqlFn = Callable[[str, dict[str, Any]], dict[str, Any]]

_AGENT_PR_PAGE_QUERY = """
query AgentOpenPrs($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 50, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        number
        isDraft
        headRefOid
        author { login }
        commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
        comments(last: 50) { nodes { author { login } body } }
      }
    }
  }
}
"""

_PR_THREADS_QUERY = """
query PrThreadStates($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { isResolved }
      }
    }
  }
}
"""

_ALLOWED_MERGE_READ_QUERIES = frozenset({_AGENT_PR_PAGE_QUERY, _PR_THREADS_QUERY})


def _default_client_factory() -> httpx.Client:
    return httpx.Client(timeout=20)


def _post_gql(
    token: str, query: str, variables: dict[str, Any], client_factory: Any
) -> dict[str, Any]:
    try:
        with client_factory() as client:
            resp = client.post(
                "https://api.github.com/graphql",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"query": query, "variables": variables},
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
    except httpx.HTTPStatusError as exc:
        raise ResolveConversationError(
            f"merge-loop GraphQL HTTP {exc.response.status_code}"
        ) from None
    except httpx.HTTPError:
        raise ResolveConversationError("merge-loop GraphQL request failed") from None
    except ValueError:
        raise ResolveConversationError("merge-loop GraphQL returned a non-JSON response") from None
    errors = body.get("errors")
    if errors:
        raise ResolveConversationError(f"merge-loop GraphQL returned {len(errors)} error(s)")
    return body.get("data") or {}


def make_merge_read_gql(token: str, *, client_factory: Any = _default_client_factory) -> GqlFn:
    """Read client bound to *token*; refuses queries outside the merge-loop set."""

    def _gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if query not in _ALLOWED_MERGE_READ_QUERIES:
            raise ResolveConversationError(
                "merge-loop read client refusing an unknown GraphQL operation"
            )
        return _post_gql(token, query, variables, client_factory)

    return _gql


def _unresolved_thread_count(gql: GqlFn, repo: str, number: int) -> int | None:
    """Paginated unresolved-thread count; None on any read fault (fail closed)."""
    owner, name = repo.split("/", 1)
    unresolved = 0
    after: str | None = None
    seen_cursors: set[str] = set()
    while True:
        try:
            data = gql(
                _PR_THREADS_QUERY,
                {"owner": owner, "name": name, "number": number, "after": after},
            )
        except ResolveConversationError:
            return None
        pull = ((data or {}).get("repository") or {}).get("pullRequest")
        if pull is None:
            return None
        threads = pull.get("reviewThreads") or {}
        for node in threads.get("nodes") or []:
            if node.get("isResolved") is not True:
                unresolved += 1
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return unresolved
        after = page.get("endCursor")
        if not after or after in seen_cursors:
            return unresolved
        seen_cursors.add(after)


def _readiness_from_comments(node: dict[str, Any]) -> tuple[int | None, str | None]:
    """Latest clearance-authored readiness (stage, head); (None, None) if absent."""
    stage: int | None = None
    head: str | None = None
    for c in ((node.get("comments") or {}).get("nodes")) or []:
        author = ((c.get("author") or {}).get("login")) or ""
        if author not in CLEARANCE_BOT_LOGINS:
            continue
        parsed = parse_readiness(c.get("body") or "")
        if parsed is not None:
            stage, head = parsed  # last matching comment wins
    return stage, head


def snapshots_for_repo(gql: GqlFn, repo: str) -> list[PrSnapshot]:
    """Every open PR's merge-relevant state; thread counts fetched lazily."""
    owner, name = repo.split("/", 1)
    snapshots: list[PrSnapshot] = []
    after: str | None = None
    seen_cursors: set[str] = set()
    seen_numbers: set[int] = set()
    while True:
        data = gql(_AGENT_PR_PAGE_QUERY, {"owner": owner, "name": name, "after": after})
        repository = (data or {}).get("repository")
        if repository is None:
            raise ResolveConversationError(f"repository not found in {repo!r}")
        conn = repository.get("pullRequests") or {}
        for node in conn.get("nodes") or []:
            number = node.get("number")
            if not isinstance(number, int) or number in seen_numbers:
                continue
            seen_numbers.add(number)
            author = ((node.get("author") or {}).get("login")) or ""
            is_draft = bool(node.get("isDraft"))
            head_oid = str(node.get("headRefOid") or "")
            rollup_nodes = ((node.get("commits") or {}).get("nodes")) or [{}]
            rollup = ((rollup_nodes[0].get("commit") or {}).get("statusCheckRollup")) or {}
            checks_state = rollup.get("state")
            cheap_green = author in AGENT_PR_AUTHORS and not is_draft and checks_state == "SUCCESS"
            threads = _unresolved_thread_count(gql, repo, number) if cheap_green else None
            stage, r_head = _readiness_from_comments(node)
            snapshots.append(
                PrSnapshot(
                    pr_id=str(node.get("id") or ""),
                    number=number,
                    author=author,
                    is_draft=is_draft,
                    head_oid=head_oid,
                    checks_state=checks_state,
                    unresolved_threads=threads,
                    readiness_stage=stage,
                    readiness_head=r_head,
                )
            )
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        if not after or after in seen_cursors:
            break
        seen_cursors.add(after)
    return snapshots


_MERGE_MUTATION = """
mutation MergeAgentPr($prId: ID!, $expectedHeadOid: GitObjectID!) {
  mergePullRequest(
    input: {pullRequestId: $prId, mergeMethod: REBASE, expectedHeadOid: $expectedHeadOid}
  ) {
    pullRequest { merged }
  }
}
"""


def make_merge_gql(token: str, *, client_factory: Any = _default_client_factory) -> GqlFn:
    """Mutation client; the ONLY operation it will run is the rebase merge."""

    def _gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if query is not _MERGE_MUTATION:
            raise ResolveConversationError(
                "merge-loop mutation client refusing an unknown GraphQL operation"
            )
        return _post_gql(token, query, variables, client_factory)

    return _gql


def merge_pr(merge_gql: GqlFn, pr_id: str, expected_head: str) -> tuple[str, str]:
    """Attempt one rebase merge. expectedHeadOid makes a moved head a benign
    failure (GitHub rejects), so scan→apply races cannot merge stale state.
    Never raises: one PR's failure must not abort the multi-repo run.
    """
    try:
        data = merge_gql(_MERGE_MUTATION, {"prId": pr_id, "expectedHeadOid": expected_head})
    except ResolveConversationError as exc:
        return "merge_failed", str(exc)
    merged = ((data.get("mergePullRequest") or {}).get("pullRequest") or {}).get("merged")
    if merged is True:
        return "merged", ""
    return "merge_failed", "mutation returned without merged=true"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _append_merge_audit(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line under an exclusive lock (0600, local-only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def run_merge_loop(
    requested_repos: Sequence[str],
    *,
    read_gql: GqlFn,
    merge_gql: GqlFn,
    max_merges: int = 3,
    dry_run: bool = False,
    audit_path: Path | None = DEFAULT_MERGE_AUDIT_PATH,
    now: Callable[[], str] | None = None,
) -> MergeLoopSummary:
    """Scan allowlisted repos and rebase-merge fully-green agent PRs.

    Non-agent PRs are invisible to this loop (no decision, no audit).
    Agent PRs get exactly one decision each. Cap bounds approved merge
    ATTEMPTS (would_merge in dry-run), not successes: a live run where
    merge_pr keeps returning merge_failed must still hit the cap. One
    repo's scan failure is recorded and the remaining repos still run.
    """
    timestamp = (now or _utc_now)()
    allowed, skipped = gate_repos(requested_repos, ceiling=merge_allowed_repos())
    decisions: list[MergeDecision] = []
    errors: list[tuple[str, str]] = []
    prs_scanned = 0
    approved = 0
    capped = False

    def _record(decision: MergeDecision, head: str) -> None:
        decisions.append(decision)
        if audit_path is not None:
            _append_merge_audit(
                audit_path,
                {
                    "ts": timestamp,
                    "repo": decision.repo,
                    "pr": decision.pr,
                    "action": decision.action,
                    "reason": decision.reason,
                    "head": head,
                    "dry_run": dry_run,
                },
            )

    for repo in allowed:
        try:
            snapshots = snapshots_for_repo(read_gql, repo)
        except ResolveConversationError as exc:
            errors.append((repo, str(exc)))
            continue
        prs_scanned += len(snapshots)
        for s in snapshots:
            if s.author not in AGENT_PR_AUTHORS:
                continue  # never touched, never listed
            reason = should_merge(s)
            if reason != "ok":
                _record(MergeDecision(repo, s.number, "skipped", reason), s.head_oid)
                continue
            if approved >= max_merges:
                capped = True
                _record(MergeDecision(repo, s.number, "skipped", "capped"), s.head_oid)
                continue
            approved += 1
            if dry_run:
                _record(MergeDecision(repo, s.number, "would_merge"), s.head_oid)
                continue
            action, message = merge_pr(merge_gql, s.pr_id, s.head_oid)
            _record(MergeDecision(repo, s.number, action, message), s.head_oid)

    return MergeLoopSummary(
        repos_scanned=tuple(allowed),
        repos_skipped=tuple(skipped),
        prs_scanned=prs_scanned,
        decisions=tuple(decisions),
        capped=capped,
        dry_run=dry_run,
        errors=tuple(errors),
    )
