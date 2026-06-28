"""
resolve_conversation — resolve GitHub PR review conversations as a fixed machine account.

Hard constraints (enforced by construction):

1. The ONLY GraphQL mutation this module may ever issue is ``resolveReviewThread``.
   No code path merges, closes, comments, or issues any other mutation.

2. Identity is fixed to MACHINE_ACCOUNT = "iterwheel-countdown-user". The token is
   obtained via ``gh auth token --user iterwheel-countdown-user`` and is never logged,
   printed, or exposed in exceptions. The global gh active account is never modified.

3. Only repos in RESOLVE_ALLOWED_REPOS may be targeted; any other repo raises
   ResolveConversationError before any network call is made.

4. Non-sandbox repos must not surface raw PR numbers or thread node IDs in public
   output; _RAW_IDENTIFIER_REPOS controls which repos may include raw identifiers
   in to_public_dict().
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

# gh treats these as the github.com token and lets them override the stored
# credential, so they must be stripped before reading the machine account token.
_AMBIENT_TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")

MACHINE_ACCOUNT: str = "iterwheel-countdown-user"
RESOLVE_ALLOWED_REPOS: frozenset[str] = frozenset(
    {"iterwheel/voyager", "iterwheel/voyager-sandbox"}
)
_RAW_IDENTIFIER_REPOS: frozenset[str] = frozenset({"iterwheel/voyager-sandbox"})

GraphQLFn = Callable[[str, dict[str, Any]], dict[str, Any]]


class ResolveConversationError(RuntimeError):
    """Raised on any resolution failure: auth, allowlist, GraphQL errors, etc."""


@dataclass(frozen=True)
class ThreadState:
    thread_id: str
    is_resolved: bool | None
    viewer_can_resolve: bool | None
    viewer_can_reply: bool | None
    is_outdated: bool | None


@dataclass(frozen=True)
class ResolveSummary:
    repo: str
    pr: int | None
    resolved: int
    skipped: int
    dry_run: bool
    details: tuple[tuple[str, str], ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        show_raw = self.repo in _RAW_IDENTIFIER_REPOS
        if show_raw:
            threads: list[dict[str, Any]] = [
                {"thread_id": tid, "action": action} for tid, action in self.details
            ]
        else:
            threads = [{"action": action, "redacted": True} for _, action in self.details]
        return {
            "repo": self.repo,
            "pr": self.pr if show_raw else None,
            "resolved": self.resolved,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
            "threads": threads,
        }


def _default_client_factory() -> httpx.Client:
    return httpx.Client(timeout=20)


def read_machine_token(run: Callable[..., Any] = subprocess.run) -> str:
    """Obtain the fixed machine account token via gh CLI.

    Uses getattr defensively so test doubles can be plain objects.
    Never logs or re-raises the token value.
    """
    # Strip ambient GH_TOKEN/GITHUB_TOKEN: gh would otherwise return that token
    # (the owner could be anyone — e.g. a CI bot or the human) instead of the
    # stored iterwheel-countdown-user credential, silently resolving as the
    # wrong identity. Belt to the viewer-login check in resolve_conversations.
    scrubbed_env = {k: v for k, v in os.environ.items() if k not in _AMBIENT_TOKEN_ENV}
    try:
        proc = run(
            # Pin --hostname github.com: the GraphQL client always posts to
            # api.github.com, so reading the token from a GH_HOST-configured
            # Enterprise credential store would resolve against the wrong host.
            ["gh", "auth", "token", "--hostname", "github.com", "--user", MACHINE_ACCOUNT],
            capture_output=True,
            text=True,
            timeout=30,
            env=scrubbed_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Never chain (from None): the original may carry argv/env; type name only.
        raise ResolveConversationError(
            f"gh auth token invocation failed: {type(exc).__name__}"
        ) from None
    if getattr(proc, "returncode", 1) != 0:
        raise ResolveConversationError(
            f"gh auth token exited with returncode={getattr(proc, 'returncode', '?')}"
        )
    token = (getattr(proc, "stdout", "") or "").strip()
    if not token:
        raise ResolveConversationError("gh auth token returned an empty token")
    return token


def make_github_gql(
    token: str,
    *,
    client_factory: Callable[[], httpx.Client] = _default_client_factory,
) -> GraphQLFn:
    """Return a callable that executes a GraphQL query against the GitHub API.

    The token is captured in a closure and never logged or surfaced in exceptions.
    Errors are reported by count only, never by payload.
    """

    def _gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Defense-in-depth: only the three known operations may ever be sent.
        if query not in _ALLOWED_OPERATIONS:
            raise ResolveConversationError("refusing to issue an unknown GraphQL operation")
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
            # from None: drop the request/response object so no header/body leaks.
            raise ResolveConversationError(f"GraphQL HTTP {exc.response.status_code}") from None
        except httpx.HTTPError:
            raise ResolveConversationError("GraphQL request failed") from None
        except ValueError:
            raise ResolveConversationError("GraphQL returned a non-JSON response") from None
        errors = body.get("errors")
        if errors:
            raise ResolveConversationError(f"GraphQL returned {len(errors)} error(s)")
        return body.get("data") or {}

    return _gql


def _should_resolve(t: ThreadState) -> bool:
    """Return True only when ALL conditions pass; fail-closed on None.

    NOTE: outdated threads ARE resolvable. ``viewerCanResolve`` is the authorization
    boundary; ``isOutdated`` only means the anchored line moved (typically because the
    code WAS changed — often the very fix), so excluding it just stranded addressed
    findings. It is intentionally not a gate.
    """
    return t.is_resolved is False and t.viewer_can_resolve is True and t.viewer_can_reply is True


def _parse_thread_node(node: dict[str, Any]) -> ThreadState:
    return ThreadState(
        thread_id=node.get("id") or "",
        is_resolved=node.get("isResolved"),
        viewer_can_resolve=node.get("viewerCanResolve"),
        viewer_can_reply=node.get("viewerCanReply"),
        is_outdated=node.get("isOutdated"),
    )


_RESOLVE_MUTATION = """
mutation ResolveThread($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread {
      isResolved
    }
  }
}
"""

_NODE_QUERY = """
query GetThread($threadId: ID!) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      id
      isResolved
      viewerCanResolve
      viewerCanReply
      isOutdated
      pullRequest {
        repository {
          nameWithOwner
        }
      }
    }
  }
}
"""

_PR_THREADS_QUERY = """
query GetReviewThreads($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          viewerCanResolve
          viewerCanReply
          isOutdated
        }
      }
    }
  }
}
"""

_VIEWER_QUERY = """
query Viewer {
  viewer {
    login
  }
}
"""

_ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {_RESOLVE_MUTATION, _NODE_QUERY, _PR_THREADS_QUERY, _VIEWER_QUERY}
)


def _assert_machine_identity(gql: GraphQLFn) -> None:
    """Fail closed unless the authenticated viewer IS the machine account.

    Last line of defense for the 'never resolve as the human identity'
    guarantee: even if the wrong token slipped through env scrubbing, no
    mutation fires unless the live viewer login matches MACHINE_ACCOUNT.
    """
    login = ((gql(_VIEWER_QUERY, {}).get("viewer") or {}).get("login")) or ""
    if login != MACHINE_ACCOUNT:
        raise ResolveConversationError(
            f"authenticated identity is not {MACHINE_ACCOUNT!r}; refusing to resolve"
        )


def _node_repo(node: dict[str, Any]) -> str | None:
    """Owning repo (nameWithOwner) of a review-thread node, if present."""
    repo = ((node.get("pullRequest") or {}).get("repository") or {}).get("nameWithOwner")
    return repo if isinstance(repo, str) and repo else None


def _apply_thread(
    ts: ThreadState,
    *,
    dry_run: bool,
    gql: GraphQLFn,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Process one thread. Returns (resolved_delta, skipped_delta, new_detail_pairs)."""
    if not ts.thread_id:
        return 0, 1, [("", "skipped_invalid_node")]
    if not _should_resolve(ts):
        return 0, 1, [(ts.thread_id, "skipped_guard")]
    if dry_run:
        return 1, 0, [(ts.thread_id, "would_resolve")]
    data = gql(_RESOLVE_MUTATION, {"threadId": ts.thread_id})
    is_resolved = (data.get("resolveReviewThread") or {}).get("thread", {}).get("isResolved")
    if is_resolved is True:
        return 1, 0, [(ts.thread_id, "resolved")]
    return 0, 1, [(ts.thread_id, "verify_failed")]


