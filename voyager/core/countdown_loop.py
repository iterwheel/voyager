"""Deterministic core of the Countdown multi-repo review-thread resolve loop.

This module is the no-LLM, no-governance skeleton (PRP VOY-1830, issue A):

* a non-blocking single-instance lock (so a second run exits instead of piling up),
* a production gate (``effective = requested ∩ frozenset ceiling``),
* open-PR + review-thread enumeration via the App-installation identity, and
* a deterministic candidate prefilter for PAT-fallback-eligible threads.

The prefilter deliberately does NOT reuse ``countdown_diagnostic._skip_reason``:
that predicate keeps threads the *current viewer can resolve*. Here the current
viewer is the App installation, and the PAT fallback only applies to threads the
App **cannot** resolve (``viewerCanResolve is False``) — exactly the contract
``cli._validate_pat_resolve_app_baseline`` enforces before a PAT resolve. Reusing
``_skip_reason`` would drop the fallback targets and keep the threads the PAT path
would refuse, so we mirror the baseline contract in ``_fallback_skip_reason``.

It intentionally does NOT decide *whether* a thread should be resolved (that is the
LLM gate, issue B) and does NOT resolve anything. It reports the candidates a later
stage would consider.
"""

from __future__ import annotations

import errno
import fcntl
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from voyager.core.countdown_diagnostic import (
    COUNTDOWN_AGENT_SLUG,
    DEDICATED_PAT_FALLBACK_RESOLVE_ALLOWED_REPOSITORIES,
    ReviewThreadCapability,
)
from voyager.core.github_app import GitHubGraphQLError

# Per-target failures we tolerate (skip the target, keep scanning the rest)
# rather than abort the whole multi-repo run.
#   - GitHubGraphQLError / httpx.HTTPError: transient API or per-repo access errors.
#   - TimeoutError: builtin (not an httpx subclass) from the async stack.
#   - RuntimeError: GitHubAppClient.installation_token raises it when a repo has no
#     discoverable App installation (github_app.py:140); tolerating it lets an
#     uninstalled/inaccessible repo be skipped instead of aborting the whole run.
#     Trade-off: a genuine RuntimeError in enumeration is recorded as a per-target
#     error rather than crashing — acceptable for a resilient multi-repo scan.
_TOLERATED_ENUMERATION_ERRORS = (GitHubGraphQLError, httpx.HTTPError, TimeoutError, RuntimeError)

# Repos whose raw PR numbers / thread node IDs MAY appear in CLI/JSON output.
# Sandbox only — VOY-1828 forbids emitting private real-repo identifiers to
# terminal transcripts. NEVER add iterwheel/voyager here; it would silently leak
# once the resolve frozenset is expanded (issue C).
_RAW_IDENTIFIER_REPOS = frozenset({"iterwheel/voyager-sandbox"})

DEFAULT_LOCK_PATH = Path.home() / ".voyager" / "locks" / "countdown-resolve-loop.lock"
DEFAULT_MAX_RESOLVES = 10

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


class AlreadyRunningError(RuntimeError):
    """Raised when another resolve-loop instance already holds the lock."""


class LoopGitHubClient(Protocol):
    """The slice of ``GitHubAppClient`` the loop needs (kept narrow for testing)."""

    async def graphql(
        self, app_slug: str, repository: str, *, query: str, variables: dict[str, Any]
    ) -> Any: ...

    async def pull_request_review_thread_capabilities(
        self, app_slug: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]: ...


@contextmanager
def single_instance_lock(path: Path = DEFAULT_LOCK_PATH) -> Iterator[None]:
    """Hold a non-blocking ``flock``; raise :class:`AlreadyRunning` if held elsewhere.

    Unlike the blocking ``LOCK_EX`` appenders elsewhere in the codebase
    (clearance/state.py, assembly/audit.py), this uses ``LOCK_NB`` so a concurrent
    run fails fast instead of queueing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            # Only treat genuine contention (lock held elsewhere) as "already running".
            # Other OSErrors (e.g. ENOLCK — locking unavailable on the filesystem) are
            # operational faults: re-raise so a scheduled run fails loudly instead of
            # silently skipping every invocation as exit-0 "already running".
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise
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
    ceiling: frozenset[str] = DEDICATED_PAT_FALLBACK_RESOLVE_ALLOWED_REPOSITORIES,
) -> tuple[list[str], list[str]]:
    """Split *requested* into ``(allowed, skipped)`` by the hard-ceiling frozenset.

    The ceiling is the only authorization boundary: a repo outside it is rejected
    even if it was requested (this is the "ship dark" guarantee — real repos stay
    inert until the frozenset is expanded via the VOY-1827/1828 CHG). Order is
    preserved; duplicates are collapsed.
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


