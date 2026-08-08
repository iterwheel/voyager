"""Unit tests for voyager.core.merge_loop."""

from __future__ import annotations

import json as _json
import os

import pytest
from typer.testing import CliRunner

from voyager.core.merge_loop import (
    _AGENT_PR_PAGE_QUERY,
    _BASE_FRESHNESS_QUERY,
    _PR_BASE_QUERY,
    _PR_COMMENTS_QUERY,
    _PR_THREADS_QUERY,
    ALLOWED_BASE_REFS,
    MERGE_ALLOWED_REPOS,
    MergeDecision,
    MergeLoopSummary,
    PrSnapshot,
    _base_behind_by,
    _current_base_ref,
    _is_clearance_bot,
    _readiness_for_pr,
    _unresolved_thread_count,
    make_merge_read_gql,
    merge_allowed_repos,
    parse_readiness,
    run_merge_loop,
    should_merge,
    snapshots_for_repo,
)
from voyager.core.resolve_conversation import MACHINE_ACCOUNT, ResolveConversationError


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
        "base_behind": 0,
        "unresolved_threads": 0,
        "readiness_stage": 3,
        "readiness_head": HEAD,
        "base_ref": "main",
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
            ({"base_behind": None}, "base_freshness_unreadable"),
            ({"base_behind": 2}, "base_stale"),
            ({"unresolved_threads": None}, "threads_unreadable"),
            ({"unresolved_threads": 2}, "threads_unresolved"),
            ({"readiness_stage": None, "readiness_head": None}, "readiness_missing"),
            ({"readiness_stage": 2}, "readiness_not_ready"),
            ({"readiness_head": "b" * 40}, "readiness_stale_head"),
            ({"base_ref": "release/1.x"}, "base_not_allowed"),
            ({"base_ref": ""}, "base_not_allowed"),
        ],
    )
    def test_each_condition_fails_closed(self, overrides, reason):
        assert should_merge(snap(**overrides)) == reason

    def test_stage_4_ready_for_merge_also_ok(self):
        assert should_merge(snap(readiness_stage=4)) == "ok"

    def test_allowed_base_refs_is_main_only(self):
        assert frozenset({"main"}) == ALLOWED_BASE_REFS


def _pr_node(
    number=1, author="ryosaeba1985", draft=False, checks="SUCCESS", head=HEAD, base_ref="main"
):
    return {
        "id": f"PR_{number}",
        "number": number,
        "isDraft": draft,
        "headRefOid": head,
        "baseRefName": base_ref,
        "author": {"login": author},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]},
    }


def _readiness_comment(head=HEAD):
    return {
        "author": {"login": "iterwheel-clearance", "__typename": "Bot"},
        "body": READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", head),
    }


def _readiness_pages(*numbers):
    """comment_pages entry for each *numbers*: one page holding a fresh readiness comment."""
    return {n: [[_readiness_comment()]] for n in numbers}


def _identity_gql_ok(query, variables):
    return {"viewer": {"login": MACHINE_ACCOUNT}}


def _fake_gql(
    pr_nodes, thread_pages=None, comment_pages=None, behind_by=0, current_base_ref="main"
):
    """Return a gql callable serving PR pages, thread pages, comment pages,
    base-freshness compares (default behindBy 0 — base is up to date), and
    the apply-time current-baseRefName read (default "main" — unchanged from
    the snapshot, so happy-path tests are unaffected).

    behind_by may be a fixed int (every compare returns the same value) or a
    list consumed left-to-right across successive compares — e.g. [0, 2] for
    a snapshot-time read of 0 followed by an apply-time re-read of 2. The
    last element repeats once the list is exhausted.

    current_base_ref=None simulates an apply-time baseRefName read fault
    (null pullRequest) — mirrors the null-ref/compare fault shape used for
    behind_by faults."""
    thread_pages = thread_pages or {}
    comment_pages = comment_pages or {}
    behind_sequence = list(behind_by) if isinstance(behind_by, list) else None

    def gql(query, variables):
        if query is _BASE_FRESHNESS_QUERY:
            if behind_sequence is not None:
                value = behind_sequence.pop(0) if len(behind_sequence) > 1 else behind_sequence[0]
            else:
                value = behind_by
            return {"repository": {"ref": {"compare": {"behindBy": value}}}}
        if query is _PR_BASE_QUERY:
            if current_base_ref is None:
                return {"repository": {"pullRequest": None}}
            return {"repository": {"pullRequest": {"baseRefName": current_base_ref}}}
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
        if query is _PR_COMMENTS_QUERY:
            pages = comment_pages.get(variables["number"], [[]])
            after = variables.get("after")
            idx = 0 if after is None else int(after)
            nodes = pages[idx] if idx < len(pages) else []
            has_next = idx + 1 < len(pages)
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {
                                "hasNextPage": has_next,
                                "endCursor": str(idx + 1) if has_next else None,
                            },
                            "nodes": nodes,
                        }
                    }
                }
            }
        raise AssertionError(f"unexpected query: {query[:40]}")

    return gql


