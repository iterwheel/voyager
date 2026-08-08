"""Unit tests for voyager.core.merge_loop."""

from __future__ import annotations

import json as _json

import pytest

from voyager.core.merge_loop import (
    _AGENT_PR_PAGE_QUERY,
    _PR_THREADS_QUERY,
    MERGE_ALLOWED_REPOS,
    MergeDecision,
    MergeLoopSummary,
    PrSnapshot,
    make_merge_read_gql,
    merge_allowed_repos,
    parse_readiness,
    run_merge_loop,
    should_merge,
    snapshots_for_repo,
)
from voyager.core.resolve_conversation import ResolveConversationError


class TestMergeAllowedRepos:
    def test_builtin_is_sandbox_only(self):
        assert frozenset({"iterwheel/voyager-sandbox"}) == MERGE_ALLOWED_REPOS

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
        assert parsed is not None
        assert parsed[0] == 2

    def test_missing_marker_returns_none(self):
        assert parse_readiness(READINESS_BODY.split("\n", 1)[1]) is None

    def test_missing_head_sha_returns_none(self):
        body = READINESS_BODY.replace("Head SHA", "Head Something")
        assert parse_readiness(body) is None

    def test_short_sha_returns_none(self):
        body = READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", "a96782f")
        assert parse_readiness(body) is None


HEAD = "a" * 40


