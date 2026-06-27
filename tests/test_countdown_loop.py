"""Tests for the deterministic core of the Countdown resolve loop (PRP VOY-1830, issue A)."""

from __future__ import annotations

from typing import Any

import pytest

from voyager.core.countdown_diagnostic import (
    DEDICATED_PAT_FALLBACK_RESOLVE_ALLOWED_REPOSITORIES,
)
from voyager.core.countdown_loop import (
    AlreadyRunningError,
    Candidate,
    LoopSummary,
    gate_repos,
    load_repo_list,
    run_resolve_loop,
    single_instance_lock,
)
from voyager.core.github_app import GitHubGraphQLError

SANDBOX = "iterwheel/voyager-sandbox"
REAL = "iterwheel/voyager"


def _thread(
    tid: str,
    *,
    resolved: bool = False,
    outdated: bool = False,
    can_resolve: bool = True,
    can_reply: bool = True,
) -> dict[str, Any]:
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": outdated,
        "viewerCanResolve": can_resolve,
        "viewerCanReply": can_reply,
    }


class FakeClient:
    """Stand-in for GitHubAppClient: serves canned PR numbers and review threads."""

    def __init__(
        self,
        *,
        prs_by_repo: dict[str, list[int]] | None = None,
        threads_by_pr: dict[tuple[str, int], list[dict[str, Any]]] | None = None,
        raise_repos: set[str] | None = None,
        raise_prs: set[tuple[str, int]] | None = None,
        pr_pages: dict[str, list[list[int]]] | None = None,
    ) -> None:
        self.prs_by_repo = prs_by_repo or {}
        self.threads_by_pr = threads_by_pr or {}
        self.raise_repos = raise_repos or set()
        self.raise_prs = raise_prs or set()
        self.pr_pages = pr_pages or {}
        self.graphql_repos: list[str] = []
        self.thread_calls: list[tuple[str, int]] = []

    async def graphql(
        self, app_slug: str, repository: str, *, query: str, variables: dict[str, Any]
    ) -> Any:
        self.graphql_repos.append(repository)
        if repository in self.raise_repos:
            raise GitHubGraphQLError([{"message": "boom"}])
        if repository in self.pr_pages:
            pages = self.pr_pages[repository]
            after = variables.get("after")
            idx = 0 if after is None else int(after.rsplit("-", 1)[1])
            numbers = pages[idx]
            has_next = idx + 1 < len(pages)
            cursor = f"{repository}-cursor-{idx + 1}" if has_next else None
            return {
                "repository": {
                    "pullRequests": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                        "nodes": [{"number": n} for n in numbers],
                    }
                }
            }
        numbers = self.prs_by_repo.get(repository, [])
        return {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"number": n} for n in numbers],
                }
            }
        }

    async def pull_request_review_threads(
        self, app_slug: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        self.thread_calls.append((repo, pull_number))
        if (repo, pull_number) in self.raise_prs:
            raise GitHubGraphQLError([{"message": "thread boom"}])
        return self.threads_by_pr.get((repo, pull_number), [])


# --- gate / dark-state -------------------------------------------------------


def test_frozenset_ceiling_is_sandbox_only():
    # Dark-state guarantee depends on this exact literal; if it changes the
    # real-repo authorization (VOY-1827/1828 CHG) must be re-reviewed.
    assert frozenset({SANDBOX}) == DEDICATED_PAT_FALLBACK_RESOLVE_ALLOWED_REPOSITORIES


def test_gate_splits_by_ceiling():
    allowed, skipped = gate_repos([REAL, SANDBOX, REAL])
    assert allowed == [SANDBOX]
    assert skipped == [REAL]  # duplicate collapsed, real repo rejected


async def test_dark_state_rejects_real_repo_while_ceiling_sandbox_only():
    client = FakeClient(
        prs_by_repo={REAL: [1]},
        threads_by_pr={(REAL, 1): [_thread("T1")]},
    )
    summary = await run_resolve_loop(client, requested_repos=[REAL])
    assert summary.repos_scanned == ()
    assert REAL in summary.repos_skipped
    assert summary.candidates == ()
    assert summary.prs_scanned == 0
    # the rejected repo is never even enumerated
    assert client.graphql_repos == []
    assert client.thread_calls == []


# --- prefilter ---------------------------------------------------------------


async def test_prefilter_keeps_only_resolvable_current_unresolved():
    threads = [
        _thread("ok"),
        _thread("already", resolved=True),
        _thread("stale", outdated=True),
        _thread("forbidden", can_resolve=False),
    ]
    client = FakeClient(prs_by_repo={SANDBOX: [7]}, threads_by_pr={(SANDBOX, 7): threads})
    summary = await run_resolve_loop(client, requested_repos=[SANDBOX])
    assert summary.repos_scanned == (SANDBOX,)
    assert summary.prs_scanned == 1
    assert [c.thread_id for c in summary.candidates] == ["ok"]


# --- empty default -----------------------------------------------------------


async def test_empty_repo_list_resolves_nothing():
    client = FakeClient()
    summary = await run_resolve_loop(client, requested_repos=[])
    assert summary.candidates == ()
    assert summary.prs_scanned == 0
    assert summary.repos_scanned == ()
    assert client.graphql_repos == []


# --- max-resolves cap --------------------------------------------------------


async def test_max_resolves_caps_candidates_without_silent_truncation():
    threads = [_thread(f"t{i}") for i in range(5)]
    client = FakeClient(prs_by_repo={SANDBOX: [1]}, threads_by_pr={(SANDBOX, 1): threads})
    summary = await run_resolve_loop(client, requested_repos=[SANDBOX], max_resolves=2)
    assert len(summary.candidates) == 2
    assert summary.capped is True


# --- per-repo error isolation (codex bot finding #1) -------------------------


async def test_repo_enumeration_error_is_isolated_and_scan_continues():
    repo_a, repo_b = "iterwheel/a", "iterwheel/b"
    ceiling = frozenset({repo_a, repo_b})
    client = FakeClient(
        prs_by_repo={repo_b: [9]},
        threads_by_pr={(repo_b, 9): [_thread("ok")]},
        raise_repos={repo_a},
    )
    summary = await run_resolve_loop(client, requested_repos=[repo_a, repo_b], ceiling=ceiling)
    # repo_a failed but did not abort the run; repo_b still scanned and yielded a candidate.
    assert [e.target for e in summary.errors] == [repo_a]
    assert [c.thread_id for c in summary.candidates] == ["ok"]
    assert summary.repos_scanned == (repo_a, repo_b)


# --- cap must not over-report scanned repos (codex bot finding #2) ------------


async def test_cap_does_not_report_unvisited_repos_as_scanned():
    repo_a, repo_b = "iterwheel/a", "iterwheel/b"
    ceiling = frozenset({repo_a, repo_b})
    client = FakeClient(
        prs_by_repo={repo_a: [1], repo_b: [2]},
        threads_by_pr={
            (repo_a, 1): [_thread(f"t{i}") for i in range(3)],
            (repo_b, 2): [_thread("late")],
        },
        raise_repos=set(),
    )
    summary = await run_resolve_loop(
        client, requested_repos=[repo_a, repo_b], ceiling=ceiling, max_resolves=2
    )
    assert summary.capped is True
    assert len(summary.candidates) == 2
    # repo_b was never visited (cap hit in repo_a) — must not be reported as scanned,
    # nor enumerated at all.
    assert summary.repos_scanned == (repo_a,)
    assert repo_b not in client.graphql_repos
    assert (repo_b, 2) not in client.thread_calls


async def test_per_pr_error_isolated_and_sibling_prs_continue():
    repo = "iterwheel/a"
    ceiling = frozenset({repo})
    client = FakeClient(
        prs_by_repo={repo: [1, 2]},
        threads_by_pr={(repo, 2): [_thread("ok")]},
        raise_prs={(repo, 1)},
    )
    summary = await run_resolve_loop(client, requested_repos=[repo], ceiling=ceiling)
    # PR 1's thread fetch failed but PR 2 still scanned and yielded a candidate.
    assert [e.target for e in summary.errors] == [f"{repo}#1"]
    assert [c.thread_id for c in summary.candidates] == ["ok"]
    assert summary.prs_scanned == 2


async def test_open_pr_enumeration_paginates_across_pages():
    repo = "iterwheel/a"
    ceiling = frozenset({repo})
    client = FakeClient(
        pr_pages={repo: [[1, 2], [3, 4], [5]]},
        threads_by_pr={(repo, 5): [_thread("deep")]},
    )
    summary = await run_resolve_loop(client, requested_repos=[repo], ceiling=ceiling)
    # all three pages enumerated → PR 5 (on page 3) reached and its candidate found.
    assert summary.prs_scanned == 5
    assert [c.thread_id for c in summary.candidates] == ["deep"]


# --- output redaction (trinity code-review P1) -------------------------------


def test_to_public_dict_redacts_non_sandbox_identifiers():
    real = LoopSummary(
        repos_scanned=(REAL,),
        repos_skipped=(),
        prs_scanned=1,
        candidates=(Candidate(REAL, 5, "PRRT_secret"),),
        capped=False,
    )
    entry = real.to_public_dict()["candidates"][0]
    assert entry["pr"] is None
    assert entry["thread_id"] is None
    assert entry["redacted"] is True
    # the raw private identifiers must not appear anywhere in default output
    import json

    assert "PRRT_secret" not in json.dumps(real.to_public_dict())
    # show_raw is an explicit operator opt-in
    raw_entry = real.to_public_dict(show_raw=True)["candidates"][0]
    assert raw_entry["pr"] == 5
    assert raw_entry["thread_id"] == "PRRT_secret"


def test_to_public_dict_keeps_sandbox_identifiers_raw():
    sb = LoopSummary(
        repos_scanned=(SANDBOX,),
        repos_skipped=(),
        prs_scanned=1,
        candidates=(Candidate(SANDBOX, 7, "PRRT_ok"),),
        capped=False,
    )
    entry = sb.to_public_dict()["candidates"][0]
    assert entry["pr"] == 7
    assert entry["thread_id"] == "PRRT_ok"


def test_load_repo_list_rejects_malformed(tmp_path):
    f = tmp_path / "repos.txt"
    f.write_text(f"{SANDBOX}\nnot-a-repo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="OWNER/REPO"):
        load_repo_list(f)


# --- single-instance lock ----------------------------------------------------


def test_single_instance_lock_blocks_second_holder(tmp_path):
    lock = tmp_path / "loop.lock"
    # Outer holds the lock; the inner (second) acquisition must fail fast.
    with (
        single_instance_lock(lock),
        pytest.raises(AlreadyRunningError),
        single_instance_lock(lock),
    ):
        pass


def test_single_instance_lock_reacquirable_after_release(tmp_path):
    lock = tmp_path / "loop.lock"
    with single_instance_lock(lock):
        pass
    with single_instance_lock(lock):  # must not raise
        pass


# --- repo list parsing -------------------------------------------------------


def test_load_repo_list_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "repos.txt"
    f.write_text(
        f"# header\n{SANDBOX}\n\n  {REAL}  # inline\n# trailing\n",
        encoding="utf-8",
    )
    assert load_repo_list(f) == [SANDBOX, REAL]


# --- CLI orchestration (trinity code-review: command was untested) ------------

import contextlib  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import voyager.core.config as _config_mod  # noqa: E402
import voyager.core.countdown_loop as _loop_mod  # noqa: E402
import voyager.core.github_app as _gh_mod  # noqa: E402
from voyager.cli import app as _cli_app  # noqa: E402


def _fake_cfg(*, apps: dict[str, Any], allowed: tuple[str, ...]) -> Any:
    return SimpleNamespace(
        apps=apps,
        countdown=SimpleNamespace(
            dedicated_pat_fallback=SimpleNamespace(allowed_repositories=allowed)
        ),
    )


class _FakeAppClient:
    def __init__(self, apps: Any) -> None: ...

    async def aclose(self) -> None: ...


def test_cli_app_not_configured_exits_1(monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setattr(
        _config_mod, "load_config", lambda *_a, **_k: _fake_cfg(apps={}, allowed=())
    )
    result = CliRunner().invoke(_cli_app, ["countdown", "resolve-loop"])
    assert result.exit_code == 1
    assert "not configured" in result.output


def test_cli_already_running_exits_0(monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setattr(
        _config_mod,
        "load_config",
        lambda *_a, **_k: _fake_cfg(apps={"iterwheel-countdown": object()}, allowed=(SANDBOX,)),
    )

    def _raising_lock(_path):
        raise AlreadyRunningError("held elsewhere")

    monkeypatch.setattr(_loop_mod, "single_instance_lock", _raising_lock)
    result = CliRunner().invoke(_cli_app, ["countdown", "resolve-loop"])
    assert result.exit_code == 0  # lock contention is not an error
    assert "already running" in result.output


@pytest.mark.parametrize(
    ("extra_args", "expected"),
    [
        ([], ["cfg/repo"]),  # no --repos → TOML allowed_repositories
        ("FROM_FILE", ["file/repo"]),  # --repos <file> → file contents
    ],
)
def test_cli_repo_source_selection(monkeypatch, tmp_path, extra_args, expected):
    from typer.testing import CliRunner

    monkeypatch.setattr(
        _config_mod,
        "load_config",
        lambda *_a, **_k: _fake_cfg(apps={"iterwheel-countdown": object()}, allowed=("cfg/repo",)),
    )
    monkeypatch.setattr(_loop_mod, "single_instance_lock", lambda _p: contextlib.nullcontext())
    monkeypatch.setattr(_gh_mod, "GitHubAppClient", _FakeAppClient)

    captured: dict[str, Any] = {}

    async def _fake_run(client, *, requested_repos, **_k):
        captured["requested"] = list(requested_repos)
        return LoopSummary((), (), 0, (), False)

    monkeypatch.setattr(_loop_mod, "run_resolve_loop", _fake_run)

    args = ["countdown", "resolve-loop"]
    if extra_args == "FROM_FILE":
        f = tmp_path / "repos.txt"
        f.write_text("file/repo\n", encoding="utf-8")
        args += ["--repos", str(f)]
    result = CliRunner().invoke(_cli_app, args)
    assert result.exit_code == 0, result.output
    assert captured["requested"] == expected