class TestSnapshotsForRepo:
    def test_green_agent_pr_snapshot(self):
        gql = _fake_gql(
            [_pr_node()],
            thread_pages={1: [{"isResolved": True}]},
            comment_pages={1: [[_readiness_comment()]]},
        )
        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert s.author == "ryosaeba1985"
        assert s.unresolved_threads == 0
        assert s.readiness_stage == 3
        assert s.readiness_head == HEAD
        assert s.base_ref == "main"
        assert s.base_behind == 0

    def test_missing_base_ref_name_is_empty_string(self):
        node = _pr_node()
        del node["baseRefName"]
        gql = _fake_gql([node], thread_pages={1: []}, comment_pages={1: [[]]})
        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert s.base_ref == ""

    def test_readiness_from_wrong_author_ignored(self):
        impostor = {"author": {"login": "someone"}, "body": READINESS_BODY}
        gql = _fake_gql([_pr_node()], thread_pages={1: []}, comment_pages={1: [[impostor]]})
        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert s.readiness_stage is None

    def test_non_agent_pr_skips_thread_and_comments_fetch(self):
        calls = []
        inner = _fake_gql([_pr_node(author="frankyxhl")])

        def spy(query, variables):
            calls.append(query)
            return inner(query, variables)

        (s,) = snapshots_for_repo(spy, "frankyxhl/fx_bin")
        assert s.unresolved_threads is None
        assert s.readiness_stage is None
        assert s.base_behind is None
        assert _PR_THREADS_QUERY not in calls
        assert _PR_COMMENTS_QUERY not in calls
        assert _BASE_FRESHNESS_QUERY not in calls

    def test_base_compare_read_failure_yields_base_freshness_unreadable_skip(self):
        """A cheap-green PR whose base-freshness compare fails to read must fail
        closed to base_behind=None, which should_merge reports as
        base_freshness_unreadable — never a silent 'ok' on an unverified base."""

        def gql(query, variables):
            if query is _BASE_FRESHNESS_QUERY:
                raise ResolveConversationError("simulated compare read failure")
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
                return {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            if query is _PR_COMMENTS_QUERY:
                return {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [_readiness_comment()],
                            }
                        }
                    }
                }
            raise AssertionError(f"unexpected query: {query[:40]}")

        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert s.base_behind is None
        assert should_merge(s) == "base_freshness_unreadable"

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
            if query is _BASE_FRESHNESS_QUERY:
                return {"repository": {"ref": {"compare": {"behindBy": 0}}}}
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
            if query is _PR_COMMENTS_QUERY:
                return {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
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
            if query is _BASE_FRESHNESS_QUERY:
                return {"repository": {"ref": {"compare": {"behindBy": 0}}}}
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
            if query is _PR_COMMENTS_QUERY:
                return {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
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
            if query is _BASE_FRESHNESS_QUERY:
                return {"repository": {"ref": {"compare": {"behindBy": 0}}}}
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
            if query is _PR_COMMENTS_QUERY:
                return {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            raise AssertionError(f"unexpected query: {query[:40]}")

        (snapshot,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert snapshot.unresolved_threads is None
        assert call_count[0] == 1  # thread query was attempted

    def test_comments_read_failure_yields_readiness_missing_skip(self):
        """A busy PR whose readiness comments page fails to read must fail closed to
        (None, None), which should_merge then reports as readiness_missing — never a
        silent 'ok'."""

        def gql(query, variables):
            if query is _BASE_FRESHNESS_QUERY:
                return {"repository": {"ref": {"compare": {"behindBy": 0}}}}
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
            if query is _PR_COMMENTS_QUERY:
                raise ResolveConversationError("simulated comments read failure")
            raise AssertionError(f"unexpected query: {query[:40]}")

        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert (s.readiness_stage, s.readiness_head) == (None, None)
        assert should_merge(s) == "readiness_missing"

    def test_truncated_comments_pagination_after_match_still_uses_match(self):
        """Finding 1 (round 7): under first-match semantics (mirroring
        GitHubApp.upsert_issue_comment()'s first-match PATCH target), a
        readiness match found on the page already read is conclusively the
        first match — truncated pagination (hasNextPage=true, null
        endCursor) *after* that match does not discard it. This replaces the
        old "last matching comment wins" expectation of (None, None)."""

        def gql(query, variables):
            if query is _BASE_FRESHNESS_QUERY:
                return {"repository": {"ref": {"compare": {"behindBy": 0}}}}
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
            if query is _PR_COMMENTS_QUERY:
                return {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasNextPage": True, "endCursor": None},
                                "nodes": [_readiness_comment()],
                            }
                        }
                    }
                }
            raise AssertionError(f"unexpected query: {query[:40]}")

        (s,) = snapshots_for_repo(gql, "frankyxhl/fx_bin")
        assert (s.readiness_stage, s.readiness_head) == (3, HEAD)
        assert should_merge(s) == "ok"


class TestThreadCountForPr:
    """Finding 2: truncated pagination (hasNextPage true but no usable cursor to
    advance) must fail closed to None, never a partial in-range count."""

    def test_null_end_cursor_with_more_pages_returns_none(self):
        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": None},
                            "nodes": [{"isResolved": False}],
                        }
                    }
                }
            }

        assert _unresolved_thread_count(gql, "frankyxhl/fx_bin", 1) is None

    def test_repeated_end_cursor_returns_none(self):
        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "stuck"},
                            "nodes": [{"isResolved": False}],
                        }
                    }
                }
            }

        assert _unresolved_thread_count(gql, "frankyxhl/fx_bin", 1) is None


