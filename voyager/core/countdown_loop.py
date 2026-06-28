"""
countdown_loop — multi-repo review-thread resolve loop, run as the fixed machine
account on top of the #222 ``resolve_conversation`` resolver (PRP VOY-1831).

For each allowlisted repo: enumerate open PRs, deterministically prefilter the
threads the machine account can resolve, ask an LLM **should-resolve gate** whether
each candidate is actually addressed, and resolve only the approved ones — under a
single-instance lock, a ``max_resolves`` blast-radius cap, and a redacted audit
trail.

Safety model (see PRP §Safety model): the LLM gate is a FAIL-CLOSED VETO on top of
the deterministic candidate set. It can only veto a mechanically-resolvable thread,
never promote a non-candidate; any gate error / parse failure / refusal defaults to
skip. The authoritative resolve is still ``resolve_conversations`` (identity-gated,
resolve-only). Non-sandbox repos never emit raw PR numbers / thread node IDs
(VOY-1828).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from voyager.core.resolve_conversation import (
    RESOLVE_ALLOWED_REPOS,
    ResolveConversationError,
    ThreadState,
    _assert_machine_identity,
    _parse_thread_node,
    _should_resolve,
    resolve_conversations,
)

DEFAULT_LOCK_PATH = Path.home() / ".voyager" / "countdown-resolve-loop.lock"
DEFAULT_AUDIT_PATH = Path.home() / ".voyager" / "countdown-resolve-loop.audit.jsonl"
DEFAULT_MAX_RESOLVES = 20

# Repos whose raw PR numbers / thread node IDs MAY appear in output (VOY-1828).
# Sandbox only — NEVER add iterwheel/voyager here.
_RAW_IDENTIFIER_REPOS: frozenset[str] = frozenset({"iterwheel/voyager-sandbox"})

# Per-target failures we tolerate (skip the target, keep scanning) rather than
# abort the whole multi-repo run.
_TOLERATED_ERRORS = (httpx.HTTPError, TimeoutError, ResolveConversationError, RuntimeError)


class AlreadyRunningError(RuntimeError):
    """Raised when another resolve-loop instance already holds the lock."""


class ReadGqlFn(Protocol):
    def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...


class ShouldResolveGate(Protocol):
    """Decides whether a mechanically-resolvable candidate should actually resolve."""

    def should_resolve(self, candidate: Candidate) -> GateVerdict: ...


_OPEN_PR_NUMBERS_QUERY = """
query OpenPullRequestNumbers($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { number }
    }
  }
}
"""

_PR_THREADS_WITH_COMMENTS_QUERY = """
query ReviewThreadsWithComments($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          viewerCanResolve
          viewerCanReply
          comments(first: 20) {
            pageInfo { hasNextPage }
            nodes { author { login } body }
          }
        }
      }
    }
  }
}
"""

# Re-read JUST the live comment count of one thread, to detect a comment added between
# the gate's judgment and the resolve mutation (TOCTOU on stale gate evidence).
_THREAD_FRESHNESS_QUERY = """
query ThreadFreshness($threadId: ID!) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 1) { totalCount }
    }
  }
}
"""


ACTION_VETOED = "vetoed"
ACTION_WOULD_RESOLVE = "would_resolve"
ACTION_SKIPPED_STALE = "skipped_stale"
ACTION_RESOLVED = "resolved"
ACTION_RESOLVE_FAILED = "resolve_failed"


@dataclass(frozen=True)
class Candidate:
    """A review thread that passed the deterministic (mechanically-resolvable) prefilter."""

    repo: str
    pr: int
    thread_id: str
    comments: tuple[tuple[str, str], ...]  # (author_login, body)
    # True if the thread has more comments than we fetched — the gate must fail
    # closed, since a later "still broken" comment may be missing from the prefix.
    comments_truncated: bool = False


@dataclass(frozen=True)
class GateVerdict:
    should_resolve: bool
    reason: str


@dataclass(frozen=True)
class TargetError:
    """A repo (or repo#pr) whose enumeration/resolve failed; the run skipped it."""

    repo: str
    message: str
    pr: int | None = None

    def public_target(self) -> str:
        if self.pr is None:
            return self.repo
        # Redaction keys ONLY on repo membership (VOY-1828) — no operator override.
        if self.repo in _RAW_IDENTIFIER_REPOS:
            return f"{self.repo}#{self.pr}"
        return f"{self.repo}#<redacted>"


@dataclass(frozen=True)
class Decision:
    """One per-candidate outcome, for the summary and the audit trail."""

    repo: str
    pr: int
    thread_id: str
    action: str  # resolved | would_resolve | vetoed | skipped_stale | resolve_failed
    reason: str

    def public(self) -> dict[str, Any]:
        # Redaction keys ONLY on repo membership (VOY-1828) — no operator override.
        # `reason` is dropped for non-sandbox repos: veto/would_resolve reasons are
        # LLM free-text over comment bodies and could echo a raw PR#/node-ID. `action`
        # is a fixed enum, always safe. Sandbox keeps the reason for debugging.
        out: dict[str, Any] = {"repo": self.repo, "action": self.action}
        if self.repo in _RAW_IDENTIFIER_REPOS:
            out["pr"] = self.pr
            out["thread_id"] = self.thread_id
            out["reason"] = self.reason
        else:
            out["redacted"] = True
        return out


@dataclass
class _CandidateFlow:
    repo: str
    pr: int
    candidate: Candidate
    gate: ShouldResolveGate
    read_gql: ReadGqlFn
    resolve_gql: Any
    do_resolve: Any
    dry_run: bool
    max_resolves: int
    approved_count: int
    timestamp: str
    audit_path: Path | None
    verdict: GateVerdict | None = None
    capped: bool = False


_Guard = Callable[[_CandidateFlow], Decision | None]


@dataclass(frozen=True)
class LoopSummary:
    repos_scanned: tuple[str, ...]
    repos_skipped: tuple[str, ...]
    prs_scanned: int
    decisions: tuple[Decision, ...]
    capped: bool
    dry_run: bool
    errors: tuple[TargetError, ...] = ()
    repos_enumerated: int = 0
    prs_enumerated: int = 0

    @property
    def resolved(self) -> int:
        return sum(1 for d in self.decisions if d.action == "resolved")

    @property
    def would_resolve(self) -> int:
        return sum(1 for d in self.decisions if d.action == "would_resolve")

    @property
    def systemic_failure(self) -> bool:
        """The whole scan failed at some scope (likely a global auth/config fault),
        so the caller should fail rather than report a clean zero-candidate run."""
        if not self.repos_scanned:
            return False
        if self.repos_enumerated == 0:
            return True
        return self.prs_scanned > 0 and self.prs_enumerated == 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "repos_scanned": list(self.repos_scanned),
            "repos_skipped": list(self.repos_skipped),
            "prs_scanned": self.prs_scanned,
            "resolved": self.resolved,
            "would_resolve": self.would_resolve,
            "decision_count": len(self.decisions),
            "capped": self.capped,
            "dry_run": self.dry_run,
            "systemic_failure": self.systemic_failure,
            "errors": [{"target": e.public_target(), "message": e.message} for e in self.errors],
            "decisions": [d.public() for d in self.decisions],
        }


