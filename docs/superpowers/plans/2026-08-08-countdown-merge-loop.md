# Countdown Merge-Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vyg countdown merge-loop` — autonomously rebase-merge agent-authored PRs when all deterministic conditions are green (spec: `rules/VOY-1839-PRP-Countdown-Merge-Loop-Autonomous-Agent-PR-Merge.md`).

**Architecture:** One new module `voyager/core/merge_loop.py` mirroring the resolve-loop skeleton in `voyager/core/countdown_loop.py` (allowlist ceiling, single-instance lock, per-run cap, redacted summary, local JSONL audit), plus a CLI command, launchd/wrapper deployment templates, and a deployment SOP. Single mutation type: `mergePullRequest` (REBASE, `expectedHeadOid`-guarded).

**Tech Stack:** Python 3.12, typer CLI, httpx GraphQL, pytest. No new dependencies.

## Global Constraints

- Identity is fixed to `iterwheel-countdown-bot`; token via `read_machine_token()` from `voyager/core/resolve_conversation.py`; never logged or printed.
- Fail-closed everywhere: unreadable/missing/None data ⇒ skip, never merge.
- Only PRs authored by `ryosaeba1985` may ever be merged.
- Merge method is REBASE, always with `expectedHeadOid`.
- All queries/mutations go through clients that whitelist their known GraphQL documents (defense-in-depth pattern from `make_read_gql`).
- `ruff` + `mypy` clean; conventional-commit messages; run tests with `.venv/bin/pytest` from the worktree root.
- Do not modify `voyager/core/countdown_loop.py` or `voyager/core/resolve_conversation.py` (reuse via import only: `single_instance_lock`, `load_repo_list`, `gate_repos`, `make_read_gql` is NOT reusable — merge-loop defines its own clients).

---

### Task 1: Module skeleton — constants, dataclasses, ceiling

**Files:**
- Create: `voyager/core/merge_loop.py`
- Test: `tests/unit/test_merge_loop.py`

**Interfaces:**
- Consumes: `ResolveConversationError` from `voyager.core.resolve_conversation`; `CLEARANCE_COMMENT_MARKER` from `voyager.bots.clearance.constants`.
- Produces (later tasks rely on these exact names):
  - `AGENT_PR_AUTHORS: frozenset[str]`, `CLEARANCE_BOT_LOGINS: frozenset[str]`, `REQUIRED_READINESS_STAGE: int = 3`
  - `MERGE_ALLOWED_REPOS: frozenset[str]`, `merge_allowed_repos() -> frozenset[str]` (env `VOYAGER_MERGE_EXTRA_REPOS`)
  - `DEFAULT_MERGE_LOCK_PATH: Path`, `DEFAULT_MERGE_AUDIT_PATH: Path`
  - `PrSnapshot` frozen dataclass: `pr_id: str, number: int, author: str, is_draft: bool, head_oid: str, checks_state: str | None, unresolved_threads: int | None, readiness_stage: int | None, readiness_head: str | None`
  - `MergeDecision` frozen dataclass: `repo: str, pr: int, action: str, reason: str = ""` with `public() -> dict`
  - `MergeLoopSummary` frozen dataclass: `repos_scanned/repos_skipped: tuple[str, ...], prs_scanned: int, decisions: tuple[MergeDecision, ...], capped: bool, dry_run: bool, errors: tuple[tuple[str, str], ...]` with properties `merged`, `would_merge`, and `to_public_dict() -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_merge_loop.py
"""Unit tests for voyager.core.merge_loop."""

from __future__ import annotations

import pytest

from voyager.core.merge_loop import (
    MERGE_ALLOWED_REPOS,
    MergeDecision,
    MergeLoopSummary,
    merge_allowed_repos,
)
from voyager.core.resolve_conversation import ResolveConversationError


class TestMergeAllowedRepos:
    def test_builtin_is_sandbox_only(self):
        assert MERGE_ALLOWED_REPOS == frozenset({"iterwheel/voyager-sandbox"})

    def test_env_extras_extend_ceiling(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        assert "frankyxhl/fx_bin" in merge_allowed_repos()
        assert "iterwheel/voyager-sandbox" in merge_allowed_repos()

    def test_extras_normalize_case(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "Frankyxhl/FX_bin")
        assert "frankyxhl/fx_bin" in merge_allowed_repos()

    def test_malformed_extra_fails_closed(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "not-a-repo-path")
        with pytest.raises(ResolveConversationError):
            merge_allowed_repos()


class TestDecisionRedaction:
    def test_sandbox_repo_is_raw(self):
        d = MergeDecision(repo="iterwheel/voyager-sandbox", pr=7, action="merged")
        assert d.public() == {
            "repo": "iterwheel/voyager-sandbox",
            "action": "merged",
            "pr": 7,
            "reason": "",
        }

    def test_other_repo_is_redacted(self):
        d = MergeDecision(repo="frankyxhl/fx_bin", pr=88, action="merged")
        assert d.public() == {"repo": "frankyxhl/fx_bin", "action": "merged", "redacted": True}


class TestSummary:
    def test_counts_and_public_dict(self):
        ds = (
            MergeDecision(repo="frankyxhl/fx_bin", pr=1, action="merged"),
            MergeDecision(repo="frankyxhl/fx_bin", pr=2, action="would_merge"),
            MergeDecision(repo="frankyxhl/fx_bin", pr=3, action="skipped", reason="draft"),
        )
        s = MergeLoopSummary(
            repos_scanned=("frankyxhl/fx_bin",),
            repos_skipped=(),
            prs_scanned=3,
            decisions=ds,
            capped=False,
            dry_run=False,
            errors=(),
        )
        assert s.merged == 1
        assert s.would_merge == 1
        pub = s.to_public_dict()
        assert pub["merged"] == 1
        assert pub["decision_count"] == 3
        assert pub["errors"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voyager.core.merge_loop'`