class TestReadinessForPr:
    """_readiness_for_pr pages ALL issue comments; the clearance bot upserts its
    readiness comment in place, so on a busy PR it can sit on an early page while
    dozens of later comments arrive after it."""

    def test_marker_on_first_of_two_pages_is_still_found(self):
        page1 = [_readiness_comment()]
        # Many later, unrelated comments pushed the marker off a fixed-size window
        # in the old (comments last: 50) implementation — pagination must not.
        page2 = [{"author": {"login": "someone"}, "body": f"comment {i}"} for i in range(60)]

        def gql(query, variables):
            assert query is _PR_COMMENTS_QUERY
            after = variables.get("after")
            if after is None:
                return {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "c2"},
                                "nodes": page1,
                            }
                        }
                    }
                }
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": page2,
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (3, HEAD)

    def test_read_failure_returns_none_none(self):
        def gql(query, variables):
            raise ResolveConversationError("boom")

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (None, None)

    def test_null_pull_request_returns_none_none(self):
        def gql(query, variables):
            return {"repository": {"pullRequest": None}}

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (None, None)

    def test_null_end_cursor_with_no_match_yet_fails_closed(self):
        """Finding 1 (round 7): under first-match semantics, a null endCursor
        with hasNextPage still true only needs to fail closed when NO marker
        comment has been found on the readable page(s) — an unreached later
        page might have held one. (Superseded scenario: a match found on the
        readable page before truncation is now trusted — see
        test_match_found_before_truncation_is_trusted_not_discarded — because
        it is, by pagination order, definitively the first match.)"""

        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": True, "endCursor": None},
                            "nodes": [{"author": {"login": "someone"}, "body": "unrelated"}],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (None, None)

    def test_repeated_end_cursor_with_no_match_yet_fails_closed(self):
        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "stuck"},
                            "nodes": [{"author": {"login": "someone"}, "body": "unrelated"}],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (None, None)

    def test_match_found_before_truncation_is_trusted_not_discarded(self):
        """Finding 1 (round 7): mirrors GitHubApp.upsert_issue_comment()
        (voyager/core/github_app.py ~L816-846), which stops at the FIRST
        marker match. A match found on an early page is conclusively the
        first match regardless of what happens on later pages, so truncated
        pagination *after* the match is irrelevant and must not discard it.
        This replaces the old "last matching comment wins" test, which
        expected (None, None) here because a later page could have held a
        more-recent match that superseded this one."""

        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": True, "endCursor": None},
                            "nodes": [_readiness_comment()],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (3, HEAD)

    def test_duplicate_markers_first_fresh_wins_over_later_stale(self):
        """GitHubApp.upsert_issue_comment() (voyager/core/github_app.py ~L816-846)
        scans comments oldest-first and PATCHes the FIRST comment matching
        (marker in body AND author == bot) — a later duplicate marker comment
        (plausible leftover from older non-paginated upsert behavior) is never
        touched again by the writer. The reader must trust that same first
        comment, not whichever one happens to parse last."""
        first_fresh = _readiness_comment(head="b" * 40)
        later_stale_duplicate = _readiness_comment(head="c" * 40)

        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [first_fresh, later_stale_duplicate],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (3, "b" * 40)

    def test_first_marker_unparseable_fails_closed_ignoring_later_parseable_duplicate(self):
        """The first clearance-authored marker comment is the one upsert
        updates; if it fails to parse (stage/head missing), fail closed
        instead of falling through to a later, well-formed duplicate."""
        first_unparseable = {
            "author": {"login": "iterwheel-clearance", "__typename": "Bot"},
            "body": "<!-- iterwheel:clearance-readiness -->\nmalformed, no stage or head",
        }
        later_parseable = _readiness_comment()

        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [first_unparseable, later_parseable],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (None, None)

    def test_spoofed_user_marker_ignored_bot_marker_wins(self):
        """Round-8 finding: GraphQL represents a GitHub App bot's login as the
        PLAIN slug (no "[bot]" suffix -- that suffix is a REST-only rendering),
        so login-only matching cannot tell the real clearance bot apart from a
        plain USER account that happens to share its login. upsert_issue_comment()
        (github_app.py ~L816-846) only ever PATCHes a comment whose REST author
        is "iterwheel-clearance[bot]" -- a spoofed/duplicate User-typed comment
        with a valid marker body sitting earlier in comment order is never
        touched by the writer. Trusting it here would consume first-match and
        permanently shadow the comment the writer actually keeps current."""
        spoof = {
            "author": {"login": "iterwheel-clearance", "__typename": "User"},
            "body": READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", "b" * 40),
        }
        real_bot = _readiness_comment(head="c" * 40)

        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [spoof, real_bot],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (3, "c" * 40)

    def test_bot_typed_other_slug_ignored(self):
        other_bot = {
            "author": {"login": "some-other-app", "__typename": "Bot"},
            "body": READINESS_BODY,
        }

        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [other_bot],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (None, None)

    def test_missing_typename_fails_closed(self):
        no_typename = {
            "author": {"login": "iterwheel-clearance"},
            "body": READINESS_BODY,
        }

        def gql(query, variables):
            return {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [no_typename],
                        }
                    }
                }
            }

        assert _readiness_for_pr(gql, "frankyxhl/fx_bin", 1) == (None, None)