# --------------------------------------------------------------------------- #
# Lock / gate / repo-list (mechanism-agnostic; ported from #221, de-PAT'd)
# --------------------------------------------------------------------------- #


@contextmanager
def single_instance_lock(path: Path = DEFAULT_LOCK_PATH) -> Iterator[None]:
    """Hold a non-blocking ``flock``; raise :class:`AlreadyRunningError` if held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise  # operational fault (e.g. ENOLCK) — fail loud, not "already running"
            raise AlreadyRunningError(f"another resolve-loop run holds {path}") from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def gate_repos(
    requested: Sequence[str],
    *,
    ceiling: frozenset[str] = RESOLVE_ALLOWED_REPOS,
) -> tuple[list[str], list[str]]:
    """Split *requested* into ``(allowed, skipped)`` by the allowlist ceiling.

    Order preserved, duplicates collapsed. A repo outside the ceiling is rejected
    even if requested — the resolver allowlist is the only authorization boundary.
    """
    allowed: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for repo in requested:
        if repo in seen:
            continue
        seen.add(repo)
        (allowed if repo in ceiling else skipped).append(repo)
    return allowed, skipped


def load_repo_list(path: Path) -> list[str]:
    """Read an ``OWNER/REPO``-per-line file; ``#`` comments and blanks ignored."""
    repos: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        owner, sep, name = line.partition("/")
        if not sep or not owner or not name or "/" in name:
            raise ValueError(f"{path}:{lineno}: expected OWNER/REPO, got {line!r}")
        repos.append(line.lower())
    return repos