@dataclass(frozen=True)
class Candidate:
    """A review thread that passed the deterministic prefilter."""

    repo: str
    pr: int
    thread_id: str


@dataclass(frozen=True)
class TargetError:
    """A repo (or repo#pr) whose enumeration failed; the run skipped it and continued."""

    repo: str
    message: str
    pr: int | None = None

    def public_target(self, *, show_raw: bool) -> str:
        if self.pr is None:
            return self.repo
        if show_raw or self.repo in _RAW_IDENTIFIER_REPOS:
            return f"{self.repo}#{self.pr}"
        # Non-sandbox: keep the private PR number out of output (VOY-1828).
        return f"{self.repo}#<redacted>"


@dataclass(frozen=True)
class LoopSummary:
    repos_scanned: tuple[str, ...]
    repos_skipped: tuple[str, ...]
    prs_scanned: int
    candidates: tuple[Candidate, ...]
    capped: bool
    errors: tuple[TargetError, ...] = ()
    repos_enumerated: int = 0

    @property
    def systemic_failure(self) -> bool:
        """Every attempted repo failed to even list its PRs — a likely global
        auth/config fault (missing key, 401 on the installation token) rather
        than isolated per-repo issues. The caller should fail, not report success."""
        return bool(self.repos_scanned) and self.repos_enumerated == 0

    def to_public_dict(self, *, show_raw: bool = False) -> dict[str, Any]:
        """Public summary. Raw PR numbers / thread IDs are emitted only for
        sandbox repos (VOY-1828) unless ``show_raw`` is set (operator opt-in)."""
        return {
            "repos_scanned": list(self.repos_scanned),
            "repos_skipped": list(self.repos_skipped),
            "prs_scanned": self.prs_scanned,
            "candidate_count": len(self.candidates),
            "capped": self.capped,
            "systemic_failure": self.systemic_failure,
            "errors": [
                {"target": e.public_target(show_raw=show_raw), "message": e.message}
                for e in self.errors
            ],
            "candidates": [_candidate_public(c, show_raw=show_raw) for c in self.candidates],
        }


def _candidate_public(candidate: Candidate, *, show_raw: bool) -> dict[str, Any]:
    if show_raw or candidate.repo in _RAW_IDENTIFIER_REPOS:
        return {"repo": candidate.repo, "pr": candidate.pr, "thread_id": candidate.thread_id}
    # Non-sandbox: never put the raw private PR number / thread node ID in output.
    return {"repo": candidate.repo, "pr": None, "thread_id": None, "redacted": True}


async def _list_open_pr_numbers(client: LoopGitHubClient, app_slug: str, repo: str) -> list[int]:
    owner, name = repo.split("/", 1)
    numbers: list[int] = []
    after: str | None = None
    seen_cursors: set[str] = set()
    while True:
        data = await client.graphql(
            app_slug,
            repo,
            query=_OPEN_PR_NUMBERS_QUERY,
            variables={"owner": owner, "name": name, "after": after},
        )
        connection = (((data or {}).get("repository") or {}).get("pullRequests")) or {}
        for node in connection.get("nodes") or []:
            number = node.get("number")
            if isinstance(number, int):
                numbers.append(number)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after or after in seen_cursors:
            break  # missing or repeating cursor — stop rather than loop forever
        seen_cursors.add(after)
    return numbers


def _capability_from_thread(
    thread: dict[str, Any], *, repo: str, pr: int
) -> ReviewThreadCapability:
    def _maybe_bool(key: str) -> bool | None:
        value = thread.get(key)
        return value if isinstance(value, bool) else None

    return ReviewThreadCapability(
        thread_id=str(thread.get("id") or ""),
        type_name="PullRequestReviewThread",
        repository=repo,
        pr=pr,
        is_resolved=_maybe_bool("isResolved"),
        is_outdated=_maybe_bool("isOutdated"),
        viewer_can_resolve=_maybe_bool("viewerCanResolve"),
        viewer_can_reply=_maybe_bool("viewerCanReply"),
    )