class TestIsClearanceBot:
    """_is_clearance_bot is the actor-type-verified predicate _readiness_for_pr
    uses in place of a bare login-in-set check (round-8 fix)."""

    def test_bot_plain_slug_is_true(self):
        assert _is_clearance_bot({"login": "iterwheel-clearance", "__typename": "Bot"})

    def test_bot_bracket_suffixed_login_is_true(self):
        assert _is_clearance_bot({"login": "iterwheel-clearance[bot]", "__typename": "Bot"})

    def test_user_typed_same_login_is_false(self):
        assert not _is_clearance_bot({"login": "iterwheel-clearance", "__typename": "User"})

    def test_bot_other_slug_is_false(self):
        assert not _is_clearance_bot({"login": "some-other-app", "__typename": "Bot"})

    def test_missing_typename_is_false(self):
        assert not _is_clearance_bot({"login": "iterwheel-clearance"})

    def test_missing_login_is_false(self):
        assert not _is_clearance_bot({"__typename": "Bot"})

    def test_none_author_is_false(self):
        assert not _is_clearance_bot(None)


class TestBaseBehindBy:
    """_base_behind_by fails closed to None on any read fault, a null ref/compare,
    or a non-int behindBy — a stale/wrong value here would let a rebase merge
    land on an untested base while checks_state still reads SUCCESS."""

    def test_returns_behind_by(self):
        def gql(query, variables):
            assert query is _BASE_FRESHNESS_QUERY
            assert variables == {
                "owner": "frankyxhl",
                "name": "fx_bin",
                "baseRef": "main",
                "prHeadRef": "refs/pull/1/head",
            }
            return {"repository": {"ref": {"compare": {"behindBy": 2}}}}

        assert _base_behind_by(gql, "frankyxhl/fx_bin", "main", 1) == 2

    def test_read_failure_returns_none(self):
        def gql(query, variables):
            raise ResolveConversationError("boom")

        assert _base_behind_by(gql, "frankyxhl/fx_bin", "main", 1) is None

    def test_null_ref_returns_none(self):
        def gql(query, variables):
            return {"repository": {"ref": None}}

        assert _base_behind_by(gql, "frankyxhl/fx_bin", "main", 1) is None

    def test_null_compare_returns_none(self):
        def gql(query, variables):
            return {"repository": {"ref": {"compare": None}}}

        assert _base_behind_by(gql, "frankyxhl/fx_bin", "main", 1) is None

    def test_non_int_behind_by_returns_none(self):
        def gql(query, variables):
            return {"repository": {"ref": {"compare": {"behindBy": "not-a-number"}}}}

        assert _base_behind_by(gql, "frankyxhl/fx_bin", "main", 1) is None