def resolve_conversations(
    *,
    repo: str,
    pr: int | None = None,
    thread_id: str | None = None,
    dry_run: bool = False,
    gql: GraphQLFn,
) -> ResolveSummary:
    """Resolve review threads in *repo* via the fixed machine account.

    Exactly one of *pr* or *thread_id* must be supplied.
    Raises ResolveConversationError for allowlist violations, argument errors,
    auth failures, and GraphQL errors.
    """
    if repo not in RESOLVE_ALLOWED_REPOS:
        raise ResolveConversationError(
            f"Repo {repo!r} is not in the allowlist ({sorted(RESOLVE_ALLOWED_REPOS)})"
        )
    if (pr is None) == (thread_id is None):
        raise ResolveConversationError(
            "exactly one of pr or thread_id must be provided, not both or neither"
        )

    # Hard identity gate before any read/mutation: refuse unless the live token
    # actually belongs to the machine account.
    _assert_machine_identity(gql)

    resolved = 0
    skipped = 0
    details: list[tuple[str, str]] = []

    if thread_id is not None:
        if not thread_id:
            skipped += 1
            details.append(("", "skipped_empty_thread_id"))
        else:
            node = gql(_NODE_QUERY, {"threadId": thread_id}).get("node") or {}
            ts = _parse_thread_node(node)
            if not ts.thread_id:
                # null node / non-review-thread node = mistyped or inaccessible id.
                # For one explicit target, fail loud instead of exiting "skipped:1".
                # No id in the message (VOY-1828): node IDs are sensitive.
                raise ResolveConversationError(
                    "requested review thread not found or not accessible"
                )
            # Node IDs are global: a thread from another repo would otherwise pass
            # the allowlist on the user-supplied `repo` alone. Verify it belongs here.
            owning = _node_repo(node)
            if owning is not None and owning != repo:
                raise ResolveConversationError(
                    f"thread does not belong to {repo!r} (allowlist bypass blocked)"
                )
            r, s, d = _apply_thread(ts, dry_run=dry_run, gql=gql)
            resolved += r
            skipped += s
            details.extend(d)
    else:
        assert pr is not None  # narrowed: exactly one of pr/thread_id is set
        owner, name = repo.split("/", 1)
        cursor: str | None = None
        seen_cursors: set[str] = set()
        processed: set[str] = set()
        while True:
            data = gql(
                _PR_THREADS_QUERY,
                {"owner": owner, "name": name, "number": pr, "cursor": cursor},
            )
            pull_request = (data.get("repository") or {}).get("pullRequest")
            if pull_request is None:
                # null pullRequest with no GraphQL error = wrong PR number; fail loud
                # instead of silently reporting "nothing to resolve". The PR number is
                # omitted from the message to honor the non-sandbox redaction rule
                # (VOY-1828) — the CLI prints ResolveConversationError text verbatim.
                raise ResolveConversationError(f"requested pull request not found in {repo!r}")
            review_threads: dict[str, Any] = pull_request.get("reviewThreads") or {}
            page_info: dict[str, Any] = review_threads.get("pageInfo") or {}
            nodes: list[dict[str, Any]] = review_threads.get("nodes") or []

            for node in nodes:
                ts = _parse_thread_node(node)
                if ts.thread_id and ts.thread_id in processed:
                    continue  # dedup across overlapping/repeated pages
                if ts.thread_id:
                    processed.add(ts.thread_id)
                r, s, d = _apply_thread(ts, dry_run=dry_run, gql=gql)
                resolved += r
                skipped += s
                details.extend(d)

            if not page_info.get("hasNextPage"):
                break
            end_cursor: str | None = page_info.get("endCursor")
            if end_cursor is None:
                raise ResolveConversationError(
                    "pagination invariant violated: hasNextPage with null endCursor"
                )
            if end_cursor in seen_cursors:
                break  # cursor cycle: stop rather than loop forever
            seen_cursors.add(end_cursor)
            cursor = end_cursor

    return ResolveSummary(
        repo=repo,
        pr=pr,
        resolved=resolved,
        skipped=skipped,
        dry_run=dry_run,
        details=tuple(details),
    )