# --------------------------------------------------------------------------- #
# Read client (machine token; broad read-only queries, separate from resolve path)
# --------------------------------------------------------------------------- #


_ALLOWED_READ_QUERIES: frozenset[str] = frozenset(
    {_OPEN_PR_NUMBERS_QUERY, _PR_THREADS_WITH_COMMENTS_QUERY, _THREAD_FRESHNESS_QUERY}
)


def _default_client_factory() -> httpx.Client:
    return httpx.Client(timeout=20)


def make_read_gql(
    token: str,
    *,
    client_factory: Any = _default_client_factory,
) -> ReadGqlFn:
    """GraphQL read client bound to *token* (never logged)."""

    def _gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Defense-in-depth: the read client may only run the two known read queries.
        # It binds a write-capable token, so an accidental mutation must be impossible.
        if query not in _ALLOWED_READ_QUERIES:
            raise ResolveConversationError("read client refusing an unknown GraphQL operation")
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
                f"read GraphQL HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError:
            raise ResolveConversationError("read GraphQL request failed") from None
        except ValueError:
            raise ResolveConversationError("read GraphQL returned a non-JSON response") from None
        errors = body.get("errors")
        if errors:
            raise ResolveConversationError(f"read GraphQL returned {len(errors)} error(s)")
        return body.get("data") or {}

    return _gql


def _list_open_pr_numbers(gql: ReadGqlFn, repo: str) -> list[int]:
    owner, name = repo.split("/", 1)
    numbers: list[int] = []
    seen_numbers: set[int] = set()
    after: str | None = None
    seen_cursors: set[str] = set()
    while True:
        data = gql(_OPEN_PR_NUMBERS_QUERY, {"owner": owner, "name": name, "after": after})
        repository = (data or {}).get("repository")
        if repository is None:
            # A null repository (token lost access, repo renamed/deleted) must be a hard
            # error — NOT an empty list, which would look like a healthy zero-PR scan and
            # leave systemic_failure false on an unattended run.
            raise ResolveConversationError(f"repository not found in {repo!r}")
        conn = (repository.get("pullRequests")) or {}
        for node in conn.get("nodes") or []:
            number = node.get("number")
            if isinstance(number, int) and number not in seen_numbers:
                seen_numbers.add(number)  # overlapping/repeated pages must not double-scan
                numbers.append(number)
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        if not after or after in seen_cursors:
            break
        seen_cursors.add(after)
    return numbers