class TestCurrentBaseRef:
    """_current_base_ref fails closed to None on any read fault, a missing
    pullRequest, or a non-str baseRefName — a retargeted PR must never read
    as unchanged (P2 round 14: expectedHeadOid pins only the head, so a base
    retarget between the freshness read and mergePullRequest could land a
    PR outside ALLOWED_BASE_REFS)."""

    def test_returns_base_ref(self):
        def gql(query, variables):
            assert query is _PR_BASE_QUERY
            assert variables == {"owner": "frankyxhl", "name": "fx_bin", "number": 1}
            return {"repository": {"pullRequest": {"baseRefName": "main"}}}

        assert _current_base_ref(gql, "frankyxhl/fx_bin", 1) == "main"

    def test_read_failure_returns_none(self):
        def gql(query, variables):
            raise ResolveConversationError("boom")

        assert _current_base_ref(gql, "frankyxhl/fx_bin", 1) is None

    def test_null_pull_request_returns_none(self):
        def gql(query, variables):
            return {"repository": {"pullRequest": None}}

        assert _current_base_ref(gql, "frankyxhl/fx_bin", 1) is None

    def test_non_str_base_ref_returns_none(self):
        def gql(query, variables):
            return {"repository": {"pullRequest": {"baseRefName": None}}}

        assert _current_base_ref(gql, "frankyxhl/fx_bin", 1) is None


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
    return _pr_node(number=number)


