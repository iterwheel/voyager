"""Tests for voyager.core.resolve_conversation public contract.

Written against the spec only — implementation not read.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voyager.core.resolve_conversation import (
    _NODE_QUERY,
    _RESOLVE_MUTATION,
    MACHINE_ACCOUNT,
    RESOLVE_ALLOWED_REPOS,
    ResolveConversationError,
    ResolveSummary,
    ThreadState,
    _should_resolve,
    make_github_gql,
    read_machine_token,
    resolve_conversations,
)

# ---------------------------------------------------------------------------
# Shared helpers / fakes
# ---------------------------------------------------------------------------


def _thread_node(
    *,
    id: str = "PRRT_1",
    is_resolved: bool | None = False,
    can_resolve: bool | None = True,
    can_reply: bool | None = True,
    is_outdated: bool | None = False,
) -> dict:
    """Build a raw GraphQL review-thread node dict."""
    return {
        "id": id,
        "isResolved": is_resolved,
        "viewerCanResolve": can_resolve,
        "viewerCanReply": can_reply,
        "isOutdated": is_outdated,
    }


def _pr_response(
    nodes: list[dict],
    *,
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict:
    """Build a paged PR review-threads GraphQL response."""
    return {
        "repository": {
            "pullRequest": {
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            }
        }
    }


def _node_response(node: dict) -> dict:
    """Build a single-node GraphQL response (for thread_id mode)."""
    return {"node": node}


_RESOLVE_MUTATION_OK: dict = {"resolveReviewThread": {"thread": {"isResolved": True}}}


class _SmartGql:
    """
    Query-aware gql fake:

    - Routes anything containing 'mutation' to mutation_response (and enforces
      that it must be 'resolveReviewThread' — raises AssertionError otherwise).
    - Routes regular queries to the provided pages list in order.

    This decouples tests from the implementation's interleaving of page-fetches
    and mutation calls.
    """

    def __init__(
        self,
        pages: list[dict],
        mutation_response: dict | None = None,
        viewer_login: str = "iterwheel-countdown-user",
    ) -> None:
        self._pages = list(pages)
        self._page_idx = 0
        self._mutation_response = (
            mutation_response if mutation_response is not None else _RESOLVE_MUTATION_OK
        )
        self._viewer_login = viewer_login
        self.all_calls: list[tuple[str, dict]] = []
        self.mutation_calls: list[tuple[str, dict]] = []
        self.query_calls: list[tuple[str, dict]] = []

    def __call__(self, query: str, variables: dict) -> dict:
        self.all_calls.append((query, variables))
        if "query viewer" in query.lower():
            # Identity gate: default to the machine account so existing tests pass.
            # (Matches the dedicated viewer op, not the viewerCanResolve field.)
            return {"viewer": {"login": self._viewer_login}}
        if "mutation" in query.lower():
            self.mutation_calls.append((query, variables))
            if "resolveReviewThread" not in query:
                raise AssertionError(
                    f"Forbidden mutation detected (not resolveReviewThread):\n{query[:400]}"
                )
            return self._mutation_response
        # Regular query — serve next page.
        self.query_calls.append((query, variables))
        if self._page_idx >= len(self._pages):
            raise AssertionError(
                f"Unexpected query #{self._page_idx + 1}; only {len(self._pages)} page(s) queued."
            )
        resp = self._pages[self._page_idx]
        self._page_idx += 1
        return resp

    @property
    def mutation_count(self) -> int:
        return len(self.mutation_calls)

    def assert_no_mutations(self) -> None:
        if self.mutation_calls:
            raise AssertionError(
                f"Expected no mutations; got {len(self.mutation_calls)}:\n"
                + "\n".join(q[:200] for q, _ in self.mutation_calls)
            )


# ---------------------------------------------------------------------------
# TestShouldResolve
# ---------------------------------------------------------------------------


class TestShouldResolve:
    def _ts(
        self,
        is_resolved: bool | None,
        viewer_can_resolve: bool | None,
        viewer_can_reply: bool | None,
        is_outdated: bool | None,
    ) -> ThreadState:
        return ThreadState(
            thread_id="t1",
            is_resolved=is_resolved,
            viewer_can_resolve=viewer_can_resolve,
            viewer_can_reply=viewer_can_reply,
            is_outdated=is_outdated,
        )

    def test_all_conditions_met_returns_true(self) -> None:
        assert _should_resolve(self._ts(False, True, True, False)) is True

    def test_already_resolved_returns_false(self) -> None:
        assert _should_resolve(self._ts(True, True, True, False)) is False

    def test_is_resolved_none_fail_closed(self) -> None:
        assert _should_resolve(self._ts(None, True, True, False)) is False

    def test_viewer_cannot_resolve_returns_false(self) -> None:
        assert _should_resolve(self._ts(False, False, True, False)) is False

    def test_viewer_can_resolve_none_fail_closed(self) -> None:
        assert _should_resolve(self._ts(False, None, True, False)) is False

    def test_viewer_cannot_reply_returns_false(self) -> None:
        assert _should_resolve(self._ts(False, True, False, False)) is False

    def test_viewer_can_reply_none_fail_closed(self) -> None:
        assert _should_resolve(self._ts(False, True, None, False)) is False

    def test_is_outdated_true_still_resolves(self) -> None:
        # Outdated is NOT a gate: viewerCanResolve authorizes; outdated only means the
        # anchored line moved (often because the code was fixed).
        assert _should_resolve(self._ts(False, True, True, True)) is True

    def test_is_outdated_none_ignored(self) -> None:
        # is_outdated is no longer consulted, so None for it does not fail closed.
        assert _should_resolve(self._ts(False, True, True, None)) is True

    def test_all_none_returns_false(self) -> None:
        assert _should_resolve(self._ts(None, None, None, None)) is False

    def test_only_is_resolved_false_is_insufficient(self) -> None:
        # The remaining gates (can_resolve / can_reply) are "bad" — must still be False.
        assert _should_resolve(self._ts(False, False, False, True)) is False


# ---------------------------------------------------------------------------
# TestAllowlist
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_voyager_in_allowlist(self) -> None:
        assert "iterwheel/voyager" in RESOLVE_ALLOWED_REPOS

    def test_voyager_sandbox_in_allowlist(self) -> None:
        assert "iterwheel/voyager-sandbox" in RESOLVE_ALLOWED_REPOS

    def test_allowlist_is_frozenset(self) -> None:
        assert isinstance(RESOLVE_ALLOWED_REPOS, frozenset)

    def test_allowlist_has_exactly_two_entries(self) -> None:
        assert len(RESOLVE_ALLOWED_REPOS) == 2

    def test_unknown_repo_raises_resolve_error(self) -> None:
        with pytest.raises(ResolveConversationError, match="allowlist"):
            resolve_conversations(
                repo="other/repo",
                pr=1,
                gql=lambda q, v: {},
            )

    def test_unknown_repo_gql_never_called_before_raise(self) -> None:
        """Allowlist check must fire before any gql call."""
        gql_called: list[bool] = []

        def spy_gql(query: str, variables: dict) -> dict:
            gql_called.append(True)
            return {}

        with pytest.raises(ResolveConversationError):
            resolve_conversations(repo="evil/hacker", pr=99, gql=spy_gql)

        assert not gql_called

    def test_production_repo_passes_allowlist(self) -> None:
        gql = _SmartGql([_pr_response([])])
        result = resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        assert isinstance(result, ResolveSummary)

    def test_sandbox_repo_passes_allowlist(self) -> None:
        gql = _SmartGql([_pr_response([])])
        result = resolve_conversations(repo="iterwheel/voyager-sandbox", pr=1, gql=gql)
        assert isinstance(result, ResolveSummary)


# ---------------------------------------------------------------------------
# TestExactlyOne
# ---------------------------------------------------------------------------


class TestExactlyOne:
    def test_both_pr_and_thread_id_raises(self) -> None:
        with pytest.raises(ResolveConversationError, match="exactly one"):
            resolve_conversations(
                repo="iterwheel/voyager",
                pr=1,
                thread_id="PRRT_X",
                gql=lambda q, v: {},
            )

    def test_neither_pr_nor_thread_id_raises(self) -> None:
        with pytest.raises(ResolveConversationError, match="exactly one"):
            resolve_conversations(
                repo="iterwheel/voyager",
                gql=lambda q, v: {},
            )

    def test_only_pr_does_not_raise_exactly_one(self) -> None:
        gql = _SmartGql([_pr_response([])])
        result = resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        assert isinstance(result, ResolveSummary)

    def test_only_thread_id_does_not_raise_exactly_one(self) -> None:
        node = _thread_node(id="PRRT_S", is_resolved=True)
        gql = _SmartGql([_node_response(node)])
        result = resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_S", gql=gql)
        assert isinstance(result, ResolveSummary)


# ---------------------------------------------------------------------------
# TestReadMachineToken
# ---------------------------------------------------------------------------


class TestReadMachineToken:
    def _completed_process(self, returncode: int = 0, stdout: str = "ghp_token123\n") -> MagicMock:
        cp = MagicMock()
        cp.returncode = returncode
        cp.stdout = stdout
        cp.stderr = ""
        return cp

    def _make_run(self, returncode: int = 0, stdout: str = "ghp_token123\n") -> MagicMock:
        return MagicMock(return_value=self._completed_process(returncode=returncode, stdout=stdout))

    def test_machine_account_constant_value(self) -> None:
        assert MACHINE_ACCOUNT == "iterwheel-countdown-user"

    def test_success_returns_stripped_token(self) -> None:
        run = self._make_run(returncode=0, stdout="ghp_abc123\n")
        assert read_machine_token(run=run) == "ghp_abc123"

    def test_success_calls_correct_gh_command(self) -> None:
        run = self._make_run(returncode=0, stdout="ghp_abc123\n")
        read_machine_token(run=run)
        run.assert_called_once()
        args, kwargs = run.call_args
        assert args[0] == [
            "gh",
            "auth",
            "token",
            "--hostname",
            "github.com",
            "--user",
            "iterwheel-countdown-user",
        ]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 30

    def test_ambient_github_tokens_are_scrubbed_from_subprocess_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "ambient-gh")
        monkeypatch.setenv("GITHUB_TOKEN", "ambient-github")
        monkeypatch.setenv("PATH", "/usr/bin")  # an unrelated var must survive
        run = self._make_run(returncode=0, stdout="ghp_abc123\n")
        read_machine_token(run=run)
        env = run.call_args.kwargs["env"]
        assert "GH_TOKEN" not in env
        assert "GITHUB_TOKEN" not in env
        assert env.get("PATH") == "/usr/bin"

    def test_nonzero_returncode_raises_resolve_error(self) -> None:
        run = self._make_run(returncode=1, stdout="")
        with pytest.raises(ResolveConversationError):
            read_machine_token(run=run)

    def test_whitespace_only_stdout_raises_resolve_error(self) -> None:
        run = self._make_run(returncode=0, stdout="   \n  ")
        with pytest.raises(ResolveConversationError):
            read_machine_token(run=run)

    def test_empty_stdout_raises_resolve_error(self) -> None:
        run = self._make_run(returncode=0, stdout="")
        with pytest.raises(ResolveConversationError):
            read_machine_token(run=run)

    def test_token_not_leaked_in_nonzero_returncode_exception(self) -> None:
        """Token value in stdout must never appear in the exception message."""
        secret = "ghp_super_secret_should_never_leak_9999"
        cp = self._completed_process(returncode=1, stdout=secret)
        run = MagicMock(return_value=cp)
        with pytest.raises(ResolveConversationError) as exc_info:
            read_machine_token(run=run)
        assert secret not in str(exc_info.value)

    def test_resolve_error_is_runtime_error_subclass(self) -> None:
        assert issubclass(ResolveConversationError, RuntimeError)


# ---------------------------------------------------------------------------
# TestOrchestration  (PR mode, forbidden-mutation guard)
# ---------------------------------------------------------------------------


class TestOrchestration:
    def test_mixed_threads_resolved_and_skipped_counts(self) -> None:
        """Two resolvable + two non-resolvable threads in one PR page."""
        nodes = [
            _thread_node(
                id="PRRT_A", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
            ),
            _thread_node(id="PRRT_B", is_resolved=True),  # already resolved
            _thread_node(id="PRRT_C", is_resolved=False, can_resolve=False),  # can't resolve
            _thread_node(
                id="PRRT_D", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
            ),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        result = resolve_conversations(repo="iterwheel/voyager", pr=10, gql=gql)
        assert result.resolved == 2
        assert result.skipped == 2

    def test_mutation_guard_catches_forbidden_mutation(self) -> None:
        """Meta-test: the _SmartGql guard must raise AssertionError for any forbidden mutation."""
        gql = _SmartGql([])
        bad_query = 'mutation ClosePR { closePullRequest(input: {pullRequestId: "x"}) { clientMutationId } }'
        with pytest.raises(AssertionError, match="Forbidden"):
            gql(bad_query, {})

    def test_no_forbidden_mutation_during_pr_orchestration(self) -> None:
        """Any mutation issued must be resolveReviewThread — guard enforces this end-to-end."""
        nodes = [
            _thread_node(
                id="PRRT_OK", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
            ),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        resolve_conversations(repo="iterwheel/voyager", pr=5, gql=gql)
        # _SmartGql already raises AssertionError on any forbidden mutation, so
        # reaching here is the proof. Double-check any mutation calls explicitly.
        for query, _ in gql.mutation_calls:
            assert "resolveReviewThread" in query

    def test_no_resolvable_threads_all_skipped(self) -> None:
        nodes = [
            _thread_node(id="PRRT_1", is_resolved=True),
            _thread_node(id="PRRT_2", is_resolved=False, can_resolve=False),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        result = resolve_conversations(repo="iterwheel/voyager", pr=7, gql=gql)
        assert result.resolved == 0
        assert result.skipped == 2
        gql.assert_no_mutations()

    def test_summary_fields_populated_correctly(self) -> None:
        gql = _SmartGql([_pr_response([])])
        result = resolve_conversations(repo="iterwheel/voyager", pr=42, gql=gql)
        assert result.repo == "iterwheel/voyager"
        assert result.pr == 42
        assert result.dry_run is False

    def test_empty_pr_returns_zero_counts(self) -> None:
        gql = _SmartGql([_pr_response([])])
        result = resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        assert result.resolved == 0
        assert result.skipped == 0

    def test_pagination_two_pages_both_resolvable(self) -> None:
        """Paginated PR: threads from both pages are resolved."""
        page1 = _pr_response(
            [
                _thread_node(
                    id="PRRT_P1",
                    is_resolved=False,
                    can_resolve=True,
                    can_reply=True,
                    is_outdated=False,
                )
            ],
            has_next_page=True,
            end_cursor="cursor_abc",
        )
        page2 = _pr_response(
            [
                _thread_node(
                    id="PRRT_P2",
                    is_resolved=False,
                    can_resolve=True,
                    can_reply=True,
                    is_outdated=False,
                )
            ],
            has_next_page=False,
        )
        gql = _SmartGql([page1, page2])
        result = resolve_conversations(repo="iterwheel/voyager", pr=3, gql=gql)
        assert result.resolved == 2

    def test_outdated_thread_is_resolved(self) -> None:
        # Outdated threads ARE resolvable (anchored line just moved, often the fix).
        nodes = [
            _thread_node(
                id="PRRT_OD", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=True
            ),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        result = resolve_conversations(repo="iterwheel/voyager", pr=2, gql=gql)
        assert result.resolved == 1
        assert result.skipped == 0


# ---------------------------------------------------------------------------
# TestDryRun
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_issues_no_mutation_at_all(self) -> None:
        nodes = [
            _thread_node(
                id="PRRT_DR1",
                is_resolved=False,
                can_resolve=True,
                can_reply=True,
                is_outdated=False,
            ),
            _thread_node(
                id="PRRT_DR2",
                is_resolved=False,
                can_resolve=True,
                can_reply=True,
                is_outdated=False,
            ),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        resolve_conversations(repo="iterwheel/voyager", pr=1, dry_run=True, gql=gql)
        gql.assert_no_mutations()

    def test_dry_run_counts_would_resolve_toward_resolved(self) -> None:
        nodes = [
            _thread_node(
                id="PRRT_W1", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
            ),
            _thread_node(
                id="PRRT_W2", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
            ),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        result = resolve_conversations(repo="iterwheel/voyager", pr=1, dry_run=True, gql=gql)
        assert result.resolved == 2

    def test_dry_run_details_contain_would_resolve_action(self) -> None:
        nodes = [
            _thread_node(
                id="PRRT_WR", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
            ),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        result = resolve_conversations(repo="iterwheel/voyager", pr=1, dry_run=True, gql=gql)
        actions = [action for _, action in result.details]
        assert any("would_resolve" in action for action in actions)

    def test_dry_run_flag_reflected_in_summary(self) -> None:
        gql = _SmartGql([_pr_response([])])
        result = resolve_conversations(repo="iterwheel/voyager", pr=1, dry_run=True, gql=gql)
        assert result.dry_run is True

    def test_dry_run_non_resolvable_thread_still_skipped(self) -> None:
        nodes = [
            _thread_node(id="PRRT_NR", is_resolved=True),
        ]
        gql = _SmartGql([_pr_response(nodes)])
        result = resolve_conversations(repo="iterwheel/voyager", pr=1, dry_run=True, gql=gql)
        assert result.skipped == 1
        assert result.resolved == 0


# ---------------------------------------------------------------------------
# TestSingleThreadId
# ---------------------------------------------------------------------------


class TestSingleThreadId:
    def test_resolvable_thread_resolved_count_one(self) -> None:
        node = _thread_node(
            id="PRRT_S1", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
        )
        gql = _SmartGql([_node_response(node)])
        result = resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_S1", gql=gql)
        assert result.resolved == 1
        assert result.skipped == 0

    def test_non_resolvable_thread_skipped_no_mutation(self) -> None:
        node = _thread_node(
            id="PRRT_S2", is_resolved=False, can_resolve=False, can_reply=True, is_outdated=False
        )
        gql = _SmartGql([_node_response(node)])
        result = resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_S2", gql=gql)
        assert result.resolved == 0
        assert result.skipped == 1
        gql.assert_no_mutations()

    def test_resolvable_thread_issues_only_resolve_mutation(self) -> None:
        node = _thread_node(
            id="PRRT_S3", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
        )
        gql = _SmartGql([_node_response(node)])
        resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_S3", gql=gql)
        for query, _ in gql.mutation_calls:
            assert "resolveReviewThread" in query

    def test_already_resolved_thread_skipped(self) -> None:
        node = _thread_node(id="PRRT_S4", is_resolved=True)
        gql = _SmartGql([_node_response(node)])
        result = resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_S4", gql=gql)
        assert result.resolved == 0
        assert result.skipped == 1

    def test_outdated_thread_is_resolved(self) -> None:
        # Outdated single thread is resolvable (anchored line moved, not a skip reason).
        node = _thread_node(
            id="PRRT_S5", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=True
        )
        gql = _SmartGql([_node_response(node)])
        result = resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_S5", gql=gql)
        assert result.resolved == 1
        assert result.skipped == 0

    def test_single_thread_mode_pr_is_none_in_summary(self) -> None:
        node = _thread_node(id="PRRT_S6", is_resolved=True)
        gql = _SmartGql([_node_response(node)])
        result = resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_S6", gql=gql)
        assert result.pr is None

    def test_single_thread_dry_run_no_mutation_resolved_counted(self) -> None:
        node = _thread_node(
            id="PRRT_S7", is_resolved=False, can_resolve=True, can_reply=True, is_outdated=False
        )
        gql = _SmartGql([_node_response(node)])
        result = resolve_conversations(
            repo="iterwheel/voyager", thread_id="PRRT_S7", dry_run=True, gql=gql
        )
        assert result.resolved == 1
        gql.assert_no_mutations()


# ---------------------------------------------------------------------------
# TestToPublicDict
# ---------------------------------------------------------------------------


class TestToPublicDict:
    def test_returns_dict(self) -> None:
        summary = ResolveSummary(
            repo="iterwheel/voyager", pr=None, resolved=0, skipped=0, dry_run=False
        )
        assert isinstance(summary.to_public_dict(), dict)

    def test_production_repo_redacts_thread_ids(self) -> None:
        """Thread IDs must not appear in to_public_dict() output for the production repo."""
        secret_id = "PRRT_secret_production_never_leak_abc123"
        summary = ResolveSummary(
            repo="iterwheel/voyager",
            pr=None,
            resolved=1,
            skipped=0,
            dry_run=False,
            details=((secret_id, "resolved"),),
        )
        d = summary.to_public_dict()
        assert secret_id not in str(d)

    def test_sandbox_repo_shows_raw_thread_ids(self) -> None:
        """Thread IDs must be visible in to_public_dict() for the sandbox repo."""
        thread_id = "PRRT_sandbox_visible_xyz456"
        summary = ResolveSummary(
            repo="iterwheel/voyager-sandbox",
            pr=42,
            resolved=1,
            skipped=0,
            dry_run=False,
            details=((thread_id, "resolved"),),
        )
        d = summary.to_public_dict()
        assert thread_id in str(d)

    def test_pr_none_represented_in_dict(self) -> None:
        summary = ResolveSummary(
            repo="iterwheel/voyager", pr=None, resolved=0, skipped=2, dry_run=False
        )
        d = summary.to_public_dict()
        assert d.get("pr") is None

    def test_sandbox_pr_value_in_dict(self) -> None:
        summary = ResolveSummary(
            repo="iterwheel/voyager-sandbox", pr=99, resolved=3, skipped=1, dry_run=True
        )
        d = summary.to_public_dict()
        assert d.get("pr") == 99

    def test_resolved_and_skipped_counts_in_dict(self) -> None:
        summary = ResolveSummary(
            repo="iterwheel/voyager-sandbox", pr=1, resolved=5, skipped=3, dry_run=False
        )
        d = summary.to_public_dict()
        assert d.get("resolved") == 5
        assert d.get("skipped") == 3

    def test_dry_run_flag_in_dict(self) -> None:
        summary = ResolveSummary(
            repo="iterwheel/voyager", pr=None, resolved=0, skipped=0, dry_run=True
        )
        d = summary.to_public_dict()
        assert d.get("dry_run") is True


# ---------------------------------------------------------------------------
# TestReviewFixes — regressions for issues found in adversarial review
# (allowlist-before-auth, cross-repo bypass, silent PR-not-found, null-cursor
# page drop, duplicate-cursor reprocessing, verify_failed, gql HTTP layer)
# ---------------------------------------------------------------------------


def _node_in_repo(node: dict, repo: str) -> dict:
    """Attach owning-repo info to a thread node (as GitHub returns it)."""
    return {**node, "pullRequest": {"repository": {"nameWithOwner": repo}}}


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict, capture: dict) -> None:
        self._payload = payload
        self._capture = capture

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResp:
        self._capture["url"] = url
        self._capture["headers"] = headers
        self._capture["json"] = json
        return _FakeResp(self._payload)


class TestReviewFixes:
    def test_cross_repo_thread_id_is_blocked(self) -> None:
        node = _node_in_repo(_thread_node(id="PRRT_X"), "evil/other")
        gql = _SmartGql([_node_response(node)])
        with pytest.raises(ResolveConversationError) as ei:
            resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_X", gql=gql)
        assert "belong" in str(ei.value).lower() or "bypass" in str(ei.value).lower()
        gql.assert_no_mutations()

    def test_same_repo_thread_id_resolves(self) -> None:
        node = _node_in_repo(_thread_node(id="PRRT_Y"), "iterwheel/voyager")
        gql = _SmartGql([_node_response(node)])
        summary = resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_Y", gql=gql)
        assert summary.resolved == 1

    def test_pr_not_found_raises(self) -> None:
        gql = _SmartGql([{"repository": {"pullRequest": None}}])
        with pytest.raises(ResolveConversationError) as ei:
            resolve_conversations(repo="iterwheel/voyager", pr=99999, gql=gql)
        msg = str(ei.value)
        assert "not found" in msg.lower()
        # VOY-1828: non-sandbox errors must not leak the raw PR number.
        assert "99999" not in msg

    def test_wrong_viewer_identity_refuses_and_no_mutation(self) -> None:
        # Token belongs to someone other than the machine account → refuse.
        gql = _SmartGql([_pr_response([_thread_node(id="PRRT_1")])], viewer_login="ryosaeba1985")
        with pytest.raises(ResolveConversationError) as ei:
            resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        assert "identity" in str(ei.value).lower()
        gql.assert_no_mutations()

    def test_empty_viewer_login_refuses(self) -> None:
        gql = _SmartGql([_pr_response([_thread_node(id="PRRT_1")])], viewer_login="")
        with pytest.raises(ResolveConversationError):
            resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        gql.assert_no_mutations()

    def test_identity_checked_even_in_dry_run(self) -> None:
        gql = _SmartGql([_pr_response([_thread_node(id="PRRT_1")])], viewer_login="someoneelse")
        with pytest.raises(ResolveConversationError):
            resolve_conversations(repo="iterwheel/voyager", pr=1, dry_run=True, gql=gql)

    def test_single_thread_missing_node_raises(self) -> None:
        # GitHub returns node: null for a mistyped / inaccessible / non-review node.
        gql = _SmartGql([{"node": None}])
        with pytest.raises(ResolveConversationError) as ei:
            resolve_conversations(repo="iterwheel/voyager", thread_id="PRRT_GONE", gql=gql)
        msg = str(ei.value)
        assert "not found" in msg.lower() or "not accessible" in msg.lower()
        # VOY-1828: must not echo the thread node id.
        assert "PRRT_GONE" not in msg
        gql.assert_no_mutations()

    def test_single_thread_wrong_type_node_raises(self) -> None:
        # A non-PullRequestReviewThread node returns an empty fragment ({}).
        gql = _SmartGql([{"node": {}}])
        with pytest.raises(ResolveConversationError):
            resolve_conversations(repo="iterwheel/voyager", thread_id="ISSUE_1", gql=gql)
        gql.assert_no_mutations()

    def test_has_next_page_with_null_cursor_raises(self) -> None:
        page = _pr_response([_thread_node(id="PRRT_1")], has_next_page=True, end_cursor=None)
        gql = _SmartGql([page])
        with pytest.raises(ResolveConversationError) as ei:
            resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        assert "pagination" in str(ei.value).lower()

    def test_duplicate_thread_across_pages_resolved_once(self) -> None:
        dup = _thread_node(id="PRRT_DUP", is_resolved=False)
        page1 = _pr_response([dup], has_next_page=True, end_cursor="C1")
        page2 = _pr_response([dup], has_next_page=False, end_cursor=None)
        gql = _SmartGql([page1, page2])
        summary = resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        assert summary.resolved == 1
        assert gql.mutation_count == 1

    def test_verify_failed_is_skipped_not_resolved(self) -> None:
        node = _thread_node(id="PRRT_V", is_resolved=False)
        gql = _SmartGql(
            [_pr_response([node])],
            mutation_response={"resolveReviewThread": {"thread": {"isResolved": False}}},
        )
        summary = resolve_conversations(repo="iterwheel/voyager", pr=1, gql=gql)
        assert summary.resolved == 0
        assert summary.skipped == 1

    def test_make_github_gql_sends_bearer_and_endpoint(self) -> None:
        capture: dict = {}
        gql = make_github_gql(
            "secret-tok",
            client_factory=lambda: _FakeClient({"data": {"node": {}}}, capture),
        )
        out = gql(_NODE_QUERY, {"threadId": "PRRT_1"})
        assert out == {"node": {}}
        assert capture["url"].endswith("/graphql")
        assert capture["headers"]["Authorization"] == "Bearer secret-tok"
        assert "Accept" in capture["headers"]

    def test_make_github_gql_raises_on_graphql_errors(self) -> None:
        capture: dict = {}
        gql = make_github_gql(
            "secret-tok",
            client_factory=lambda: _FakeClient(
                {"errors": [{"message": "a"}, {"message": "b"}]}, capture
            ),
        )
        with pytest.raises(ResolveConversationError) as ei:
            gql(_RESOLVE_MUTATION, {"threadId": "PRRT_1"})
        # Token must never appear in the error.
        assert "secret-tok" not in str(ei.value)

    def test_make_github_gql_refuses_unknown_operation(self) -> None:
        capture: dict = {}
        gql = make_github_gql(
            "secret-tok", client_factory=lambda: _FakeClient({"data": {}}, capture)
        )
        with pytest.raises(ResolveConversationError):
            gql("mutation Evil { mergePullRequest { clientMutationId } }", {})
        # Refused before any network call.
        assert capture == {}