- [ ] **Step 3: Write the implementation**

```python
# voyager/core/merge_loop.py
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voyager.bots.clearance.constants import CLEARANCE_COMMENT_MARKER
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add voyager/core/merge_loop.py tests/unit/test_merge_loop.py
git commit -m "feat(merge-loop): module skeleton — ceiling, snapshot, decision, summary"
```

---

### Task 2: Readiness-comment parser

**Files:**
- Modify: `voyager/core/merge_loop.py` (append)
- Test: `tests/unit/test_merge_loop.py` (append)

**Interfaces:**
- Produces: `parse_readiness(body: str) -> tuple[int, str] | None` — `(stage, full_head_sha)`; `None` when the body is not a clearance readiness comment or lacks either field. Later tasks call it on issue-comment bodies authored by `CLEARANCE_BOT_LOGINS`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_merge_loop.py
from voyager.core.merge_loop import parse_readiness

READINESS_BODY = """<!-- iterwheel:clearance-readiness -->
## Clearance

🚦 Stage: 3 - Ready for approval (`clearance-3-ready-for-approval`)
✅ Threads: 0 blocking

<details>
<summary>Details</summary>

- Head SHA: `a96782f4e41207e63d63bd552f9b4fa5399c7eb8`
</details>
"""


class TestParseReadiness:
    def test_parses_stage_and_head(self):
        assert parse_readiness(READINESS_BODY) == (
            3,
            "a96782f4e41207e63d63bd552f9b4fa5399c7eb8",
        )

    def test_stage_2_parses_as_2(self):
        body = READINESS_BODY.replace("Stage: 3", "Stage: 2")
        parsed = parse_readiness(body)
        assert parsed is not None and parsed[0] == 2

    def test_missing_marker_returns_none(self):
        assert parse_readiness(READINESS_BODY.split("\n", 1)[1]) is None

    def test_missing_head_sha_returns_none(self):
        body = READINESS_BODY.replace("Head SHA", "Head Something")
        assert parse_readiness(body) is None

    def test_short_sha_returns_none(self):
        body = READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", "a96782f")
        assert parse_readiness(body) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestParseReadiness -v`
Expected: FAIL — `ImportError: cannot import name 'parse_readiness'`

- [ ] **Step 3: Write the implementation**

```python
# append to voyager/core/merge_loop.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestParseReadiness -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add voyager/core/merge_loop.py tests/unit/test_merge_loop.py
git commit -m "feat(merge-loop): parse clearance readiness stage + head anchor"
```

---

### Task 3: `should_merge` predicate (truth table)

**Files:**
- Modify: `voyager/core/merge_loop.py` (append)
- Test: `tests/unit/test_merge_loop.py` (append)

**Interfaces:**
- Consumes: `PrSnapshot` (Task 1).
- Produces: `should_merge(s: PrSnapshot) -> str` — returns `"ok"` to merge, else the skip reason: `not_agent_author | draft | checks_not_green | threads_unreadable | threads_unresolved | readiness_missing | readiness_not_ready | readiness_stale_head`. Reason strings are stable API (audit + tests).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_merge_loop.py
from voyager.core.merge_loop import PrSnapshot, should_merge

HEAD = "a" * 40


def snap(**overrides) -> PrSnapshot:
    """A fully-green agent PR snapshot; tests break one field at a time."""
    base = dict(
        pr_id="PR_x",
        number=1,
        author="ryosaeba1985",
        is_draft=False,
        head_oid=HEAD,
        checks_state="SUCCESS",
        unresolved_threads=0,
        readiness_stage=3,
        readiness_head=HEAD,
    )
    base.update(overrides)
    return PrSnapshot(**base)


class TestShouldMerge:
    def test_fully_green_is_ok(self):
        assert should_merge(snap()) == "ok"

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"author": "frankyxhl"}, "not_agent_author"),
            ({"author": "somebody-else"}, "not_agent_author"),
            ({"is_draft": True}, "draft"),
            ({"checks_state": "FAILURE"}, "checks_not_green"),
            ({"checks_state": "PENDING"}, "checks_not_green"),
            ({"checks_state": None}, "checks_not_green"),
            ({"unresolved_threads": None}, "threads_unreadable"),
            ({"unresolved_threads": 2}, "threads_unresolved"),
            ({"readiness_stage": None, "readiness_head": None}, "readiness_missing"),
            ({"readiness_stage": 2}, "readiness_not_ready"),
            ({"readiness_head": "b" * 40}, "readiness_stale_head"),
        ],
    )
    def test_each_condition_fails_closed(self, overrides, reason):
        assert should_merge(snap(**overrides)) == reason

    def test_stage_4_ready_for_merge_also_ok(self):
        assert should_merge(snap(readiness_stage=4)) == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestShouldMerge -v`