class TestRunMergeLoop:
    def _run(
        self,
        pr_nodes,
        thread_pages=None,
        comment_pages=None,
        merge_gql=None,
        identity_gql=None,
        behind_by=0,
        **kwargs,
    ):
        read = _fake_gql(
            pr_nodes,
            thread_pages=thread_pages or {},
            comment_pages=comment_pages or {},
            behind_by=behind_by,
        )
        merges: list[str] = []

        if merge_gql is None:

            def merge_gql(query, variables):
                merges.append(variables["prId"])
                return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=identity_gql or _identity_gql_ok,
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
        summary, merges = self._run(
            [_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1)
        )
        assert merges == ["PR_1"]
        assert summary.merged == 1

    def test_dry_run_issues_no_mutation(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run(
            [_green_pr(1)],
            thread_pages={1: []},
            comment_pages=_readiness_pages(1),
            dry_run=True,
        )
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
            comment_pages=_readiness_pages(1, 2),
            max_merges=1,
        )
        assert len(merges) == 1
        assert summary.capped is True

    def test_second_pr_suppressed_after_first_merge_succeeds(self, monkeypatch):
        """Finding P2 round 5: a snapshot's base_behind is read before any
        mutation. Once one merge lands in a repo, main has moved and every
        other cached snapshot in that repo is stale — it must not be
        rebase-merged onto an untested base this cycle."""
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run(
            [_green_pr(1), _green_pr(2)],
            thread_pages={1: [], 2: []},
            comment_pages=_readiness_pages(1, 2),
            max_merges=3,
        )
        assert merges == ["PR_1"]
        assert [(d.action, d.reason) for d in summary.decisions] == [
            ("merged", ""),
            ("skipped", "base_moved_by_merge"),
        ]

    def test_merge_failed_does_not_suppress_next_pr(self, monkeypatch):
        """merge_failed does not move the base (the mutation never applied),
        so it must not suppress the next candidate in the same repo."""
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        calls: list[str] = []

        def merge_gql_fails(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": False}}}

        summary, _ = self._run(
            [_green_pr(1), _green_pr(2)],
            thread_pages={1: [], 2: []},
            comment_pages=_readiness_pages(1, 2),
            merge_gql=merge_gql_fails,
            max_merges=2,
        )
        assert calls == ["PR_1", "PR_2"]
        assert [d.action for d in summary.decisions] == ["merge_failed", "merge_failed"]

    def test_dry_run_does_not_suppress(self, monkeypatch):
        """would_merge never mutates GitHub, so the base never moves and
        dry-run must list every green PR, not just the first."""
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, merges = self._run(
            [_green_pr(1), _green_pr(2)],
            thread_pages={1: [], 2: []},
            comment_pages=_readiness_pages(1, 2),
            dry_run=True,
            max_merges=3,
        )
        assert merges == []
        assert [d.action for d in summary.decisions] == ["would_merge", "would_merge"]

    def test_audit_lines_written(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))

        def merge_gql(query, variables):
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=audit,
        )
        # fx_bin is not in _RAW_IDENTIFIER_REPOS: audit records must be
        # redacted like resolve-loop's (countdown_loop.py) — no pr/reason/head.
        intent_line, outcome_line = audit.read_text().strip().splitlines()
        intent = _json.loads(intent_line)
        outcome = _json.loads(outcome_line)
        assert intent == {
            "ts": intent["ts"],
            "dry_run": False,
            "repo": "frankyxhl/fx_bin",
            "action": "merge_intent",
            "redacted": True,
        }
        assert outcome == {
            "ts": outcome["ts"],
            "dry_run": False,
            "repo": "frankyxhl/fx_bin",
            "action": "merged",
            "redacted": True,
        }

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
            comment_pages=_readiness_pages(1, 2),
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
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))

        def merge_gql(query, variables):
            return {"mergePullRequest": {"pullRequest": {"merged": False}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=audit,
        )
        intent_line, outcome_line = audit.read_text().strip().splitlines()
        intent = _json.loads(intent_line)
        outcome = _json.loads(outcome_line)
        # fx_bin is not in _RAW_IDENTIFIER_REPOS: redacted, no pr/reason/head.
        assert intent["action"] == "merge_intent"
        assert intent.get("redacted") is True
        assert "pr" not in intent
        assert outcome["action"] == "merge_failed"
        assert outcome.get("redacted") is True
        assert "pr" not in outcome
        assert outcome["repo"] == "frankyxhl/fx_bin"
        assert summary.merged == 0
        assert len(summary.decisions) == 1

    def test_sandbox_repo_audit_is_raw(self, monkeypatch, tmp_path):
        """iterwheel/voyager-sandbox IS in _RAW_IDENTIFIER_REPOS: audit records
        keep raw pr/reason (prior art: countdown_loop.py redaction contract)."""
        monkeypatch.delenv("VOYAGER_MERGE_EXTRA_REPOS", raising=False)
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))

        def merge_gql(query, variables):
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        run_merge_loop(
            ["iterwheel/voyager-sandbox"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=audit,
        )
        intent_line, outcome_line = audit.read_text().strip().splitlines()
        intent = _json.loads(intent_line)
        outcome = _json.loads(outcome_line)
        assert intent["action"] == "merge_intent"
        assert intent["pr"] == 1
        assert intent["reason"] == ""
        assert intent["repo"] == "iterwheel/voyager-sandbox"
        assert "redacted" not in intent
        assert outcome["action"] == "merged"
        assert outcome["pr"] == 1
        assert outcome["reason"] == ""
        assert outcome["repo"] == "iterwheel/voyager-sandbox"
        assert "redacted" not in outcome

    def test_all_repos_fail_enumeration_is_systemic_failure(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")

        def failing_gql(query, variables):
            raise ResolveConversationError("boom")

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=failing_gql,
            merge_gql=lambda query, variables: {},
            identity_gql=_identity_gql_ok,
            audit_path=None,
        )
        assert summary.systemic_failure is True
        assert summary.to_public_dict()["systemic_failure"] is True

    def test_successful_scan_is_not_systemic_failure(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        summary, _ = self._run([_green_pr(1)], thread_pages={1: []})
        assert summary.systemic_failure is False
        assert summary.to_public_dict()["systemic_failure"] is False

    def test_all_repos_ceiling_skipped_is_not_systemic_failure(self, monkeypatch):
        monkeypatch.delenv("VOYAGER_MERGE_EXTRA_REPOS", raising=False)
        summary, _ = self._run([_green_pr(1)], thread_pages={1: []})
        assert summary.repos_scanned == ()
        assert summary.systemic_failure is False


class TestApplyTimeBaseFreshness:
    """Finding P2 round 9: _base_behind_by is read once in snapshots_for_repo;
    main can advance between that read and merge_pr (human push, release
    bot, another loop instance). expectedHeadOid only pins the PR head, so
    the live path re-reads base_behind immediately before the write-ahead
    intent + merge_pr call. dry-run issues no mutation, so it must not
    re-read — the snapshot value is what's reported."""

    def test_apply_time_base_advance_skips_with_no_mutation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql(
            [_green_pr(1)],
            thread_pages={1: []},
            comment_pages=_readiness_pages(1),
            behind_by=[0, 2],
        )
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=audit,
        )
        assert calls == []  # merge_pr never called — no mutation issued
        (d,) = summary.decisions
        assert (d.action, d.reason) == ("skipped", "base_stale_at_apply")
        lines = [_json.loads(line) for line in audit.read_text().strip().splitlines()]
        assert all(line["action"] != "merge_intent" for line in lines)

    def test_apply_time_read_failure_skips_unreadable(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        read = _fake_gql(
            [_green_pr(1)],
            thread_pages={1: []},
            comment_pages=_readiness_pages(1),
            behind_by=[0, None],
        )
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=None,
        )
        assert calls == []
        (d,) = summary.decisions
        assert (d.action, d.reason) == ("skipped", "base_freshness_unreadable")

    def test_apply_time_reread_still_fresh_merge_proceeds(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        read = _fake_gql(
            [_green_pr(1)],
            thread_pages={1: []},
            comment_pages=_readiness_pages(1),
            behind_by=[0, 0],
        )
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=None,
        )
        assert calls == ["PR_1"]
        assert summary.merged == 1

    def test_dry_run_freshness_query_called_once_per_pr(self, monkeypatch):
        """dry-run issues no mutation, so no apply-time re-read: the
        base-freshness query runs exactly once (snapshot time only)."""
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        inner = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))
        calls: list[str] = []

        def spy(query, variables):
            if query is _BASE_FRESHNESS_QUERY:
                calls.append(query)
            return inner(query, variables)

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=spy,
            merge_gql=lambda q, v: {"mergePullRequest": {"pullRequest": {"merged": True}}},
            identity_gql=_identity_gql_ok,
            audit_path=None,
            dry_run=True,
        )
        assert len(calls) == 1
        assert summary.would_merge == 1


