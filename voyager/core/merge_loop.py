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
from voyager.core.resolve_conversation import (
    ResolveConversationError,
    _assert_machine_identity,
)

AGENT_PR_AUTHORS = frozenset({"ryosaeba1985"})
CLEARANCE_APP_SLUG = "iterwheel-clearance"
REQUIRED_READINESS_STAGE = 3

MERGE_ALLOWED_REPOS = frozenset({"iterwheel/voyager-sandbox"})
_RAW_IDENTIFIER_REPOS = frozenset({"iterwheel/voyager-sandbox"})
ALLOWED_BASE_REFS = frozenset({"main"})
_EXTRA_REPOS_ENV = "VOYAGER_MERGE_EXTRA_REPOS"
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")
_EXTRA_AUTHORS_ENV = "VOYAGER_MERGE_EXTRA_AUTHORS"
_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")

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


def merge_allowed_authors() -> frozenset[str]:
    """Effective author allowlist: the built-in agent account plus
    operator-local extras (e.g. ``dependabot`` for dependency-bump PRs).

    FAIL-CLOSED parse, same contract as merge_allowed_repos(): a malformed
    entry raises instead of being skipped. Entries are lowercased so
    should_merge/snapshots_for_repo can do case-insensitive membership
    checks (GraphQL `author.login` for dependabot is the plain login
    `dependabot`, not `app/dependabot` or `dependabot[bot]`).
    """
    raw = os.environ.get(_EXTRA_AUTHORS_ENV, "")
    extras: set[str] = set()
    for idx, token in enumerate(raw.replace(",", " ").split(), start=1):
        if not _LOGIN_PATTERN.match(token):
            raise ResolveConversationError(
                f"{_EXTRA_AUTHORS_ENV} entry #{idx} is not a valid GitHub login"
            )
        extras.add(token.lower())
    return AGENT_PR_AUTHORS | frozenset(extras)


@dataclass(frozen=True)
class PrSnapshot:
    """One open PR's merge-relevant state, read in a single scan pass."""

    pr_id: str
    number: int
    author: str
    is_draft: bool
    head_oid: str
    checks_state: str | None  # statusCheckRollup.state; None = missing/unreadable
    base_behind: int | None  # commits PR head is behind base tip; None = unreadable (fail closed)
    unresolved_threads: int | None  # None = thread read failed (fail closed)
    readiness_stage: int | None  # parsed clearance readiness stage
    readiness_head: str | None  # head SHA the readiness comment was computed for
    base_ref: str  # baseRefName; "" if missing (fail closed against ALLOWED_BASE_REFS)
    review_decision: str | None  # GraphQL reviewDecision; None if missing (fail closed)


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
    repos_enumerated: int = 0

    @property
    def merged(self) -> int:
        return sum(1 for d in self.decisions if d.action == "merged")

    @property
    def would_merge(self) -> int:
        return sum(1 for d in self.decisions if d.action == "would_merge")

    @property
    def systemic_failure(self) -> bool:
        """The whole scan failed at some scope (likely a global auth/config fault),
        so the caller should fail rather than report a clean zero-candidate run."""
        if not self.repos_scanned:
            return False
        return self.repos_enumerated == 0

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
            "systemic_failure": self.systemic_failure,
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