Expected: FAIL — `ImportError: cannot import name 'should_merge'`

- [ ] **Step 3: Write the implementation**

```python
# append to voyager/core/merge_loop.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestShouldMerge -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add voyager/core/merge_loop.py tests/unit/test_merge_loop.py
git commit -m "feat(merge-loop): deterministic should_merge predicate"
```

---

### Task 4: GraphQL read client + PR snapshot enumeration

**Files:**
- Modify: `voyager/core/merge_loop.py` (append)
- Test: `tests/unit/test_merge_loop.py` (append)

**Interfaces:**
- Consumes: `PrSnapshot`, `parse_readiness`, `CLEARANCE_BOT_LOGINS` (Tasks 1–2); `httpx` (already a dependency).
- Produces:
  - `make_merge_read_gql(token: str, *, client_factory=...) -> Callable[[str, dict], dict]` — whitelists exactly `_AGENT_PR_PAGE_QUERY` and `_PR_THREADS_QUERY`.
  - `snapshots_for_repo(gql, repo: str) -> list[PrSnapshot]` — every open PR, paginated; thread counts fetched only for PRs passing the cheap author/draft/checks fields (None otherwise is fine — predicate already rejected them).
  - `_unresolved_thread_count(gql, repo: str, number: int) -> int | None` — paginated; None on read error.

- [ ] **Step 1: Write the failing tests**

Tests drive the pure orchestration with a fake `gql` callable — no HTTP.