class TestApplyTimeBaseRetarget:
    """P2 round 14: a PR can be retargeted (base branch changed) after the
    snapshot/apply-time freshness reads but before mergePullRequest.
    expectedHeadOid pins only the head, and the freshness compare in
    should_merge / the apply-time re-read both ran against the snapshot's
    base_ref, so a retargeted release/maintenance PR could otherwise merge
    outside ALLOWED_BASE_REFS. The live path re-reads baseRefName FIRST,
    before the base_behind re-read."""

    def test_retarget_at_apply_skips_with_no_mutation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql(
            [_green_pr(1)],
            thread_pages={1: []},
            comment_pages=_readiness_pages(1),
            current_base_ref="release/1.x",
        )
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=audit,
        )
        assert calls == []  # merge_pr never called — no mutation issued
        (d,) = summary.decisions
        assert (d.action, d.reason) == ("skipped", "base_retargeted_at_apply")
        lines = [_json.loads(line) for line in audit.read_text().strip().splitlines()]
        assert all(line["action"] != "merge_intent" for line in lines)

    def test_apply_time_base_read_fault_skips_unreadable(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        read = _fake_gql(
            [_green_pr(1)],
            thread_pages={1: []},
            comment_pages=_readiness_pages(1),
            current_base_ref=None,
        )
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=None,
        )
        assert calls == []
        (d,) = summary.decisions
        assert (d.action, d.reason) == ("skipped", "base_freshness_unreadable")

    def test_unchanged_base_merge_proceeds(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        read = _fake_gql(
            [_green_pr(1)],
            thread_pages={1: []},
            comment_pages=_readiness_pages(1),
            current_base_ref="main",
        )
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=None,
        )
        assert calls == ["PR_1"]
        assert summary.merged == 1

    def test_dry_run_never_calls_pr_base_query(self, monkeypatch):
        """dry-run issues no mutation, so no apply-time re-read at all: the
        current-base-ref query must never be called."""
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        inner = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))
        calls: list[str] = []

        def spy(query, variables):
            if query is _PR_BASE_QUERY:
                calls.append(query)
            return inner(query, variables)

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=spy,
            merge_gql=lambda q, v: {"mergePullRequest": {"pullRequest": {"merged": True}}},
            identity_gql=_identity_gql_ok,
            audit_path=None,
            dry_run=True,
        )
        assert calls == []
        assert summary.would_merge == 1