def should_merge(s: PrSnapshot, *, allowed_authors: frozenset[str] | None = None) -> str:
    """Deterministic merge predicate. Returns "ok" or a stable skip reason:
    not_agent_author | base_not_allowed | not_approved | draft |
    checks_not_green | base_freshness_unreadable | base_stale |
    threads_unreadable | threads_unresolved | readiness_missing |
    readiness_not_ready | readiness_stale_head.

    *allowed_authors* is the built-in agent author plus operator-local
    extras (VOYAGER_MERGE_EXTRA_AUTHORS); None resolves merge_allowed_authors()
    at call time. Matching is case-insensitive against s.author.

    Order matters only for reporting; every condition is independently
    fail-closed. Stage >= REQUIRED_READINESS_STAGE accepts both
    "3 - Ready for approval" and "4 - Ready for merge". base_behind guards
    against a rebase merge landing on a base (main) that advanced past the
    commit the head's checks_state was computed against — expectedHeadOid
    only pins the PR head, not the base.

    not_approved (operator design reversal, VOY-1839 §Merge predicate):
    zero-touch is retired — every target repo's ruleset now requires an
    approving review, and this loop only merges a PR the operator has
    actually approved. Mirrors GitHub's own semantics: gate on GraphQL
    `reviewDecision == "APPROVED"` on the PR as a whole, not on any single
    review event, so an approval survives a later push as long as the
    repo's ruleset has "dismiss stale reviews" off — that knob lives in the
    target repo's ruleset (VOY-1840), not in this loop. review_decision is
    None (missing), "REVIEW_REQUIRED", and "CHANGES_REQUESTED" all fail
    closed to not_approved alike — including a repo with NO required-review
    ruleset configured, whose reviewDecision reads null: such a repo is
    deliberately unmergeable by this loop until its ruleset requires at
    least one approving review.

    "base_moved_by_merge", "base_stale_at_apply", and "base_retargeted_at_apply"
    are NOT return values of this function — all three are orchestration-level
    skips applied by run_merge_loop after this predicate already said "ok".
    "base_moved_by_merge" fires for a second PR in the same repo after an
    earlier merge in the same run advanced main out from under this PR's
    cached base_behind read. "base_stale_at_apply" fires when run_merge_loop
    re-reads base_behind immediately before merging (closing the
    snapshot->mutation race window) and finds main has advanced since the
    snapshot in this function's base_stale check above. "base_retargeted_at_apply"
    (P2 round 14) fires when that same apply-time re-read finds the PR's
    baseRefName no longer matches the snapshot's base_ref (or is no longer
    in ALLOWED_BASE_REFS) — a retarget after the snapshot, which
    expectedHeadOid alone cannot guard against.
    """
    allowed = allowed_authors if allowed_authors is not None else merge_allowed_authors()
    if s.author.lower() not in allowed:
        return "not_agent_author"
    if s.base_ref not in ALLOWED_BASE_REFS:
        return "base_not_allowed"
    if s.review_decision != "APPROVED":
        return "not_approved"
    if s.is_draft:
        return "draft"
    if s.checks_state != "SUCCESS":
        return "checks_not_green"
    if s.base_behind is None:
        return "base_freshness_unreadable"
    if s.base_behind > 0:
        return "base_stale"
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
        baseRefName
        reviewDecision
        author { login }
        commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
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

# Issue comments, paginated. The clearance bot UPSERTS its readiness comment in
# place, so on a busy PR that comment can sit on an early page while dozens of
# later comments arrive after it — a fixed-size window (e.g. `comments(last:
# 50)` on the PR node) would eventually push it out and permanently skip a
# green PR. This query is read to exhaustion instead.
_PR_COMMENTS_QUERY = """
query PrComments($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { author { login __typename } body }
      }
    }
  }
}
"""

# behindBy = commits the PR head is behind the base tip. prHeadRef must be the
# synthetic refs/pull/<number>/head ref in the BASE repo, not a plain branch
# name — that also works for fork PRs, whose head branch doesn't exist here.
_BASE_FRESHNESS_QUERY = """
query($owner: String!, $name: String!, $baseRef: String!, $prHeadRef: String!) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $baseRef) {
      compare(headRef: $prHeadRef) {
        behindBy
      }
    }
  }
}
"""