```python
# append to tests/unit/test_merge_loop.py
from voyager.core.merge_loop import (
    _AGENT_PR_PAGE_QUERY,
    _PR_THREADS_QUERY,
    make_merge_read_gql,
    snapshots_for_repo,
)


def _pr_node(
    number=1, author="ryosaeba1985", draft=False, checks="SUCCESS", head=HEAD, comments=()
):
    return {
        "id": f"PR_{number}",
        "number": number,
        "isDraft": draft,
        "headRefOid": head,
        "author": {"login": author},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]},
        "comments": {"nodes": list(comments)},
    }


def _fake_gql(pr_nodes, thread_pages=None):
    """Return a gql callable serving one PR page and optional thread pages."""
    thread_pages = thread_pages or {}

    def gql(query, variables):
        if query is _AGENT_PR_PAGE_QUERY:
            return {
                "repository": {
                    "pullRequests": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": pr_nodes,
                    }
                }
            }
        if query is _PR_THREADS_QUERY:
            nodes = thread_pages.get(variables["number"], [])
            return {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": nodes,
                        }
                    }
                }
            }
        raise AssertionError(f"unexpected query: {query[:40]}")

    return gql


class TestSnapshotsForRepo:
    def test_green_agent_pr_snapshot(self):
        readiness = {
            "author": {"login": "iterwheel-clearance"},
            "body": READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", HEAD),
        }
        gql = _fake_gql(
            [_pr_node(comments=[readiness])],
            thread_pages={1: [{"isResolved": True}]},
        )
        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert s.author == "ryosaeba1985"
        assert s.unresolved_threads == 0
        assert s.readiness_stage == 3
        assert s.readiness_head == HEAD

    def test_readiness_from_wrong_author_ignored(self):
        impostor = {"author": {"login": "someone"}, "body": READINESS_BODY}
        gql = _fake_gql([_pr_node(comments=[impostor])], thread_pages={1: []})
        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert s.readiness_stage is None

    def test_non_agent_pr_skips_thread_fetch(self):
        calls = []
        inner = _fake_gql([_pr_node(author="frankyxhl")])

        def spy(query, variables):
            calls.append(query)
            return inner(query, variables)

        (s,) = snapshots_for_repo(spy, "frankyxhl/fx_bin")
        assert s.unresolved_threads is None
        assert _PR_THREADS_QUERY not in calls

    def test_unresolved_threads_counted(self):
        gql = _fake_gql(
            [_pr_node()],
            thread_pages={1: [{"isResolved": False}, {"isResolved": True}]},
        )
        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert s.unresolved_threads == 1

    def test_null_repository_raises(self):
        def gql(query, variables):
            return {"repository": None}

        with pytest.raises(ResolveConversationError):
            snapshots_for_repo(gql, "frankyxhl/fx_bin")


class TestMergeReadGqlWhitelist:
    def test_refuses_unknown_query(self):
        gql = make_merge_read_gql("tok")
        with pytest.raises(ResolveConversationError):
            gql("query { viewer { login } }", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestSnapshotsForRepo tests/unit/test_merge_loop.py::TestMergeReadGqlWhitelist -v`
Expected: FAIL — ImportError on the new names

- [ ] **Step 3: Write the implementation**

```python
# append to voyager/core/merge_loop.py
from collections.abc import Callable  # move to the module's import block

import httpx  # move to the module's import block

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
```

Note: consolidate the `httpx` / `Callable` imports into the module's top import block (mypy/ruff will flag misplaced imports).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py -v`
Expected: PASS (all, including earlier tasks)

- [ ] **Step 5: Commit**

```bash
git add voyager/core/merge_loop.py tests/unit/test_merge_loop.py
git commit -m "feat(merge-loop): whitelisted read client + PR snapshot enumeration"
```

---

### Task 5: Merge mutation client + `merge_pr`

**Files:**
- Modify: `voyager/core/merge_loop.py` (append)
- Test: `tests/unit/test_merge_loop.py` (append)

**Interfaces:**
- Consumes: `_post_gql` (Task 4).
- Produces:
  - `make_merge_gql(token: str, *, client_factory=...) -> GqlFn` — whitelists exactly `_MERGE_MUTATION`.
  - `merge_pr(merge_gql: GqlFn, pr_id: str, expected_head: str) -> tuple[str, str]` — `("merged", "")` on success, `("merge_failed", <message>)` on any fault. Never raises.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_merge_loop.py
from voyager.core.merge_loop import _MERGE_MUTATION, make_merge_gql, merge_pr