def snap(**overrides) -> PrSnapshot:
    """A fully-green agent PR snapshot; tests break one field at a time."""
    base = {
        "pr_id": "PR_x",
        "number": 1,
        "author": "ryosaeba1985",
        "is_draft": False,
        "head_oid": HEAD,
        "checks_state": "SUCCESS",
        "unresolved_threads": 0,
        "readiness_stage": 3,
        "readiness_head": HEAD,
    }
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

    def test_multi_page_pr_enumeration(self):
        """Pagination: first page hasNextPage=True, second page final."""
        pr1 = _pr_node(number=1)
        pr2 = _pr_node(number=2)

        def gql(query, variables):
            if query is _AGENT_PR_PAGE_QUERY:
                after = variables.get("after")
                if after is None:
                    # First page
                    return {
                        "repository": {
                            "pullRequests": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                                "nodes": [pr1],
                            }
                        }
                    }
                else:
                    # Second page
                    return {
                        "repository": {
                            "pullRequests": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [pr2],
                            }
                        }
                    }
            if query is _PR_THREADS_QUERY:
                return {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [{"isResolved": True}],
                            }
                        }
                    }
                }
            raise AssertionError(f"unexpected query: {query[:40]}")

        snapshots = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert len(snapshots) == 2
        assert snapshots[0].number == 1
        assert snapshots[1].number == 2

    def test_multi_page_thread_count(self):
        """Thread pagination: two pages with unresolved threads sum correctly."""

        def gql(query, variables):
            if query is _AGENT_PR_PAGE_QUERY:
                return {
                    "repository": {
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [_pr_node()],
                        }
                    }
                }
            if query is _PR_THREADS_QUERY:
                after = variables.get("after")
                if after is None:
                    # First thread page: 2 unresolved, 1 resolved
                    return {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": True, "endCursor": "thread_cursor"},
                                    "nodes": [
                                        {"isResolved": False},
                                        {"isResolved": False},
                                        {"isResolved": True},
                                    ],
                                }
                            }
                        }
                    }
                else:
                    # Second thread page: 1 unresolved
                    return {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [{"isResolved": False}],
                                }
                            }
                        }
                    }
            raise AssertionError(f"unexpected query: {query[:40]}")

        (snapshot,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert snapshot.unresolved_threads == 3  # 2 from first page + 1 from second

    def test_thread_read_failure_returns_none(self):
        """Thread read error (ResolveConversationError) returns None for unresolved_threads."""
        call_count = [0]

        def gql(query, variables):
            if query is _AGENT_PR_PAGE_QUERY:
                return {
                    "repository": {
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [_pr_node()],
                        }
                    }
                }
            if query is _PR_THREADS_QUERY:
                call_count[0] += 1
                raise ResolveConversationError("simulated thread read failure")
            raise AssertionError(f"unexpected query: {query[:40]}")

        (snapshot,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert snapshot.unresolved_threads is None
        assert call_count[0] == 1  # thread query was attempted


class TestMergeReadGqlWhitelist:
    def test_refuses_unknown_query(self):
        gql = make_merge_read_gql("tok")
        with pytest.raises(ResolveConversationError):
            gql("query { viewer { login } }", {})


class TestMergePr:
    def test_success(self):
        from voyager.core.merge_loop import _MERGE_MUTATION, merge_pr

        def gql(query, variables):
            assert query is _MERGE_MUTATION
            assert variables == {"prId": "PR_1", "expectedHeadOid": HEAD}
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        assert merge_pr(gql, "PR_1", HEAD) == ("merged", "")

    def test_api_error_is_merge_failed_not_raise(self):
        from voyager.core.merge_loop import merge_pr

        def gql(query, variables):
            raise ResolveConversationError("merge-loop GraphQL returned 1 error(s)")

        action, msg = merge_pr(gql, "PR_1", HEAD)
        assert action == "merge_failed"
        assert "error" in msg

    def test_unmerged_response_is_merge_failed(self):
        from voyager.core.merge_loop import merge_pr

        def gql(query, variables):
            return {"mergePullRequest": {"pullRequest": {"merged": False}}}

        assert merge_pr(gql, "PR_1", HEAD)[0] == "merge_failed"

    def test_mutation_client_refuses_unknown_operation(self):
        from voyager.core.merge_loop import make_merge_gql

        gql = make_merge_gql("tok")
        with pytest.raises(ResolveConversationError):
            gql(
                'mutation { closePullRequest(input: {pullRequestId: "x"}) { clientMutationId } }',
                {},
            )


def _green_pr(number):
    readiness = {
        "author": {"login": "iterwheel-clearance"},
        "body": READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", HEAD),
    }
    return _pr_node(number=number, comments=[readiness])


class TestRunMergeLoop:
    def _run(self, pr_nodes, thread_pages=None, merge_gql=None, **kwargs):
        read = _fake_gql(pr_nodes, thread_pages=thread_pages or {})
        merges: list[str] = []

        if merge_gql is None:

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

    def test_merge_failed_path_consumes_cap(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        # First PR: merge attempt fails (mutation returns merged=False)
        # Second PR: should be capped because first attempt consumed the cap
        calls: list[str] = []

        def merge_gql_fails(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": False}}}

        summary, _ = self._run(
            [_green_pr(1), _green_pr(2)],
            thread_pages={1: [], 2: []},
            merge_gql=merge_gql_fails,
            max_merges=1,
        )
        # First PR: merge attempt was made, returned merge_failed
        # Second PR: capped (attempt cap already consumed by first)
        assert len(calls) == 1  # only one merge attempt (for first PR)
        assert summary.capped is True
        # Verify the decision structure
        assert len(summary.decisions) == 2
        pr1_decision = next(d for d in summary.decisions if d.pr == 1)
        pr2_decision = next(d for d in summary.decisions if d.pr == 2)
        assert pr1_decision.action == "merge_failed"
        assert pr2_decision.action == "skipped"
        assert pr2_decision.reason == "capped"
        assert summary.merged == 0

    def test_merge_failed_recorded_in_audit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql([_green_pr(1)], thread_pages={1: []})

        def merge_gql(query, variables):
            return {"mergePullRequest": {"pullRequest": {"merged": False}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"], read_gql=read, merge_gql=merge_gql, audit_path=audit
        )
        (line,) = audit.read_text().strip().splitlines()
        record = _json.loads(line)
        assert record["action"] == "merge_failed"
        assert record["pr"] == 1
        assert record["repo"] == "frankyxhl/fx_bin"
        assert summary.merged == 0
        assert len(summary.decisions) == 1
