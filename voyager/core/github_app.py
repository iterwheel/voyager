from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
import jwt

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

_THREAD_COMMENTS_QUERY = """
query ThreadComments($threadId: ID!, $cursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          databaseId
          author { login }
          body
          url
          createdAt
          replyTo { databaseId }
        }
      }
    }
  }
}
"""


@dataclass
class InstallationToken:
    token: str
    expires_at: datetime


class GitHubAppClient:
    def __init__(self, apps: dict[str, Any]) -> None:
        self._apps = apps
        self._tokens: dict[str, InstallationToken] = {}
        self._installation_ids: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None

    def _async_client(self) -> httpx.AsyncClient:
        """Return a per-instance cached httpx.AsyncClient.

        Override in tests to inject MockTransport. Production code reuses the
        cached client across all calls; the TLS connection pool is shared and
        connection setup is amortized.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    async def aclose(self) -> None:
        """Close the cached httpx client. Call on FastAPI lifespan shutdown."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _app_jwt(self, app: Any) -> str:
        if not app.private_key_path.exists():
            raise RuntimeError(f"private key not found for {app.slug}: {app.private_key_path}")
        now = datetime.now(UTC)
        payload = {
            "iat": int((now - timedelta(seconds=60)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": app.app_id,
        }
        private_key = app.private_key_path.read_text(encoding="utf-8")
        return str(jwt.encode(payload, private_key, algorithm="RS256"))

    async def installation_token(self, app_slug: str, *, repository: str | None = None) -> str:
        app = self._apps[app_slug]
        installation_id = app.configured_installation_id_for_repository(repository)
        if not installation_id:
            installation_id = getattr(app, "installation_id", "") or None
        if not installation_id and repository:
            installation_id = await self._discover_installation_id(app, repository)
        if not installation_id:
            target = f" on {repository}" if repository else ""
            raise RuntimeError(
                f"installation_id is not configured or discoverable for {app.slug}{target}"
            )

        cache_key = f"{app_slug}:{installation_id}"
        cached = self._tokens.get(cache_key)
        now = datetime.now(UTC)
        if cached and cached.expires_at > now + timedelta(minutes=5):
            return cached.token

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._app_jwt(app)}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
        client = self._async_client()
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        self._tokens[cache_key] = InstallationToken(token=data["token"], expires_at=expires_at)
        return str(data["token"])

    async def _discover_installation_id(self, app: Any, repository: str) -> str | None:
        cache_key = f"{app.slug}:{repository}"
        if cache_key in self._installation_ids:
            return self._installation_ids[cache_key]

        owner, name = repository.split("/", 1)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._app_jwt(app)}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        url = f"{GITHUB_API}/repos/{owner}/{name}/installation"
        client = self._async_client()
        response = await client.get(url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        installation_id = str((response.json() or {}).get("id") or "")
        if installation_id:
            self._installation_ids[cache_key] = installation_id
            return installation_id
        return None

    async def request(
        self,
        app_slug: str,
        method: str,
        path: str,
        *,
        repository: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        token = await self.installation_token(app_slug, repository=repository)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        client = self._async_client()
        response = await client.request(
            method, f"{GITHUB_API}{path}", headers=headers, json=json_body
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    async def graphql(
        self,
        app_slug: str,
        repository: str,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> Any:
        token = await self.installation_token(app_slug, repository=repository)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        client = self._async_client()
        response = await client.post(
            f"{GITHUB_API}/graphql",
            headers=headers,
            json={"query": query, "variables": variables},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            raise RuntimeError(f"GitHub GraphQL errors: {data['errors']}")
        return data.get("data")

    async def pull_request(self, app_slug: str, repo: str, pull_number: int) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        payload = await self.request(
            app_slug,
            "GET",
            f"/repos/{owner}/{name}/pulls/{pull_number}",
            repository=repo,
        )
        return dict(payload or {})

    async def pull_request_reviews(
        self, app_slug: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        """Fetch ALL PR reviews, paginating beyond the first 100 records.

        GitHub returns PR reviews paginated; without following pagination, any
        later-page approval, dismissal, or change-request is missed and
        evaluate_clearance_snapshot() decides on a stale review history.
        Codex round 3 P1.
        """
        owner, name = repo.split("/", 1)
        all_reviews: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self.request(
                app_slug,
                "GET",
                f"/repos/{owner}/{name}/pulls/{pull_number}/reviews?per_page=100&page={page}",
                repository=repo,
            )
            items = list(payload or [])
            all_reviews.extend(items)
            if len(items) < 100:
                break
            page += 1
            if page > 50:
                # Safety bound: 5,000 reviews. Past that, something is wrong
                # upstream and we should not infinite-loop.
                break
        return all_reviews

    async def request_pull_request_reviewers(
        self,
        app_slug: str,
        repo: str,
        pull_number: int,
        reviewers: list[str],
    ) -> Any:
        if not reviewers:
            return None
        owner, name = repo.split("/", 1)
        return await self.request(
            app_slug,
            "POST",
            f"/repos/{owner}/{name}/pulls/{pull_number}/requested_reviewers",
            repository=repo,
            json_body={"reviewers": reviewers},
        )

    async def pull_request_review_threads(
        self, app_slug: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        owner, name = repo.split("/", 1)
        query = """
        query PullRequestReviewThreads($owner: String!, $name: String!, $number: Int!, $cursor: String) {
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
                  isOutdated
                  path
                  line
                  startLine
                  comments(first: 100) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                      databaseId
                      author {
                        login
                      }
                      body
                      url
                      createdAt
                      replyTo { databaseId }
                    }
                  }
                }
              }
            }
          }
        }
        """
        threads: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            data = await self.graphql(
                app_slug,
                repo,
                query=query,
                variables={
                    "owner": owner,
                    "name": name,
                    "number": pull_number,
                    "cursor": cursor,
                },
            )
            connection = (((data or {}).get("repository") or {}).get("pullRequest") or {}).get(
                "reviewThreads"
            ) or {}
            threads.extend(connection.get("nodes") or [])
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        for thread in threads:
            page_info = (thread.get("comments") or {}).get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                continue
            cursor = page_info.get("endCursor")
            while True:
                data = await self.graphql(
                    app_slug,
                    repo,
                    query=_THREAD_COMMENTS_QUERY,
                    variables={"threadId": thread["id"], "cursor": cursor},
                )
                comments_conn = (((data or {}).get("node") or {}).get("comments")) or {}
                new_nodes = comments_conn.get("nodes") or []
                thread.setdefault("comments", {}).setdefault("nodes", []).extend(new_nodes)
                page_info = comments_conn.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")

        return threads

    async def resolve_review_thread(
        self, app_slug: str, repository: str, thread_id: str
    ) -> dict[str, Any]:
        """Resolve a GitHub review thread via GraphQL mutation.

        Used by Stage 1.5 of SWM-1101: after the deterministic pipeline judges a
        thread RESOLVED, the watchdog calls this to sync the GitHub UI state so
        maintainers see the resolution without manual clicks.

        Callers must verify the thread's current ``isResolved`` state immediately
        before invoking this mutation — reading ``isResolved`` from a stale cached
        snapshot and then mutating can silently clobber a maintainer's own manual
        resolve (or un-resolve) performed in the GitHub UI since the last fetch.
        Prefer fetching ``pull_request_review_threads`` just before calling this.
        """
        query = """
        mutation ResolveReviewThread($threadId: ID!) {
          resolveReviewThread(input: {threadId: $threadId}) {
            thread {
              id
              isResolved
              isOutdated
              resolvedBy {
                login
              }
            }
          }
        }
        """
        data = await self.graphql(
            app_slug,
            repository,
            query=query,
            variables={"threadId": thread_id},
        )
        return (((data or {}).get("resolveReviewThread") or {}).get("thread")) or {}

    async def unresolve_review_thread(
        self, app_slug: str, repository: str, thread_id: str
    ) -> dict[str, Any]:
        """Unresolve a GitHub review thread via GraphQL mutation.

        Mirror of ``resolve_review_thread`` for the reverse direction. Used when
        Stage 1.5 of SWM-1101 determines a previously-resolved thread needs to be
        re-opened (e.g., maintainer override or follow-up Codex flag).

        Callers must verify the thread's current ``isResolved`` state immediately
        before invoking this mutation to avoid clobbering a maintainer's manual
        state change since the last fetch.
        """
        query = """
        mutation UnresolveReviewThread($threadId: ID!) {
          unresolveReviewThread(input: {threadId: $threadId}) {
            thread {
              id
              isResolved
              isOutdated
              resolvedBy {
                login
              }
            }
          }
        }
        """
        data = await self.graphql(
            app_slug,
            repository,
            query=query,
            variables={"threadId": thread_id},
        )
        return (((data or {}).get("unresolveReviewThread") or {}).get("thread")) or {}

    async def pull_request_diff(self, app_slug: str, repo: str, pull_number: int) -> str:
        """Fetch the raw unified diff for a PR.

        Used by the Clearance pipeline's State B investigator path (Wave 7B-3) so the
        LLM can verify whether the author's push actually addresses the original
        Codex concern. Returns the diff as a single ``str`` exactly as GitHub
        serves it via the ``application/vnd.github.v3.diff`` accept header — the
        same format ``git diff`` produces, with file headers like
        ``diff --git a/path/file b/path/file`` and ``@@ ... @@`` hunk markers.

        GitHub's REST endpoint ``GET /repos/{owner}/{repo}/pulls/{pull_number}``
        serves either JSON (default ``Accept: application/vnd.github+json``) or
        the raw diff text (``Accept: application/vnd.github.v3.diff``). Bypass the
        shared ``request()`` helper because it sets the JSON Accept header
        unconditionally and decodes the response as JSON, which would corrupt a
        diff payload. Use ``installation_token`` + ``_async_client`` directly so we
        keep the same auth / retry / TLS pool behaviour as every other endpoint
        on this client.
        """
        owner, name = repo.split("/", 1)
        token = await self.installation_token(app_slug, repository=repo)
        headers = {
            "Accept": "application/vnd.github.v3.diff",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        url = f"{GITHUB_API}/repos/{owner}/{name}/pulls/{pull_number}"
        client = self._async_client()
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text

    async def add_labels(
        self, app_slug: str, repo: str, issue_number: int, labels: list[str]
    ) -> Any:
        if not labels:
            return None
        owner, name = repo.split("/", 1)
        return await self.request(
            app_slug,
            "POST",
            f"/repos/{owner}/{name}/issues/{issue_number}/labels",
            repository=repo,
            json_body={"labels": labels},
        )

    async def remove_label(self, app_slug: str, repo: str, issue_number: int, label: str) -> Any:
        owner, name = repo.split("/", 1)
        label_path = quote(label, safe="")
        try:
            return await self.request(
                app_slug,
                "DELETE",
                f"/repos/{owner}/{name}/issues/{issue_number}/labels/{label_path}",
                repository=repo,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def issue_reactions(
        self,
        app_slug: str,
        repo: str,
        issue_number: int,
        *,
        content: str | None = None,
    ) -> list[dict[str, Any]]:
        owner, name = repo.split("/", 1)
        path = f"/repos/{owner}/{name}/issues/{issue_number}/reactions?per_page=100"
        if content:
            path = f"{path}&content={quote(content, safe='')}"
        payload = await self.request(app_slug, "GET", path, repository=repo)
        return list(payload or [])

    async def add_issue_reaction(
        self,
        app_slug: str,
        repo: str,
        issue_number: int,
        content: str,
    ) -> Any:
        owner, name = repo.split("/", 1)
        return await self.request(
            app_slug,
            "POST",
            f"/repos/{owner}/{name}/issues/{issue_number}/reactions",
            repository=repo,
            json_body={"content": content},
        )

    async def remove_issue_reaction(
        self,
        app_slug: str,
        repo: str,
        issue_number: int,
        content: str,
    ) -> list[Any]:
        owner, name = repo.split("/", 1)
        bot_login = f"{app_slug}[bot]"
        removed: list[Any] = []
        for reaction in await self.issue_reactions(app_slug, repo, issue_number, content=content):
            user = reaction.get("user") or {}
            if user.get("login") != bot_login:
                continue
            removed.append(
                await self.request(
                    app_slug,
                    "DELETE",
                    f"/repos/{owner}/{name}/issues/{issue_number}/reactions/{reaction['id']}",
                    repository=repo,
                )
            )
        return removed

    async def issue_comments(
        self, app_slug: str, repo: str, issue_number: int
    ) -> list[dict[str, Any]]:
        """Fetch ALL issue/PR comments, paginating beyond the first 100 records.

        Without pagination, upsert_issue_comment() searching for an existing
        bot-marker comment on a busy issue/PR would miss markers on later
        pages and create a duplicate comment on every Blueprint/Stack/Clearance
        writeback. Codex round 5 P2.
        """
        owner, name = repo.split("/", 1)
        all_comments: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self.request(
                app_slug,
                "GET",
                f"/repos/{owner}/{name}/issues/{issue_number}/comments?per_page=100&page={page}",
                repository=repo,
            )
            items = list(payload or [])
            all_comments.extend(items)
            if len(items) < 100:
                break
            page += 1
            if page > 50:
                # Safety bound: 5,000 comments. Past that, something is
                # wrong upstream and we should not infinite-loop.
                break
        return all_comments

    async def create_issue_comment(
        self,
        app_slug: str,
        repo: str,
        issue_number: int,
        *,
        body: str,
    ) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        result = await self.request(
            app_slug,
            "POST",
            f"/repos/{owner}/{name}/issues/{issue_number}/comments",
            repository=repo,
            json_body={"body": body},
        )
        return dict(result or {})

    async def create_review_thread_reply(
        self,
        app_slug: str,
        repository: str,
        pull_number: int,
        comment_id: int,
        *,
        body: str,
    ) -> dict[str, Any]:
        """POST a reply to a PR review-comment thread.

        GitHub renders the reply inline next to the code the original review
        comment anchored to, so the Clearance pipeline's Stage 1.5 conclusion
        comments appear contextually — readers see the verdict alongside the
        code Codex flagged, not buried in the PR's top-level conversation.

        Uses the REST endpoint
        ``POST /repos/{owner}/{repo}/pulls/{pull_number}/comments/{comment_id}/replies``.
        """
        owner, name = repository.split("/", 1)
        payload = await self.request(
            app_slug,
            "POST",
            f"/repos/{owner}/{name}/pulls/{pull_number}/comments/{comment_id}/replies",
            repository=repository,
            json_body={"body": body},
        )
        return dict(payload or {})

    async def upsert_issue_comment(
        self,
        app_slug: str,
        repo: str,
        issue_number: int,
        *,
        marker: str,
        body: str,
    ) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        bot_login = f"{app_slug}[bot]"
        comments = await self.issue_comments(app_slug, repo, issue_number)
        for comment in comments:
            user = comment.get("user") or {}
            if marker in str(comment.get("body") or "") and user.get("login") == bot_login:
                result = await self.request(
                    app_slug,
                    "PATCH",
                    f"/repos/{owner}/{name}/issues/comments/{comment['id']}",
                    repository=repo,
                    json_body={"body": body},
                )
                return dict(result or {})
        result = await self.request(
            app_slug,
            "POST",
            f"/repos/{owner}/{name}/issues/{issue_number}/comments",
            repository=repo,
            json_body={"body": body},
        )
        return dict(result or {})