class TestMergePr:
    def test_success(self):
        def gql(query, variables):
            assert query is _MERGE_MUTATION
            assert variables == {"prId": "PR_1", "expectedHeadOid": HEAD}
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        assert merge_pr(gql, "PR_1", HEAD) == ("merged", "")

    def test_api_error_is_merge_failed_not_raise(self):
        def gql(query, variables):
            raise ResolveConversationError("merge-loop GraphQL returned 1 error(s)")

        action, msg = merge_pr(gql, "PR_1", HEAD)
        assert action == "merge_failed"
        assert "error" in msg

    def test_unmerged_response_is_merge_failed(self):
        def gql(query, variables):
            return {"mergePullRequest": {"pullRequest": {"merged": False}}}

        assert merge_pr(gql, "PR_1", HEAD)[0] == "merge_failed"

    def test_mutation_client_refuses_unknown_operation(self):
        gql = make_merge_gql("tok")
        with pytest.raises(ResolveConversationError):
            gql(
                'mutation { closePullRequest(input: {pullRequestId: "x"}) { clientMutationId } }',
                {},
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestMergePr -v`
Expected: FAIL — ImportError on the new names

- [ ] **Step 3: Write the implementation**

```python
# append to voyager/core/merge_loop.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestMergePr -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add voyager/core/merge_loop.py tests/unit/test_merge_loop.py
git commit -m "feat(merge-loop): expectedHeadOid-guarded rebase merge mutation"
```

---

### Task 6: `run_merge_loop` orchestration — ceiling, cap, audit, summary

**Files:**
- Modify: `voyager/core/merge_loop.py` (append)
- Test: `tests/unit/test_merge_loop.py` (append)

**Interfaces:**
- Consumes: everything above, plus `gate_repos` from `voyager.core.countdown_loop`.
- Produces: `run_merge_loop(requested_repos: Sequence[str], *, read_gql: GqlFn, merge_gql: GqlFn, max_merges: int = 3, dry_run: bool = False, audit_path: Path | None = DEFAULT_MERGE_AUDIT_PATH, now: Callable[[], str] | None = None) -> MergeLoopSummary`. Decision policy: non-agent PRs produce NO decision (never touched, never listed); agent PRs always produce exactly one decision (`merged` / `would_merge` / `skipped` / `merge_failed`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_merge_loop.py
import json as _json

from voyager.core.merge_loop import run_merge_loop


def _green_pr(number):
    readiness = {
        "author": {"login": "iterwheel-clearance"},
        "body": READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", HEAD),
    }
    return _pr_node(number=number, comments=[readiness])


class TestRunMergeLoop:
    def _run(self, pr_nodes, thread_pages=None, **kwargs):
        read = _fake_gql(pr_nodes, thread_pages=thread_pages or {})
        merges: list[str] = []

        def merge_gql(query, variables):
            merges.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            audit_path=None,
            **kwargs,
        )
        return summary, merges

    def test_repo_outside_ceiling_is_skipped(self, monkeypatch):
        monkeypatch.delenv("VOYAGER_MERGE_EXTRA_REPOS", raising=False)
        summary, merges = self._run([_green_pr(1)], thread_pages={1: []})
        assert summary.repos_skipped == ("frankyxhl/fx_bin",)
        assert merges == []

    def test_green_agent_pr_merges(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run([_green_pr(1)], thread_pages={1: []})
        assert merges == ["PR_1"]
        assert summary.merged == 1

    def test_dry_run_issues_no_mutation(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run([_green_pr(1)], thread_pages={1: []}, dry_run=True)
        assert merges == []
        assert summary.would_merge == 1

    def test_non_agent_pr_produces_no_decision(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run([_pr_node(number=2, author="stranger")])
        assert merges == []
        assert summary.decisions == ()

    def test_not_ready_agent_pr_is_skipped_with_reason(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run([_pr_node(number=3)], thread_pages={3: []})
        assert merges == []
        (d,) = summary.decisions
        assert (d.action, d.reason) == ("skipped", "readiness_missing")

    def test_cap_stops_merging(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run(
            [_green_pr(1), _green_pr(2)],
            thread_pages={1: [], 2: []},
            max_merges=1,
        )
        assert len(merges) == 1
        assert summary.capped is True

    def test_audit_lines_written(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql([_green_pr(1)], thread_pages={1: []})

        def merge_gql(query, variables):
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        run_merge_loop(["frankyxhl/fx_bin"], read_gql=read, merge_gql=merge_gql, audit_path=audit)
        (line,) = audit.read_text().strip().splitlines()
        record = _json.loads(line)
        assert record["action"] == "merged"
        assert record["pr"] == 1
        assert record["repo"] == "frankyxhl/fx_bin"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestRunMergeLoop -v`
Expected: FAIL — `ImportError: cannot import name 'run_merge_loop'`

- [ ] **Step 3: Write the implementation**

```python
# append to voyager/core/merge_loop.py
from collections.abc import Sequence  # move to the module's import block
from datetime import datetime, timezone  # move to the module's import block

from voyager.core.countdown_loop import gate_repos  # module import block


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    Agent PRs get exactly one decision each. Cap counts merged (live) or
    would_merge (dry-run). One repo's scan failure is recorded and the
    remaining repos still run.
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
```

- [ ] **Step 4: Run the full unit file**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py -v`
Expected: PASS (all)

- [ ] **Step 5: Run lint + types, fix anything flagged**

Run: `.venv/bin/ruff check voyager/core/merge_loop.py tests/unit/test_merge_loop.py && .venv/bin/mypy voyager/core/merge_loop.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add voyager/core/merge_loop.py tests/unit/test_merge_loop.py
git commit -m "feat(merge-loop): run_merge_loop orchestration with cap + audit"
```

---

### Task 7: CLI command `vyg countdown merge-loop`

**Files:**
- Modify: `voyager/cli.py` (append a command to `countdown_app`, directly after the existing `resolve_loop` function)
- Test: `tests/unit/test_merge_loop.py` (append)

**Interfaces:**
- Consumes: `run_merge_loop`, `make_merge_read_gql`, `make_merge_gql`, `DEFAULT_MERGE_LOCK_PATH` (Tasks 4–6); `single_instance_lock`, `load_repo_list`, `AlreadyRunningError` from `voyager.core.countdown_loop`; `read_machine_token`, `ResolveConversationError` from `voyager.core.resolve_conversation`.
- Produces: CLI command `merge-loop` with options `--repos` (required), `--max-merges` (default 3), `--dry-run`, `--json` — same shape as `resolve-loop`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_merge_loop.py
from typer.testing import CliRunner


class TestCli:
    def test_merge_loop_command_registered(self):
        from voyager.cli import app

        result = CliRunner().invoke(app, ["countdown", "merge-loop", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--max-merges" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py::TestCli -v`
Expected: FAIL — exit code 2 (`No such command 'merge-loop'`)

- [ ] **Step 3: Write the implementation**

```python
# voyager/cli.py — add below the resolve_loop command
@countdown_app.command("merge-loop")
def merge_loop(
    repos: str = typer.Option(..., "--repos", help="Path to an OWNER/REPO-per-line file."),
    max_merges: int = typer.Option(3, "--max-merges", help="Cap on merges per run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Evaluate; issue no mutation."),
    as_json: bool = typer.Option(False, "--json", help="Emit the redacted JSON summary."),
) -> None:
    """Rebase-merge fully-green agent PRs across allowlisted repos.

    Deterministic predicate only (VOY-1839): agent author, non-draft, CI
    rollup SUCCESS, zero unresolved threads, head-anchored clearance
    readiness >= Stage 3. Identity is fixed to iterwheel-countdown-bot.
    """
    from pathlib import Path

    from voyager.core.countdown_loop import (
        AlreadyRunningError,
        load_repo_list,
        single_instance_lock,
    )
    from voyager.core.merge_loop import (
        DEFAULT_MERGE_LOCK_PATH,
        make_merge_gql,
        make_merge_read_gql,
        run_merge_loop,
    )
    from voyager.core.resolve_conversation import (
        ResolveConversationError,
        read_machine_token,
    )

    try:
        requested = load_repo_list(Path(repos))
        token = read_machine_token()
        read_gql = make_merge_read_gql(token)
        merge_gql = make_merge_gql(token)
        with single_instance_lock(DEFAULT_MERGE_LOCK_PATH):
            summary = run_merge_loop(
                requested,
                read_gql=read_gql,
                merge_gql=merge_gql,
                max_merges=max_merges,
                dry_run=dry_run,
            )
    except AlreadyRunningError as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1) from exc
    except (ResolveConversationError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1) from exc

    public = summary.to_public_dict()
    if as_json:
        typer.echo(json.dumps(public))
    else:
        typer.echo(f"repos_scanned: {len(public['repos_scanned'])}")
        typer.echo(f"merged:        {public['merged']}")
        typer.echo(f"would_merge:   {public['would_merge']}")
        typer.echo(f"capped:        {public['capped']}")
        typer.echo(f"dry_run:       {public['dry_run']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_merge_loop.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add voyager/cli.py tests/unit/test_merge_loop.py
git commit -m "feat(cli): vyg countdown merge-loop command"
```

---

### Task 8: Deployment templates (wrapper, env, repos, plist)

**Files:**
- Create: `deploy/wukong/merge-loop-adaptive.sh` (chmod 755)
- Create: `deploy/wukong/merge-loop.env.example`
- Create: `deploy/wukong/merge-loop.repos.example`
- Create: `deploy/launchd/com.iterwheel.voyager.merge-loop.plist`

**Interfaces:**
- Consumes: the CLI command from Task 7; the structure of `deploy/wukong/countdown-resolve-loop-adaptive.sh` (read it first and mirror its env re-source/fail-closed/streak logic exactly, substituting the names below).
- Produces: operator-installable templates. Env keys (wrapper contract): `MERGE_LOOP_ENABLED`, `MERGE_MAX_MERGES`, `MERGE_FAST_INTERVAL`, `MERGE_SLOW_INTERVAL`, `MERGE_FAST_STREAK_MAX`, `VOYAGER_MERGE_EXTRA_REPOS`. Install targets: `/Users/frank/.voyager/bin/merge-loop-adaptive.sh`, `/Users/frank/.voyager/merge-loop.env`, `/Users/frank/.voyager/merge-loop.repos`.

- [ ] **Step 1: Copy and adapt the wrapper**

Start from `deploy/wukong/countdown-resolve-loop-adaptive.sh` and apply exactly these substitutions (keep every fail-closed behavior — unset-before-source, integer validation, disabled⇒sleep-not-exit, streak cap):

- `ENV_FILE=/Users/frank/.voyager/merge-loop.env`
- `REPOS_FILE=/Users/frank/.voyager/merge-loop.repos`
- managed env vars: the six `MERGE_*`/`VOYAGER_MERGE_EXTRA_REPOS` keys above (drop `VOYAGER_DEEPSEEK_API_KEY` — no LLM gate)
- the run line: `"$VYG" countdown merge-loop --repos "$REPOS_FILE" --max-merges "${MERGE_MAX_MERGES:-3}" --json`
- enabled gate reads `MERGE_LOOP_ENABLED`
- decision detection for the fast lane: parse `"decision_count":` from the JSON exactly as the resolve wrapper does

- [ ] **Step 2: Write the env example**

```bash
# deploy/wukong/merge-loop.env.example
# Wukong Countdown merge-loop environment template.
#
# Copy to /Users/frank/.voyager/merge-loop.env, edit locally, chmod 600.
# launchd sources it before running vyg countdown merge-loop.

PYTHONUNBUFFERED=1

# Fail closed by default. Set to true only after the VOY-1839 dry-run gate passes.
MERGE_LOOP_ENABLED=false
MERGE_MAX_MERGES=3

# Adaptive cadence (seconds); wrapper validates integers and falls back loudly.
MERGE_FAST_INTERVAL=300
MERGE_SLOW_INTERVAL=3600
MERGE_FAST_STREAK_MAX=6

# Operator-local allowlist extension (merge ceiling — VOY-1839).
VOYAGER_MERGE_EXTRA_REPOS=
```

- [ ] **Step 3: Write the repos example**

```
# deploy/wukong/merge-loop.repos.example
# OWNER/REPO per line for vyg countdown merge-loop.
#
# Start with the sandbox during deployment validation. Add production
# repositories only after the VOY-1839 dry-run gate and operator approval.

iterwheel/voyager-sandbox
```

- [ ] **Step 4: Write the plist**

Copy `deploy/launchd/com.iterwheel.voyager.countdown-resolve-loop.plist` with these substitutions: Label `com.iterwheel.voyager.merge-loop`; ProgramArguments exec path `/Users/frank/.voyager/bin/merge-loop-adaptive.sh`; log paths `/Users/frank/Library/Logs/voyager/merge-loop.{out,err}.log`. Keep `RunAtLoad`, `KeepAlive`, `Umask` unchanged.

- [ ] **Step 5: Verify wrapper syntax and permissions**

Run: `zsh -n deploy/wukong/merge-loop-adaptive.sh && test -x deploy/wukong/merge-loop-adaptive.sh && plutil -lint deploy/launchd/com.iterwheel.voyager.merge-loop.plist`
Expected: no syntax errors; executable; `plist OK`

- [ ] **Step 6: Commit**

```bash
git add deploy/wukong/merge-loop-adaptive.sh deploy/wukong/merge-loop.env.example \
        deploy/wukong/merge-loop.repos.example deploy/launchd/com.iterwheel.voyager.merge-loop.plist
git commit -m "feat(deploy): merge-loop adaptive wrapper, env/repos templates, launchd plist"
```

---

### Task 9: Deployment SOP doc + index

**Files:**
- Create: `rules/VOY-1840-SOP-Countdown-Merge-Loop-Launchd-Deployment.md`
- Modify: `rules/VOY-0000-REF-Document-Index.md` (via `af index --root .`)

**Interfaces:**
- Consumes: VOY-1835 (structure to mirror), VOY-1839 (spec, esp. §Rollout and §Target-repo GitHub configuration), Task 8 template paths.
- Produces: the operator runbook for installing, enabling, and rolling back the merge-loop daemon.

- [ ] **Step 1: Write the SOP**

Mirror `rules/VOY-1835-SOP-Countdown-Resolve-Loop-Launchd-Deployment.md` section-for-section, substituting merge-loop names/paths from Task 8, and add two sections VOY-1835 does not have:

1. **Target-repo GitHub prerequisites** — copy the ruleset-change table from VOY-1839 verbatim (approve count 1→0 on `main-pr-gates`, code-owner review off on `protect main`, add required status checks, countdown-bot bypass on `main-owner-merge-only` if the canary shows it is needed; keep `required_review_thread_resolution` and CodeQL).
2. **Rollout gate** — the five-step canary sequence from VOY-1839 §Rollout (dry-run → operator review → ruleset changes → live `MERGE_MAX_MERGES=1` canary → cap 3).

- [ ] **Step 2: Regenerate index and validate**

Run: `af index --root . && af validate --root .`
Expected: index updated; `0 issues`

- [ ] **Step 3: Commit**

```bash
git add rules/VOY-1840-SOP-Countdown-Merge-Loop-Launchd-Deployment.md rules/VOY-0000-REF-Document-Index.md
git commit -m "docs(rules): VOY-1840 merge-loop launchd deployment SOP"
```

---

### Task 10: Full-suite gate + fx_bin dry-run verification

**Files:**
- None created; verification only.

**Interfaces:**
- Consumes: everything above.
- Produces: evidence for the PR body and the VOY-1839 rollout gate.

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/pytest -x -q`
Expected: PASS (no regressions)

- [ ] **Step 2: Live read-only dry-run against fx_bin**

```bash
printf 'frankyxhl/fx_bin\n' > /tmp/merge-loop-fxbin.repos
VOYAGER_MERGE_EXTRA_REPOS=frankyxhl/fx_bin \
  .venv/bin/vyg countdown merge-loop --repos /tmp/merge-loop-fxbin.repos --dry-run --json
```

Expected: `"repos_scanned": ["frankyxhl/fx_bin"]`, no exception; `would_merge` reflects only fully-green agent PRs (0 if none currently qualify). Paste the JSON into the PR body.

- [ ] **Step 3: Commit any fixups, push, open PR**

```bash
git push fork agent/voy-1839-merge-loop-spec
# PR onto the voyager default branch, authored by ryosaeba1985 (gh auth status first):
gh auth status
gh pr create --title "feat(countdown): merge-loop — autonomous agent-PR rebase merge (VOY-1839)" \
  --body-file <(cat <<'EOF'
Implements VOY-1839: deterministic zero-touch rebase merge of agent PRs.

- predicate: agent author / non-draft / CI SUCCESS / 0 unresolved threads / head-anchored clearance readiness >= Stage 3
- single mutation: mergePullRequest REBASE + expectedHeadOid
- resolve-loop skeleton reuse: ceiling, lock, cap, audit, adaptive launchd wrapper
- dry-run output vs frankyxhl/fx_bin: <paste JSON from Task 10 Step 2>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_016XdPV6mPuHh115xi7phUbS
EOF
)
```

---

## Post-merge operator steps (NOT part of this plan's code)

Deployment (install templates to `~/.voyager`, `launchctl bootstrap`), the fx_bin ruleset changes, and the live canary follow `rules/VOY-1840-SOP-...` after this PR merges — operator-gated per VOY-1839 §Rollout.
