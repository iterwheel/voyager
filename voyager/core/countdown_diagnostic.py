from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from voyager.core.github_app import GitHubAppClient, GitHubGraphQLError

COUNTDOWN_AGENT_SLUG = "iterwheel-countdown"

_THREAD_CAPABILITY_QUERY = """
query ReviewThreadCapabilities($threadIds: [ID!]!) {
  viewer {
    login
  }
  nodes(ids: $threadIds) {
    __typename
    ... on PullRequestReviewThread {
      id
      isResolved
      isOutdated
      viewerCanResolve
      viewerCanReply
      pullRequest {
        number
        repository {
          nameWithOwner
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class ReviewThreadCapability:
    thread_id: str
    type_name: str | None
    repository: str | None
    pr: int | None
    is_resolved: bool | None
    is_outdated: bool | None
    viewer_can_resolve: bool | None
    viewer_can_reply: bool | None
    error: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "type": self.type_name,
            "repo": self.repository,
            "pr": self.pr,
            "isResolved": self.is_resolved,
            "isOutdated": self.is_outdated,
            "viewerCanResolve": self.viewer_can_resolve,
            "viewerCanReply": self.viewer_can_reply,
            "error": self.error,
        }


@dataclass(frozen=True)
class ReviewThreadCapabilityReport:
    app_slug: str
    actor_login: str
    repository: str
    pr: int
    threads: tuple[ReviewThreadCapability, ...]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "app_slug": self.app_slug,
            "actor_login": self.actor_login,
            "repo": self.repository,
            "pr": self.pr,
            "threads": [thread.to_public_dict() for thread in self.threads],
        }


@dataclass(frozen=True)
class ReviewThreadResolveOperation:
    thread_id: str
    applied: bool
    reason: str | None
    resolved_by: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "applied": self.applied,
            "reason": self.reason,
            "resolvedBy": self.resolved_by,
        }


@dataclass(frozen=True)
class ReviewThreadResolveCanaryReport:
    before: ReviewThreadCapabilityReport
    operations: tuple[ReviewThreadResolveOperation, ...]
    after: ReviewThreadCapabilityReport

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "before": self.before.to_public_dict(),
            "operations": [operation.to_public_dict() for operation in self.operations],
            "after": self.after.to_public_dict(),
        }


def _thread_capability_from_node(
    requested_thread_id: str,
    node: dict[str, Any] | None,
) -> ReviewThreadCapability:
    if node is None:
        return ReviewThreadCapability(
            thread_id=requested_thread_id,
            type_name=None,
            repository=None,
            pr=None,
            is_resolved=None,
            is_outdated=None,
            viewer_can_resolve=None,
            viewer_can_reply=None,
            error="not_found_or_not_visible_to_app",
        )

    type_name = str(node.get("__typename") or "")
    if type_name != "PullRequestReviewThread":
        return ReviewThreadCapability(
            thread_id=requested_thread_id,
            type_name=type_name or None,
            repository=None,
            pr=None,
            is_resolved=None,
            is_outdated=None,
            viewer_can_resolve=None,
            viewer_can_reply=None,
            error="not_a_pull_request_review_thread",
        )

    pull_request = node.get("pullRequest") or {}
    repository = (pull_request.get("repository") or {}).get("nameWithOwner")
    number = pull_request.get("number")
    return ReviewThreadCapability(
        thread_id=str(node.get("id") or requested_thread_id),
        type_name=type_name,
        repository=str(repository) if repository else None,
        pr=number if isinstance(number, int) else None,
        is_resolved=node.get("isResolved") if isinstance(node.get("isResolved"), bool) else None,
        is_outdated=node.get("isOutdated") if isinstance(node.get("isOutdated"), bool) else None,
        viewer_can_resolve=(
            node.get("viewerCanResolve") if isinstance(node.get("viewerCanResolve"), bool) else None
        ),
        viewer_can_reply=(
            node.get("viewerCanReply") if isinstance(node.get("viewerCanReply"), bool) else None
        ),
        error=None,
    )


async def query_review_thread_capabilities(
    client: GitHubAppClient,
    *,
    app_slug: str = COUNTDOWN_AGENT_SLUG,
    repository: str,
    pr: int,
    thread_ids: list[str],
) -> ReviewThreadCapabilityReport:
    if not thread_ids:
        raise ValueError("at least one review thread ID is required")

    data = await client.graphql(
        app_slug,
        repository,
        query=_THREAD_CAPABILITY_QUERY,
        variables={"threadIds": thread_ids},
    )
    actor_login = str((((data or {}).get("viewer") or {}).get("login")) or f"{app_slug}[bot]")
    nodes = list((data or {}).get("nodes") or [])
    if len(nodes) < len(thread_ids):
        nodes.extend([None] * (len(thread_ids) - len(nodes)))
    nodes = nodes[: len(thread_ids)]

    return ReviewThreadCapabilityReport(
        app_slug=app_slug,
        actor_login=actor_login,
        repository=repository,
        pr=pr,
        threads=tuple(
            _thread_capability_from_node(thread_id, node if isinstance(node, dict) else None)
            for thread_id, node in zip(thread_ids, nodes, strict=True)
        ),
    )


def _skip_reason(
    thread: ReviewThreadCapability,
    *,
    repository: str,
    pr: int,
) -> str | None:
    if thread.error:
        return thread.error
    if thread.repository != repository or thread.pr != pr:
        return "thread_does_not_belong_to_target_pr"
    if thread.is_resolved is True:
        return "already_resolved"
    if thread.viewer_can_resolve is not True:
        return "viewerCanResolve is false"
    return None


async def run_review_thread_resolve_canary(
    client: GitHubAppClient,
    *,
    app_slug: str = COUNTDOWN_AGENT_SLUG,
    repository: str,
    pr: int,
    thread_ids: list[str],
) -> ReviewThreadResolveCanaryReport:
    before = await query_review_thread_capabilities(
        client,
        app_slug=app_slug,
        repository=repository,
        pr=pr,
        thread_ids=thread_ids,
    )

    operations: list[ReviewThreadResolveOperation] = []
    for thread in before.threads:
        reason = _skip_reason(thread, repository=repository, pr=pr)
        if reason is not None:
            operations.append(
                ReviewThreadResolveOperation(
                    thread_id=thread.thread_id,
                    applied=False,
                    reason=reason,
                )
            )
            continue

        try:
            result = await client.resolve_review_thread(app_slug, repository, thread.thread_id)
        except (GitHubGraphQLError, httpx.HTTPStatusError, RuntimeError) as exc:
            operations.append(
                ReviewThreadResolveOperation(
                    thread_id=thread.thread_id,
                    applied=False,
                    reason=_resolve_failure_reason(exc),
                )
            )
        else:
            resolved_by = ((result or {}).get("resolvedBy") or {}).get("login")
            operations.append(
                ReviewThreadResolveOperation(
                    thread_id=thread.thread_id,
                    applied=True,
                    reason=None,
                    resolved_by=str(resolved_by) if resolved_by else None,
                )
            )

    after = await query_review_thread_capabilities(
        client,
        app_slug=app_slug,
        repository=repository,
        pr=pr,
        thread_ids=thread_ids,
    )
    return ReviewThreadResolveCanaryReport(
        before=before,
        operations=tuple(operations),
        after=after,
    )


def _resolve_failure_reason(exc: BaseException) -> str:
    if isinstance(exc, GitHubGraphQLError):
        first = exc.errors[0] if exc.errors else {}
        first_type = str(first.get("type") or "unknown")
        return f"resolveReviewThread failed: GraphQLError first_type={first_type}"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return f"resolveReviewThread failed: HTTP {exc.response.status_code}"
    return str(exc)