def _thread_comments(node: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for c in ((node.get("comments") or {}).get("nodes")) or []:
        author = ((c.get("author") or {}).get("login")) or "(unknown)"
        body = c.get("body") or ""
        out.append((str(author), str(body)))
    return tuple(out)


def _thread_comment_count(gql: ReadGqlFn, thread_id: str) -> int | None:
    """Live comment count for one thread; None if it can't be read (fail closed).

    A transient read error must NOT abort the whole multi-repo run — it returns None so
    the caller records this single candidate as skipped_stale and keeps scanning.
    """
    try:
        data = gql(_THREAD_FRESHNESS_QUERY, {"threadId": thread_id})
    except _TOLERATED_ERRORS:
        return None
    node = (data or {}).get("node")
    if not node:
        return None
    total = (node.get("comments") or {}).get("totalCount")
    return total if isinstance(total, int) else None


def _candidates_for_pr(gql: ReadGqlFn, repo: str, pr: int) -> list[Candidate]:
    """Enumerate a PR's review threads and keep only mechanically-resolvable ones."""
    owner, name = repo.split("/", 1)
    candidates: list[Candidate] = []
    seen_threads: set[str] = set()
    after: str | None = None
    seen_cursors: set[str] = set()
    while True:
        data = gql(
            _PR_THREADS_WITH_COMMENTS_QUERY,
            {"owner": owner, "name": name, "number": pr, "after": after},
        )
        pull = ((data or {}).get("repository") or {}).get("pullRequest")
        if pull is None:
            raise ResolveConversationError(f"PR not found in {repo!r}")
        threads = pull.get("reviewThreads") or {}
        for node in threads.get("nodes") or []:
            ts: ThreadState = _parse_thread_node(node)
            if not ts.thread_id or not _should_resolve(ts):
                continue
            if ts.thread_id in seen_threads:
                continue  # overlapping/repeated pages must not double-gate a thread
            seen_threads.add(ts.thread_id)
            comments_page = (node.get("comments") or {}).get("pageInfo") or {}
            candidates.append(
                Candidate(
                    repo=repo,
                    pr=pr,
                    thread_id=ts.thread_id,
                    comments=_thread_comments(node),
                    comments_truncated=bool(comments_page.get("hasNextPage")),
                )
            )
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        if not after or after in seen_cursors:
            break
        seen_cursors.add(after)
    return candidates


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def _append_audit(path: Path, record: dict[str, Any]) -> None:
    """Append one redacted JSON line under an exclusive lock."""
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


def _cap_guard(ctx: _CandidateFlow) -> Decision | None:
    if ctx.approved_count >= ctx.max_resolves:
        ctx.capped = True
    return None


def _gate_guard(ctx: _CandidateFlow) -> Decision | None:
    ctx.verdict = _gate_verdict(ctx.gate, ctx.candidate)
    if not ctx.verdict.should_resolve:
        return Decision(
            ctx.repo,
            ctx.pr,
            ctx.candidate.thread_id,
            ACTION_VETOED,
            ctx.verdict.reason,
        )
    return None


def _dry_run_guard(ctx: _CandidateFlow) -> Decision | None:
    if not ctx.dry_run:
        return None
    verdict = ctx.verdict or GateVerdict(True, "ok")
    return Decision(
        ctx.repo,
        ctx.pr,
        ctx.candidate.thread_id,
        ACTION_WOULD_RESOLVE,
        verdict.reason,
    )


def _freshness_guard(ctx: _CandidateFlow) -> Decision | None:
    # TOCTOU guard: stale gate evidence must not resolve newly active reviewer dissent.
    live_count = _thread_comment_count(ctx.read_gql, ctx.candidate.thread_id)
    if live_count is None or live_count != len(ctx.candidate.comments):
        return Decision(
            ctx.repo,
            ctx.pr,
            ctx.candidate.thread_id,
            ACTION_SKIPPED_STALE,
            "comments_changed",
        )
    return None


def _audit_write_ahead_guard(ctx: _CandidateFlow) -> Decision | None:
    # Write-ahead intent: if audit cannot persist, abort before mutating GitHub state.
    if ctx.audit_path is not None:
        verdict = ctx.verdict or GateVerdict(True, "ok")
        intent = Decision(ctx.repo, ctx.pr, ctx.candidate.thread_id, "resolving", verdict.reason)
        _append_audit(ctx.audit_path, {"ts": ctx.timestamp, "dry_run": False, **intent.public()})
    return None


def _resolve_guard(ctx: _CandidateFlow) -> Decision | None:
    return _safe_resolve(ctx.do_resolve, ctx.candidate, ctx.resolve_gql)


_CANDIDATE_GUARDS: tuple[_Guard, ...] = (
    _cap_guard,
    _gate_guard,
    _dry_run_guard,
    _freshness_guard,
    _audit_write_ahead_guard,
    _resolve_guard,
)


def _drive_candidate(ctx: _CandidateFlow) -> Decision | None:
    for guard in _CANDIDATE_GUARDS:
        decision = guard(ctx)
        if decision is not None or ctx.capped:
            return decision
    raise RuntimeError("candidate guard list did not reach a terminal decision")


def _assert_resolver_identity_guard(resolve_fn: Any, resolve_gql: Any) -> None:
    # Run-level guard by design: wrong identity must abort before any LLM fan-out.
    if resolve_fn is None:
        _assert_machine_identity(resolve_gql)


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


def run_resolve_loop(
    *,
    requested_repos: Sequence[str],
    gate: ShouldResolveGate,
    read_gql: ReadGqlFn,
    resolve_gql: Any,
    ceiling: frozenset[str] = RESOLVE_ALLOWED_REPOS,
    max_resolves: int = DEFAULT_MAX_RESOLVES,
    dry_run: bool = False,
    timestamp: str | None = None,
    audit_path: Path | None = DEFAULT_AUDIT_PATH,
    resolve_fn: Any = None,
) -> LoopSummary:
    """Enumerate → deterministic prefilter → fail-closed LLM gate → resolve.

    *gate*, *read_gql*, *resolve_gql* are injected (fakes in tests). *resolve_gql* is
    the operation-allowlisted resolver client (``make_github_gql``); resolution goes
    through ``resolve_conversations`` so the identity/resolve-only guarantees hold.
    *resolve_fn* defaults to that path; tests inject a fake ``(Candidate, gql) -> Decision``.

    ``max_resolves`` bounds the number of APPROVED candidates per run (resolves in
    live mode, would-resolves in dry-run) — a single blast-radius/cost ceiling that
    applies in both modes.
    """
    do_resolve = resolve_fn or _do_resolve
    allowed, skipped = gate_repos(requested_repos, ceiling=ceiling)
    when = timestamp or datetime.now(UTC).isoformat()

    # Assert the resolver identity ONCE up front (real path only) so a wrong/expired
    # machine credential aborts immediately instead of fanning out into one wasted
    # LLM call per candidate across every repo. Done even in dry-run: that mode still
    # enumerates threads and ships their comments to the LLM, so a wrong identity must
    # abort there too (matches resolve_conversations, which gates identity regardless).
    _assert_resolver_identity_guard(resolve_fn, resolve_gql)

    decisions: list[Decision] = []
    scanned: list[str] = []
    errors: list[TargetError] = []
    prs_scanned = 0
    repos_enumerated = 0
    prs_enumerated = 0
    capped = False

    def _approved() -> int:
        # Bound APPROVED resolve ATTEMPTS, not just successes: a live run where the
        # resolver keeps returning resolve_failed/skipped_stale must still hit the cap.
        # Every gate-approved candidate yields a non-vetoed decision (would_resolve in
        # dry-run; resolved/skipped_stale/resolve_failed live).
        return sum(1 for d in decisions if d.action != "vetoed")

    def _record(d: Decision) -> None:
        decisions.append(d)
        if audit_path is not None:
            _append_audit(audit_path, {"ts": when, "dry_run": dry_run, **d.public()})

    for repo in allowed:
        if capped:
            break
        scanned.append(repo)
        try:
            pr_numbers = _list_open_pr_numbers(read_gql, repo)
        except _TOLERATED_ERRORS as exc:
            errors.append(TargetError(repo=repo, message=str(exc)))
            continue
        repos_enumerated += 1
        for pr in pr_numbers:
            if capped:
                break
            prs_scanned += 1
            try:
                candidates = _candidates_for_pr(read_gql, repo, pr)
            except _TOLERATED_ERRORS as exc:
                errors.append(TargetError(repo=repo, pr=pr, message=str(exc)))
                continue
            prs_enumerated += 1
            for cand in candidates:
                flow = _CandidateFlow(
                    repo=repo,
                    pr=pr,
                    candidate=cand,
                    gate=gate,
                    read_gql=read_gql,
                    resolve_gql=resolve_gql,
                    do_resolve=do_resolve,
                    dry_run=dry_run,
                    max_resolves=max_resolves,
                    approved_count=_approved(),
                    timestamp=when,
                    audit_path=audit_path,
                )
                decision = _drive_candidate(flow)
                if flow.capped:
                    capped = True
                    break
                if decision is not None:
                    _record(decision)

    return LoopSummary(
        repos_scanned=tuple(scanned),
        repos_skipped=tuple(skipped),
        prs_scanned=prs_scanned,
        decisions=tuple(decisions),
        capped=capped,
        dry_run=dry_run,
        errors=tuple(errors),
        repos_enumerated=repos_enumerated,
        prs_enumerated=prs_enumerated,
    )


def _gate_verdict(gate: ShouldResolveGate, cand: Candidate) -> GateVerdict:
    """Call the gate, FAIL CLOSED on any error/exception (default to veto/skip)."""
    # Truncated comment list → the gate would judge on a partial prefix; veto without
    # asking it, since a later "still broken" comment may be missing.
    if cand.comments_truncated:
        return GateVerdict(False, "comments_truncated")
    try:
        verdict = gate.should_resolve(cand)
    except Exception as exc:
        return GateVerdict(False, f"gate_error:{type(exc).__name__}")
    if not isinstance(verdict, GateVerdict) or verdict.should_resolve is not True:
        return GateVerdict(False, getattr(verdict, "reason", "gate_declined") or "gate_declined")
    return verdict


def _safe_resolve(do_resolve: Any, cand: Candidate, resolve_gql: Any) -> Decision:
    """Run the resolve step with per-target isolation: any unexpected exception
    becomes a ``resolve_failed`` decision rather than aborting the whole multi-repo run."""
    try:
        return do_resolve(cand, resolve_gql)  # type: ignore[no-any-return]
    except _TOLERATED_ERRORS as exc:
        return Decision(cand.repo, cand.pr, cand.thread_id, ACTION_RESOLVE_FAILED, str(exc))


def _do_resolve(cand: Candidate, resolve_gql: Any) -> Decision:
    """Resolve a single approved candidate via the identity-gated resolver."""
    try:
        summary = resolve_conversations(repo=cand.repo, thread_id=cand.thread_id, gql=resolve_gql)
    except ResolveConversationError as exc:
        return Decision(cand.repo, cand.pr, cand.thread_id, ACTION_RESOLVE_FAILED, str(exc))
    if summary.resolved == 1:
        return Decision(cand.repo, cand.pr, cand.thread_id, ACTION_RESOLVED, "ok")
    # resolved != 1: distinguish "mutation fired but GitHub did not confirm" (a real
    # failure) from "thread was no longer resolvable at apply" (benign skip).
    actions = {action for _tid, action in summary.details}
    if "verify_failed" in actions:
        return Decision(cand.repo, cand.pr, cand.thread_id, ACTION_RESOLVE_FAILED, "verify_failed")
    return Decision(
        cand.repo,
        cand.pr,
        cand.thread_id,
        ACTION_SKIPPED_STALE,
        "not_resolvable_at_apply",
    )