# Apply-time retarget + approval-revocation guard (P2 round 14; approval gate
# added when the operator reversed zero-touch, VOY-1839): a PR's base branch
# can be changed, and its reviewDecision can flip from APPROVED, after the
# snapshot/apply-time freshness reads but before mergePullRequest.
# expectedHeadOid pins only the head, so this is the only read that catches
# either a retarget onto a base outside ALLOWED_BASE_REFS or an approval
# revoked after the snapshot.
_PR_BASE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      baseRefName
      reviewDecision
    }
  }
}
"""

_ALLOWED_MERGE_READ_QUERIES = frozenset(
    {
        _AGENT_PR_PAGE_QUERY,
        _PR_THREADS_QUERY,
        _PR_COMMENTS_QUERY,
        _BASE_FRESHNESS_QUERY,
        _PR_BASE_QUERY,
    }
)


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
            # Truncated: more pages exist but there's no cursor to reach them.
            # A partial count would fail OPEN (an unread page could hold
            # unresolved threads) — fail closed instead.
            return None
        seen_cursors.add(after)


def _base_behind_by(gql: GqlFn, repo: str, base_ref: str, number: int) -> int | None:
    """Commits the PR head is behind the base branch tip; None on any read
    fault, a null ref/compare, or a non-int behindBy (fail closed) — a base
    that advanced since checks ran must never read as fresh."""
    owner, name = repo.split("/", 1)
    try:
        data = gql(
            _BASE_FRESHNESS_QUERY,
            {
                "owner": owner,
                "name": name,
                "baseRef": base_ref,
                "prHeadRef": f"refs/pull/{number}/head",
            },
        )
    except ResolveConversationError:
        return None
    ref = ((data or {}).get("repository") or {}).get("ref")
    if ref is None:
        return None
    compare = ref.get("compare")
    if compare is None:
        return None
    behind_by = compare.get("behindBy")
    return behind_by if isinstance(behind_by, int) else None


def _apply_time_pr_state(gql: GqlFn, repo: str, number: int) -> tuple[str | None, str | None]:
    """This PR's current (baseRefName, reviewDecision), read together in one
    round trip since both live on the same _PR_BASE_QUERY node. Each field
    fails closed to None independently — on a read fault or a missing
    pullRequest both come back None; a present-but-wrong-typed field (a
    non-str baseRefName or a non-str reviewDecision) fails closed only for
    that field. A PR retargeted, or an approval revoked, since the snapshot
    must never read as unchanged.

    One combined helper rather than two parallel ones deliberately avoids a
    second GraphQL round trip for what the API already returns from a
    single node read.
    """
    owner, name = repo.split("/", 1)
    try:
        data = gql(_PR_BASE_QUERY, {"owner": owner, "name": name, "number": number})
    except ResolveConversationError:
        return None, None
    pull = ((data or {}).get("repository") or {}).get("pullRequest")
    if pull is None:
        return None, None
    base_ref = pull.get("baseRefName")
    review_decision = pull.get("reviewDecision")
    return (
        base_ref if isinstance(base_ref, str) else None,
        review_decision if isinstance(review_decision, str) else None,
    )


def _is_clearance_bot(author: dict[str, Any] | None) -> bool:
    """True only for the clearance GitHub App's actual bot actor.

    Spoof scenario (round-8 finding): a plain USER account named
    "iterwheel-clearance" -- or a duplicate User-authored comment with a
    valid marker body -- could impersonate the app's login. GitHubApp.
    upsert_issue_comment() (voyager/core/github_app.py ~L816-846) only ever
    PATCHes the comment whose REST author.login is "{app_slug}[bot]"; it
    would never touch such a spoofed comment again. If _readiness_for_pr's
    first-match scan trusted login alone, a spoof sitting before the real
    marker would win permanently.

    REST vs GraphQL login representation differs: REST renders a GitHub
    App's login with a "[bot]" suffix, but GraphQL's `author.login` for the
    same actor is the PLAIN slug -- the suffix is a REST-only rendering, not
    part of the actor's identity. So login string matching alone (in either
    representation) cannot distinguish the bot from a same-named User; the
    GraphQL `__typename` field (== "Bot") is what actually says "this is an
    app-owned actor, not a human/User account". Fail closed when either
    field is missing.
    """
    if not isinstance(author, dict):
        return False
    if author.get("__typename") != "Bot":
        return False
    login = str(author.get("login") or "")
    return login.removesuffix("[bot]") == CLEARANCE_APP_SLUG


def _readiness_for_pr(gql: GqlFn, repo: str, number: int) -> tuple[int | None, str | None]:
    """Paginated FIRST clearance-authored readiness comment; (None, None) on any
    read fault (fail closed) — mirrors _unresolved_thread_count's cursor-repeat
    guard so a stuck/repeating cursor cannot spin forever.

    Reads the FIRST matching comment, not the last, to mirror
    GitHubApp.upsert_issue_comment() (voyager/core/github_app.py ~L816-846):
    it scans issue_comments() oldest-first and PATCHes the first comment
    where the marker is in the body AND the author is the bot — that
    specific comment is the only one it ever updates again. With duplicate
    markers (plausible from older non-paginated upsert behavior that could
    create a second comment instead of finding the existing one), a stale
    later duplicate must not outvote the comment the writer actually keeps
    current. If the first marker comment fails to parse (stage/head
    missing), fail closed rather than falling through to a later duplicate.
    """
    owner, name = repo.split("/", 1)
    after: str | None = None
    seen_cursors: set[str] = set()
    while True:
        try:
            data = gql(
                _PR_COMMENTS_QUERY,
                {"owner": owner, "name": name, "number": number, "after": after},
            )
        except ResolveConversationError:
            return None, None
        pull = ((data or {}).get("repository") or {}).get("pullRequest")
        if pull is None:
            return None, None
        comments = pull.get("comments") or {}
        for c in comments.get("nodes") or []:
            if not _is_clearance_bot(c.get("author")):
                continue
            body = c.get("body") or ""
            if CLEARANCE_COMMENT_MARKER not in body:
                continue
            # First clearance-authored marker comment — the one upsert
            # keeps current. Parse it or fail closed; never consider a
            # later duplicate.
            parsed = parse_readiness(body)
            if parsed is None:
                return None, None
            return parsed
        page = comments.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return None, None
        after = page.get("endCursor")
        if not after or after in seen_cursors:
            # Truncated: more pages exist but there's no cursor to reach them.
            # A best-effort partial scan could miss the clearance bot's latest
            # upsert (or worse, report a stale stage as fresh) — fail closed.
            return None, None
        seen_cursors.add(after)


def snapshots_for_repo(
    gql: GqlFn, repo: str, *, allowed_authors: frozenset[str] | None = None
) -> list[PrSnapshot]:
    """Every open PR's merge-relevant state; thread counts fetched lazily.

    *allowed_authors* gates the cheap_green check (whether the expensive
    per-PR reads below run at all); None resolves merge_allowed_authors() at
    call time. Matching is case-insensitive.
    """
    allowed = allowed_authors if allowed_authors is not None else merge_allowed_authors()
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
            base_ref = str(node.get("baseRefName") or "")
            review_decision = node.get("reviewDecision")
            rollup_nodes = ((node.get("commits") or {}).get("nodes")) or [{}]
            rollup = ((rollup_nodes[0].get("commit") or {}).get("statusCheckRollup")) or {}
            checks_state = rollup.get("state")
            cheap_green = author.lower() in allowed and not is_draft and checks_state == "SUCCESS"
            base_behind = _base_behind_by(gql, repo, base_ref, number) if cheap_green else None
            threads = _unresolved_thread_count(gql, repo, number) if cheap_green else None
            stage, r_head = _readiness_for_pr(gql, repo, number) if cheap_green else (None, None)
            snapshots.append(
                PrSnapshot(
                    pr_id=str(node.get("id") or ""),
                    number=number,
                    author=author,
                    is_draft=is_draft,
                    head_oid=head_oid,
                    checks_state=checks_state,
                    base_behind=base_behind,
                    unresolved_threads=threads,
                    readiness_stage=stage,
                    readiness_head=r_head,
                    base_ref=base_ref,
                    review_decision=review_decision,
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
    data = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # os.write can return fewer bytes than requested (e.g. ENOSPC mid
        # write). A short count is not a failure on a blocking fd — os.write
        # raises OSError when it truly can't write — so loop until every
        # byte lands; any OSError from a later call propagates as-is to the
        # caller's write-ahead-audit fail-closed path.
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
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
    identity_gql: GqlFn,
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

    Every snapshot's base_behind in a repo is read before this loop makes
    any mutation, so once a merge succeeds in a repo, main has moved out
    from under every other cached snapshot in that repo. At most one PR is
    merged per repo per run: every remaining agent PR in that repo that
    would otherwise reach the mutation is instead recorded as
    ("skipped", "base_moved_by_merge") — an orchestration-level reason,
    not one should_merge returns — and left for the next cycle's rescan.
    Suppressed PRs never consume a cap slot. dry-run is unaffected (no
    mutation ever moves the base); a merge_failed outcome does not move
    the base either and must not suppress later candidates.

    LIVE path only: immediately before the write-ahead intent + merge_pr
    call, baseRefName AND reviewDecision are re-read together in one round
    trip (_apply_time_pr_state), then base_behind is re-read separately
    (_base_behind_by) — rather than trusting the snapshot taken earlier in
    this same repo's scan. This narrows the snapshot->mutation window
    (during which main could advance from a human push, a release bot, or
    another loop instance; the PR could be retargeted to a different base;
    or the operator's approval could be revoked) from the length of a full
    repo scan down to seconds. The residual race is accepted: the
    mergePullRequest mutation has no expectedBaseOid and no base-ref or
    approval guard at all, only expectedHeadOid, so no read-then-mutate
    window can be closed to zero from this side alone; GitHub's "require
    branches up to date" branch protection and the required-approving-review
    ruleset (VOY-1840), when enabled, are the server-side backstop. A None
    baseRefName re-read records ("skipped", "base_freshness_unreadable")
    (reused — it is a base-state read fault like the base_behind one); a
    re-read that no longer matches the snapshot's base_ref, or that isn't in
    ALLOWED_BASE_REFS, records ("skipped", "base_retargeted_at_apply")
    (P2 round 14) — checked BEFORE the reviewDecision recheck, which is in
    turn checked BEFORE the base_behind re-read. A reviewDecision re-read
    that is no longer "APPROVED" records ("skipped",
    "approval_revoked_at_apply") — the apply-time counterpart of
    should_merge's snapshot-time "not_approved" guard, covering an
    approve-then-revoke race in the window between snapshot and apply. A
    None base_behind re-read also records ("skipped",
    "base_freshness_unreadable"); a positive re-read records ("skipped",
    "base_stale_at_apply") — distinct from should_merge's snapshot-time
    "base_stale". None of these apply-time skips write an intent audit line
    or consume a cap slot. dry-run never mutates, so it skips all of these
    re-reads entirely and reports the snapshot value as-is.

    *identity_gql* asserts the fixed machine identity (mirrors
    run_resolve_loop) BEFORE any repo is scanned — including in dry-run,
    since a wrong credential must abort there too, not only on live merges.

    merge_allowed_authors() is resolved ONCE here and threaded through to
    both snapshots_for_repo and should_merge for every repo in this run —
    mirroring the repo ceiling: an env change mid-run must not split the
    run's view of which authors are eligible.

    Gates applied per PR, in should_merge's order: author allowlist, base
    ref allowlist, human approval (reviewDecision == "APPROVED" — the
    operator's own end-state: approve once, and the loop completes the
    merge), not-draft, CI green, base freshness, unresolved threads,
    clearance readiness. Orchestration-level gates layered on top by this
    function: per-run cap, same-repo same-cycle base movement, and the
    apply-time rechecks documented above.
    """
    timestamp = (now or _utc_now)()
    allowed, skipped = gate_repos(requested_repos, ceiling=merge_allowed_repos())
    allowed_authors = merge_allowed_authors()
    _assert_machine_identity(identity_gql)
    decisions: list[MergeDecision] = []
    errors: list[tuple[str, str]] = []
    prs_scanned = 0
    repos_enumerated = 0
    approved = 0
    capped = False

    def _record(decision: MergeDecision) -> None:
        decisions.append(decision)
        if audit_path is not None:
            _append_merge_audit(
                audit_path, {"ts": timestamp, "dry_run": dry_run, **decision.public()}
            )

    for repo in allowed:
        try:
            snapshots = snapshots_for_repo(read_gql, repo, allowed_authors=allowed_authors)
        except ResolveConversationError as exc:
            errors.append((repo, str(exc)))
            continue
        repos_enumerated += 1
        prs_scanned += len(snapshots)
        # base_behind on every snapshot in this repo was read BEFORE this
        # loop's mutations. A successful merge moves main out from under
        # every other cached snapshot in the same repo, so once one lands,
        # the rest are stale reads and must wait for the next cycle's rescan
        # rather than rebase-merge onto an untested base.
        merged_in_repo = False
        for s in snapshots:
            if s.author.lower() not in allowed_authors:
                continue  # never touched, never listed
            reason = should_merge(s, allowed_authors=allowed_authors)
            if reason != "ok":
                _record(MergeDecision(repo, s.number, "skipped", reason))
                continue
            if approved >= max_merges:
                capped = True
                _record(MergeDecision(repo, s.number, "skipped", "capped"))
                continue
            if merged_in_repo:
                _record(MergeDecision(repo, s.number, "skipped", "base_moved_by_merge"))
                continue
            if not dry_run:
                # Apply-time re-read (P2 round 9, extended round 14; approval
                # recheck added with the operator's zero-touch reversal,
                # VOY-1839): base_behind, baseRefName, AND reviewDecision on
                # this snapshot were all read during snapshots_for_repo, but
                # any of them can change between that read and the mutation
                # below (a human push, a release bot, another loop instance,
                # a PR retarget, or an approve-then-revoke race).
                # expectedHeadOid only pins the PR head; the merge mutation
                # has no expectedBaseOid and no base-ref or approval guard at
                # all, so a PR retargeted outside ALLOWED_BASE_REFS, or one
                # whose approval was pulled, would otherwise merge undetected
                # after the snapshot. Re-read baseRefName and reviewDecision
                # FIRST (one round trip) — before trusting a base_behind
                # compare against a base that may no longer be the one
                # should_merge validated. All re-reads sit immediately before
                # the write-ahead intent + merge_pr call, before
                # approved += 1, so a skip here consumes no cap slot.
                # dry-run never mutates, so it skips all re-reads entirely
                # and reports the snapshot value.
                current_base, current_review_decision = _apply_time_pr_state(
                    read_gql, repo, s.number
                )
                if current_base is None:
                    _record(MergeDecision(repo, s.number, "skipped", "base_freshness_unreadable"))
                    continue
                if current_base != s.base_ref or current_base not in ALLOWED_BASE_REFS:
                    _record(MergeDecision(repo, s.number, "skipped", "base_retargeted_at_apply"))
                    continue
                if current_review_decision != "APPROVED":
                    _record(MergeDecision(repo, s.number, "skipped", "approval_revoked_at_apply"))
                    continue
                fresh = _base_behind_by(read_gql, repo, current_base, s.number)
                if fresh is None:
                    _record(MergeDecision(repo, s.number, "skipped", "base_freshness_unreadable"))
                    continue
                if fresh > 0:
                    _record(MergeDecision(repo, s.number, "skipped", "base_stale_at_apply"))
                    continue
            approved += 1
            if dry_run:
                _record(MergeDecision(repo, s.number, "would_merge"))
                continue
            if audit_path is not None:
                # Write-ahead intent: if the audit sink can't be written, abort
                # BEFORE mutating GitHub — no unattended merge without a trail.
                intent = MergeDecision(repo, s.number, "merge_intent")
                try:
                    _append_merge_audit(
                        audit_path, {"ts": timestamp, "dry_run": dry_run, **intent.public()}
                    )
                except OSError:
                    # In-memory only: _record would retry the same broken sink
                    # and raise again. Fail closed without merging.
                    decisions.append(MergeDecision(repo, s.number, "skipped", "audit_unwritable"))
                    continue
            action, message = merge_pr(merge_gql, s.pr_id, s.head_oid)
            _record(MergeDecision(repo, s.number, action, message))
            if action == "merged":
                merged_in_repo = True

    return MergeLoopSummary(
        repos_scanned=tuple(allowed),
        repos_skipped=tuple(skipped),
        prs_scanned=prs_scanned,
        decisions=tuple(decisions),
        capped=capped,
        dry_run=dry_run,
        errors=tuple(errors),
        repos_enumerated=repos_enumerated,
    )