class TestAuditIntent:
    """Finding B: write an intent audit line before mutating; if it can't be
    written, fail closed and never merge."""

    def test_unwritable_audit_blocks_merge(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        def _boom(*_a, **_k):
            raise OSError("disk full")

        import voyager.core.merge_loop as ml_mod

        monkeypatch.setattr(ml_mod, "_append_merge_audit", _boom)

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=audit,
        )
        assert calls == []  # merge_pr was never called — no mutation issued
        (d,) = summary.decisions
        assert (d.action, d.reason) == ("skipped", "audit_unwritable")

    def test_short_os_write_still_writes_complete_line(self, monkeypatch, tmp_path):
        """Round 13: os.write can return fewer bytes than requested (e.g. a
        near-full filesystem). _append_merge_audit must loop until every byte
        lands, not treat a short count as a completed write."""
        import voyager.core.merge_loop as ml_mod

        real_write = os.write
        write_calls = 0

        def short_write(fd, data):
            nonlocal write_calls
            write_calls += 1
            n = min(len(data), 4)  # never write more than 4 bytes at a time
            real_write(fd, data[:n])
            return n

        monkeypatch.setattr(ml_mod.os, "write", short_write)

        path = tmp_path / "audit.jsonl"
        record = {"ts": "2026-08-08T00:00:00Z", "action": "merge_intent", "repo": "x/y"}
        ml_mod._append_merge_audit(path, record)

        assert write_calls > 1  # proves the short-write path was exercised
        line = path.read_text()
        assert _json.loads(line.strip()) == record

    def test_short_write_then_oserror_blocks_merge(self, monkeypatch, tmp_path):
        """A short write followed by an OSError (e.g. ENOSPC mid-buffer) must
        still fail closed: no partial/non-JSON audit line treated as success,
        and no merge mutation fires."""
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        audit = tmp_path / "audit.jsonl"
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        import voyager.core.merge_loop as ml_mod

        write_calls = 0

        def flaky_write(fd, data):
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                return min(len(data), 3)  # short write, no error yet
            raise OSError("disk full mid-write")

        monkeypatch.setattr(ml_mod.os, "write", flaky_write)

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=audit,
        )
        assert calls == []  # merge_pr was never called — no mutation issued
        (d,) = summary.decisions
        assert (d.action, d.reason) == ("skipped", "audit_unwritable")

    def test_no_audit_path_skips_intent_write_and_still_merges(self, monkeypatch):
        # audit_path=None (as in most tests here) is the "no audit contract to
        # honor" case: intent write is skipped and merge proceeds normally.
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        summary = run_merge_loop(
            ["frankyxhl/fx_bin"],
            read_gql=read,
            merge_gql=merge_gql,
            identity_gql=_identity_gql_ok,
            audit_path=None,
        )
        assert calls == ["PR_1"]
        assert summary.merged == 1


class TestIdentityGate:
    """Finding C: mirror run_resolve_loop's hard machine-identity gate."""

    def test_wrong_identity_aborts_before_any_merge(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))
        calls: list[str] = []

        def merge_gql(query, variables):
            calls.append(variables["prId"])
            return {"mergePullRequest": {"pullRequest": {"merged": True}}}

        def wrong_identity_gql(query, variables):
            return {"viewer": {"login": "ryosaeba1985"}}  # not the machine account

        with pytest.raises(ResolveConversationError):
            run_merge_loop(
                ["frankyxhl/fx_bin"],
                read_gql=read,
                merge_gql=merge_gql,
                identity_gql=wrong_identity_gql,
                audit_path=None,
            )
        assert calls == []  # zero merge mutations issued

    def test_dry_run_still_asserts_identity(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        read = _fake_gql([_green_pr(1)], thread_pages={1: []}, comment_pages=_readiness_pages(1))

        def wrong_identity_gql(query, variables):
            return {"viewer": {"login": "someone-else"}}

        with pytest.raises(ResolveConversationError):
            run_merge_loop(
                ["frankyxhl/fx_bin"],
                read_gql=read,
                merge_gql=lambda query, variables: {},
                identity_gql=wrong_identity_gql,
                dry_run=True,
                audit_path=None,
            )


class TestCli:
    def test_merge_loop_command_registered(self):
        from voyager.cli import app

        # Force a wide, colorless terminal so Typer/Rich does not wrap flag
        # names across lines (CI defaults to ~80 cols, splitting e.g.
        # `--dry-run` into `--dry\n-run`); see tests/unit/test_cli.py.
        runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})
        result = runner.invoke(app, ["countdown", "merge-loop", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--max-merges" in result.output