def _fallback_skip_reason(
    capability: ReviewThreadCapability, *, repository: str, pr: int
) -> str | None:
    """Reason to skip a thread as a PAT-fallback candidate, or None if eligible.

    Mirrors the App-baseline contract in ``cli._validate_pat_resolve_app_baseline``:
    eligible threads belong to the target PR, are unresolved, not outdated, repliable
    by the App, and — critically — NOT resolvable by the App (so the dedicated PAT is
    the only path that can resolve them). ``None``/unknown booleans are treated as
    "skip" (fail-closed): we never surface a thread whose state we could not confirm.
    """
    if capability.repository != repository or capability.pr != pr:
        return "thread_not_on_target_pr"
    if capability.is_resolved is not False:
        return "resolved_or_unknown"
    if capability.is_outdated is not False:
        return "outdated_or_unknown"
    if capability.viewer_can_reply is not True:
        return "app_cannot_reply"
    if capability.viewer_can_resolve is not False:
        # App can resolve (or unknown) → not a PAT-fallback target.
        return "app_can_resolve_no_fallback_needed"
    return None


async def _candidates_for_pr(
    client: LoopGitHubClient, app_slug: str, repo: str, pr: int
) -> list[Candidate]:
    threads = await client.pull_request_review_thread_capabilities(app_slug, repo, pr)
    out: list[Candidate] = []
    for thread in threads:
        capability = _capability_from_thread(thread, repo=repo, pr=pr)
        if not capability.thread_id:
            continue
        if _fallback_skip_reason(capability, repository=repo, pr=pr) is None:
            out.append(Candidate(repo=repo, pr=pr, thread_id=capability.thread_id))
    return out


async def run_resolve_loop(
    client: LoopGitHubClient,
    *,
    requested_repos: Sequence[str],
    app_slug: str = COUNTDOWN_AGENT_SLUG,
    ceiling: frozenset[str] = DEDICATED_PAT_FALLBACK_RESOLVE_ALLOWED_REPOSITORIES,
    max_resolves: int = DEFAULT_MAX_RESOLVES,
) -> LoopSummary:
    """Enumerate, gate, and prefilter; return the candidate set (no resolve, no LLM).

    ``max_resolves`` caps how many candidates are reported, mirroring the eventual
    blast-radius cap. Reaching the cap sets ``capped=True`` (no silent truncation).
    """
    allowed, skipped = gate_repos(requested_repos, ceiling=ceiling)

    candidates: list[Candidate] = []
    scanned: list[str] = []
    errors: list[TargetError] = []
    prs_scanned = 0
    repos_enumerated = 0
    capped = False
    for repo in allowed:
        if capped:
            break
        # Count the repo as scanned only once we actually start it, so a cap hit
        # in an earlier repo does not report later (un-visited) repos as scanned.
        scanned.append(repo)
        try:
            pr_numbers = await _list_open_pr_numbers(client, app_slug, repo)
        except _TOLERATED_ENUMERATION_ERRORS as exc:
            # One failing/inaccessible repo must not suppress the rest of the run.
            # If EVERY repo fails this way, systemic_failure flags it for the caller
            # so a global auth/config fault is not reported as a successful scan.
            errors.append(TargetError(repo=repo, message=str(exc)))
            continue
        repos_enumerated += 1
        for pr in pr_numbers:
            prs_scanned += 1
            try:
                pr_candidates = await _candidates_for_pr(client, app_slug, repo, pr)
            except _TOLERATED_ENUMERATION_ERRORS as exc:
                errors.append(TargetError(repo=repo, pr=pr, message=str(exc)))
                continue
            for candidate in pr_candidates:
                if len(candidates) >= max_resolves:
                    capped = True
                    break
                candidates.append(candidate)
            if capped:
                break

    return LoopSummary(
        repos_scanned=tuple(scanned),
        repos_skipped=tuple(skipped),
        prs_scanned=prs_scanned,
        candidates=tuple(candidates),
        capped=capped,
        errors=tuple(errors),
        repos_enumerated=repos_enumerated,
    )


def load_repo_list(path: Path) -> list[str]:
    """Read a repo list file: one ``OWNER/REPO`` per line; ``#`` comments and blanks ignored.

    Each entry must be ``OWNER/REPO`` (exactly one ``/``, both halves non-empty);
    a malformed line raises with the line number rather than failing opaquely deeper
    in enumeration.
    """
    repos: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        owner, sep, name = line.partition("/")
        if not sep or not owner or not name or "/" in name:
            raise ValueError(f"{path}:{lineno}: expected OWNER/REPO, got {line!r}")
        # Lowercase so --repos follows the same allowlist semantics as the TOML source
        # (config parsing lowercases) — otherwise `Iterwheel/Voyager-Sandbox` would miss
        # the lowercase ceiling and the file override would become a silent no-op.
        repos.append(line.lower())
    return repos
