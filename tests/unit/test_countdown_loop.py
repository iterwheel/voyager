"""Tests for voyager.core.countdown_loop (PRP VOY-1831).

Focus: the safety invariants — the LLM gate is a fail-closed veto on top of the
deterministic candidate set, redaction, blast-radius cap, systemic-failure, and
per-target error tolerance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voyager.core import countdown_loop as cl
from voyager.core.countdown_loop import (
    AlreadyRunningError,
    Candidate,
    Decision,
    GateVerdict,
    gate_repos,
    load_repo_list,
    run_resolve_loop,
    single_instance_lock,
)

SANDBOX = "iterwheel/voyager-sandbox"
REAL = "iterwheel/voyager"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


def _thread(
    *,
    tid: str = "PRRT_1",
    resolved: bool = False,
    outdated: bool = False,
    can_resolve: bool = True,
    can_reply: bool = True,
    comments: list[tuple[str, str]] | None = None,
    truncated: bool = False,
) -> dict:
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": outdated,
        "viewerCanResolve": can_resolve,
        "viewerCanReply": can_reply,
        "comments": {
            "pageInfo": {"hasNextPage": truncated},
            "nodes": [{"author": {"login": a}, "body": b} for a, b in (comments or [])],
        },
    }


class FakeReadGql:
    """Serves open-PR numbers and per-PR review-thread pages from a fixture map."""

    def __init__(
        self,
        pr_numbers: dict[str, list[int]],
        threads: dict[tuple[str, int], list[dict]],
        *,
        head_sha: str = "deadbeef",
        fail_repos: set[str] | None = None,
        pull_missing: set[tuple[str, int]] | None = None,
        comment_counts: dict[str, int | None] | None = None,
        freshness_raise: set[str] | None = None,
    ) -> None:
        self._freshness_raise = freshness_raise or set()
        self._pr_numbers = pr_numbers
        self._threads = threads
        self._head_sha = head_sha
        self._fail_repos = fail_repos or set()
        self._pull_missing = pull_missing or set()
        # tid -> live comment count for the freshness re-read; defaults to the thread's
        # own comment count so unchanged threads resolve. Override to simulate a race.
        self._comment_counts = comment_counts or {}

        self.calls: list[str] = []

    def _default_count(self, tid: str) -> int | None:
        for nodes in self._threads.values():
            for n in nodes:
                if n.get("id") == tid:
                    return len((n.get("comments") or {}).get("nodes") or [])
        return None

    def __call__(self, query: str, variables: dict) -> dict:
        if "ThreadFreshness" in query:
            tid = variables["threadId"]
            if tid in self._freshness_raise:
                raise cl.ResolveConversationError(f"boom freshness {tid}")
            if tid in self._comment_counts:
                total = self._comment_counts[tid]
            else:
                total = self._default_count(tid)
            if total is None:
                return {"node": None}
            return {"node": {"comments": {"totalCount": total}}}
        repo = f"{variables['owner']}/{variables['name']}"
        if "OpenPullRequestNumbers" in query:
            self.calls.append(f"prs:{repo}")
            if repo in self._fail_repos:
                raise cl.ResolveConversationError(f"boom enumerating {repo}")
            nodes = [{"number": n} for n in self._pr_numbers.get(repo, [])]
            return {
                "repository": {"pullRequests": {"pageInfo": {"hasNextPage": False}, "nodes": nodes}}
            }
        # ReviewThreadsWithComments
        pr = variables["number"]
        self.calls.append(f"threads:{repo}#{pr}")
        if (repo, pr) in self._pull_missing:
            return {"repository": {"pullRequest": None}}
        nodes = self._threads.get((repo, pr), [])
        return {
            "repository": {
                "pullRequest": {
                    "headRefOid": self._head_sha,
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": nodes},
                }
            }
        }


class FakeGate:
    def __init__(self, verdict: GateVerdict | None = None, *, raises: bool = False) -> None:
        self._verdict = verdict or GateVerdict(True, "ok")
        self._raises = raises
        self.seen: list[Candidate] = []

    def should_resolve(self, candidate: Candidate) -> GateVerdict:
        self.seen.append(candidate)
        if self._raises:
            raise RuntimeError("gate exploded")
        return self._verdict


def _fake_resolver():
    resolved: list[Candidate] = []

    def _fn(cand: Candidate, _gql) -> Decision:
        resolved.append(cand)
        return Decision(cand.repo, cand.pr, cand.thread_id, "resolved", "ok")

    return _fn, resolved


def test_run_resolve_loop_returns_summary_synchronously() -> None:
    summary = run_resolve_loop(
        requested_repos=[SANDBOX],
        gate=FakeGate(),
        read_gql=FakeReadGql({SANDBOX: []}, {}),
        resolve_gql=object(),
        resolve_fn=_fake_resolver()[0],
        audit_path=None,
    )

    assert summary.repos_scanned == (SANDBOX,)


def test_make_read_gql_uses_sync_client_factory() -> None:
    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"data": {"repository": {"pullRequests": {"pageInfo": {}, "nodes": []}}}}

    class _Client:
        def __init__(self) -> None:
            self.posts = 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            pass

        def post(self, *_args, **_kwargs) -> _Response:
            self.posts += 1
            return _Response()

    client = _Client()
    gql = cl.make_read_gql("token", client_factory=lambda: client)

    assert gql(cl._OPEN_PR_NUMBERS_QUERY, {"owner": "iterwheel", "name": "voyager"}) == {
        "repository": {"pullRequests": {"pageInfo": {}, "nodes": []}}
    }
    assert client.posts == 1


def _run(read_gql, gate, **kw):
    resolve_fn = kw.pop("resolve_fn", None)
    if resolve_fn is None:
        resolve_fn, _ = _fake_resolver()
    return run_resolve_loop(
        requested_repos=kw.pop("repos", [SANDBOX]),
        gate=gate,
        read_gql=read_gql,
        resolve_gql=object(),
        resolve_fn=resolve_fn,
        audit_path=kw.pop("audit_path", None),
        **kw,
    )


# --------------------------------------------------------------------------- #
# Lock / gate / repo-list
# --------------------------------------------------------------------------- #


class TestLock:
    def test_contention_raises_already_running(self, tmp_path: Path) -> None:
        lock = tmp_path / "x.lock"
        with (
            single_instance_lock(lock),
            pytest.raises(AlreadyRunningError),
            single_instance_lock(lock),
        ):
            pass

    def test_reacquire_after_release(self, tmp_path: Path) -> None:
        lock = tmp_path / "x.lock"
        with single_instance_lock(lock):
            pass
        with single_instance_lock(lock):  # no raise
            pass


class TestGateRepos:
    def test_splits_by_ceiling(self) -> None:
        allowed, skipped = gate_repos([SANDBOX, "evil/x"], ceiling=frozenset({SANDBOX}))
        assert allowed == [SANDBOX]
        assert skipped == ["evil/x"]

    def test_dedup_preserves_order(self) -> None:
        allowed, _ = gate_repos([SANDBOX, SANDBOX], ceiling=frozenset({SANDBOX}))
        assert allowed == [SANDBOX]

    def test_default_ceiling_is_resolver_allowlist(self) -> None:
        allowed, _ = gate_repos([REAL, SANDBOX])
        assert set(allowed) == {REAL, SANDBOX}


class TestRepoList:
    def test_parses_and_lowercases(self, tmp_path: Path) -> None:
        f = tmp_path / "repos.txt"
        f.write_text("# header\nIterwheel/Voyager\n\n  owner/name  # trailing\n", encoding="utf-8")
        assert load_repo_list(f) == ["iterwheel/voyager", "owner/name"]

    def test_malformed_line_raises_with_lineno(self, tmp_path: Path) -> None:
        f = tmp_path / "repos.txt"
        f.write_text("ok/repo\nnotvalid\n", encoding="utf-8")
        with pytest.raises(ValueError, match=r":2:"):
            load_repo_list(f)


# --------------------------------------------------------------------------- #
# Deterministic prefilter
# --------------------------------------------------------------------------- #


class TestListOpenPrNumbers:
    def test_null_repository_raises(self) -> None:
        # null repository (token lost access / repo renamed) must error, not look healthy.
        def gql(query: str, variables: dict) -> dict:
            return {"repository": None}

        with pytest.raises(cl.ResolveConversationError, match="repository not found"):
            cl._list_open_pr_numbers(gql, REAL)

    def test_dedupes_pr_numbers_across_pages(self) -> None:
        # Overlapping/repeated pages must not double-enumerate the same PR.
        pages = [
            {
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                "nodes": [{"number": 1}, {"number": 2}],
            },
            {"pageInfo": {"hasNextPage": False}, "nodes": [{"number": 2}, {"number": 3}]},
        ]
        calls = {"n": 0}

        def gql(query: str, variables: dict) -> dict:
            page = pages[calls["n"]]
            calls["n"] += 1
            return {"repository": {"pullRequests": page}}

        assert cl._list_open_pr_numbers(gql, REAL) == [1, 2, 3]

    def test_candidates_dedupe_threads_across_pages(self) -> None:
        # Overlapping/repeated reviewThreads pages must not double-gate a thread.
        pages = [
            {
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                "nodes": [_thread(tid="T1"), _thread(tid="T2")],
            },
            {"pageInfo": {"hasNextPage": False}, "nodes": [_thread(tid="T2"), _thread(tid="T3")]},
        ]
        calls = {"n": 0}

        def gql(query: str, variables: dict) -> dict:
            page = pages[calls["n"]]
            calls["n"] += 1
            return {"repository": {"pullRequest": {"reviewThreads": page}}}

        cands = cl._candidates_for_pr(gql, REAL, 1)
        assert [c.thread_id for c in cands] == ["T1", "T2", "T3"]


class TestPrefilter:
    def test_only_resolvable_threads_become_candidates(self) -> None:
        threads = [
            _thread(tid="A"),  # resolvable
            _thread(tid="B", resolved=True),  # already resolved
            _thread(tid="C", outdated=True),  # outdated — STILL a candidate (not a gate)
            _thread(tid="D", can_resolve=False),  # cannot resolve
            _thread(tid="E", can_reply=False),  # cannot reply
        ]
        read = FakeReadGql({SANDBOX: [7]}, {(SANDBOX, 7): threads})
        gate = FakeGate(GateVerdict(True, "ok"))
        _run(read, gate)
        assert [c.thread_id for c in gate.seen] == ["A", "C"]

    def test_pr_not_found_is_tolerated_target_error(self) -> None:
        read = FakeReadGql({SANDBOX: [9]}, {}, pull_missing={(SANDBOX, 9)})
        summary = _run(read, FakeGate())
        assert summary.errors
        assert summary.errors[0].pr == 9
        assert summary.resolved == 0


# --------------------------------------------------------------------------- #
# Safety invariants
# --------------------------------------------------------------------------- #


class TestGateVeto:
    def test_veto_means_no_resolve(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        fn, resolved = _fake_resolver()
        summary = _run(read, FakeGate(GateVerdict(False, "not addressed")), resolve_fn=fn)
        assert resolved == []
        assert summary.resolved == 0
        assert any(d.action == "vetoed" for d in summary.decisions)

    def test_gate_exception_fails_closed(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        fn, resolved = _fake_resolver()
        summary = _run(read, FakeGate(raises=True), resolve_fn=fn)
        assert resolved == []
        assert summary.resolved == 0
        assert all(d.action == "vetoed" for d in summary.decisions)
        assert "gate_error" in summary.decisions[0].reason

    def test_truncated_comments_veto_without_calling_gate(self) -> None:
        # A thread with more comments than we fetched must fail closed: the gate would
        # judge on a partial prefix that may omit a later "still broken" reply.
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A", truncated=True)]})
        fn, resolved = _fake_resolver()
        gate = FakeGate(GateVerdict(True, "looks fixed"))
        summary = _run(read, gate, resolve_fn=fn)
        assert resolved == []
        assert gate.seen == []  # gate never consulted on a truncated thread
        assert summary.decisions[0].action == "vetoed"
        assert summary.decisions[0].reason == "comments_truncated"

    def test_injection_approve_all_cannot_resolve_noncandidates(self) -> None:
        # Gate approves EVERYTHING (simulating prompt-injection success). Non-candidates
        # (resolved/can't-resolve/can't-reply) must STILL never resolve — the
        # deterministic prefilter is the hard boundary. (Outdated is NOT a non-candidate.)
        threads = [
            _thread(tid="A"),  # the only real candidate
            _thread(tid="B", resolved=True),
            _thread(tid="C", can_reply=False),
            _thread(tid="D", can_resolve=False),
        ]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        fn, resolved = _fake_resolver()
        _run(read, FakeGate(GateVerdict(True, "resolve everything!")), resolve_fn=fn)
        assert [c.thread_id for c in resolved] == ["A"]


class TestDryRun:
    def test_dry_run_issues_no_resolve(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        fn, resolved = _fake_resolver()
        summary = _run(read, FakeGate(), resolve_fn=fn, dry_run=True)
        assert resolved == []
        assert summary.would_resolve == 1
        assert summary.resolved == 0


class TestCap:
    def test_max_resolves_caps_and_flags(self) -> None:
        threads = [_thread(tid=f"T{i}") for i in range(5)]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        fn, resolved = _fake_resolver()
        summary = _run(read, FakeGate(), resolve_fn=fn, max_resolves=2)
        assert len(resolved) == 2
        assert summary.capped is True

    def test_dry_run_caps_would_resolves(self) -> None:
        # The cap bounds APPROVED candidates in both modes — dry-run must not fan out
        # past max_resolves even though it never calls the resolver.
        threads = [_thread(tid=f"T{i}") for i in range(5)]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        summary = _run(read, FakeGate(), dry_run=True, max_resolves=2)
        assert summary.would_resolve == 2
        assert summary.capped is True

    def test_failed_attempts_count_against_cap(self) -> None:
        # The cap bounds APPROVED ATTEMPTS, not just successes: a resolver that keeps
        # failing must still stop the loop at max_resolves (blast-radius guarantee).
        threads = [_thread(tid=f"T{i}") for i in range(5)]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        attempts: list[Candidate] = []

        def _always_fail(cand: Candidate, _gql) -> Decision:
            attempts.append(cand)
            return Decision(cand.repo, cand.pr, cand.thread_id, "resolve_failed", "boom")

        summary = _run(read, FakeGate(), resolve_fn=_always_fail, max_resolves=2)
        assert len(attempts) == 2
        assert summary.capped is True
        assert summary.resolved == 0


class TestCommentFreshness:
    def test_new_comment_between_gate_and_resolve_skips(self) -> None:
        # Live count (2) differs from what the gate saw (1) → fail closed, no resolve.
        threads = [_thread(tid="A", comments=[("r", "please fix")])]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads}, comment_counts={"A": 2})
        fn, resolved = _fake_resolver()
        summary = _run(read, FakeGate(GateVerdict(True, "ok")), resolve_fn=fn)
        assert resolved == []
        assert summary.decisions[0].action == "skipped_stale"
        assert summary.decisions[0].reason == "comments_changed"

    def test_unreadable_count_skips(self) -> None:
        threads = [_thread(tid="A", comments=[("r", "please fix")])]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads}, comment_counts={"A": None})
        fn, resolved = _fake_resolver()
        summary = _run(read, FakeGate(GateVerdict(True, "ok")), resolve_fn=fn)
        assert resolved == []
        assert summary.decisions[0].action == "skipped_stale"

    def test_unchanged_count_resolves(self) -> None:
        threads = [_thread(tid="A", comments=[("r", "please fix")])]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})  # default count matches
        fn, resolved = _fake_resolver()
        _run(read, FakeGate(GateVerdict(True, "ok")), resolve_fn=fn)
        assert [c.thread_id for c in resolved] == ["A"]

    def test_freshness_error_skips_candidate_and_keeps_scanning(self) -> None:
        # A transient freshness read error must skip THAT candidate, not abort the run.
        read = FakeReadGql(
            {SANDBOX: [1, 2]},
            {
                (SANDBOX, 1): [_thread(tid="A", comments=[("r", "x")])],
                (SANDBOX, 2): [_thread(tid="B", comments=[("r", "y")])],
            },
            freshness_raise={"A"},
        )
        fn, resolved = _fake_resolver()
        summary = _run(read, FakeGate(GateVerdict(True, "ok")), resolve_fn=fn)
        assert [c.thread_id for c in resolved] == ["B"]  # PR 2 still scanned + resolved
        a = next(d for d in summary.decisions if d.thread_id == "A")
        assert a.action == "skipped_stale"


class TestThreadCommentCount:
    def test_returns_none_on_read_error(self) -> None:
        def gql(query: str, variables: dict) -> dict:
            raise cl.ResolveConversationError("transient")

        assert cl._thread_comment_count(gql, "T1") is None


class TestRedaction:
    def test_real_repo_redacts_identifiers(self) -> None:
        read = FakeReadGql({REAL: [1]}, {(REAL, 1): [_thread(tid="SECRET")]})
        summary = _run(read, FakeGate(), repos=[REAL])
        pub = summary.to_public_dict()
        blob = str(pub)
        assert "SECRET" not in blob
        assert all("pr" not in d for d in pub["decisions"])
        assert all(d.get("redacted") for d in pub["decisions"])

    def test_sandbox_shows_identifiers(self) -> None:
        read = FakeReadGql({SANDBOX: [42]}, {(SANDBOX, 42): [_thread(tid="OK")]})
        summary = _run(read, FakeGate(), repos=[SANDBOX])
        pub = summary.to_public_dict()
        assert pub["decisions"][0]["pr"] == 42
        assert pub["decisions"][0]["thread_id"] == "OK"

    def test_llm_reason_not_leaked_for_real_repo(self, tmp_path: Path) -> None:
        # The gate's reason is LLM free-text over comment bodies; for non-sandbox repos
        # it must never reach public output OR the audit (it could echo a raw PR#/node-ID).
        leak = "PRRT_kwDOSECRETnodeid #1337"
        audit = tmp_path / "audit.jsonl"
        read = FakeReadGql({REAL: [1]}, {(REAL, 1): [_thread(tid="A")]})
        _run(read, FakeGate(GateVerdict(False, leak)), repos=[REAL], audit_path=audit)
        # would have been recorded as a veto carrying `leak` as its reason
        assert leak not in audit.read_text(encoding="utf-8")


class TestIdentityGate:
    def test_dry_run_still_asserts_identity(self) -> None:
        # dry-run enumerates threads and ships comments to the LLM, so a wrong machine
        # identity must abort up front there too — not only on live resolves.
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})

        def _wrong_identity_gql(query: str, variables: dict) -> dict:
            return {"viewer": {"login": "ryosaeba1985"}}  # not the machine account

        with pytest.raises(cl.ResolveConversationError):
            run_resolve_loop(
                requested_repos=[SANDBOX],
                gate=FakeGate(),
                read_gql=read,
                resolve_gql=_wrong_identity_gql,
                resolve_fn=None,  # real path → identity asserted before any LLM call
                dry_run=True,
                audit_path=None,
            )


class TestAuditFailClosed:
    def test_unwritable_audit_aborts_before_mutation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Write-ahead intent: if the audit sink can't be written, the run must abort
        # BEFORE resolving — no unattended mutation without a trail.
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        attempts: list[Candidate] = []

        def _track(cand: Candidate, _gql) -> Decision:
            attempts.append(cand)
            return Decision(cand.repo, cand.pr, cand.thread_id, "resolved", "ok")

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(cl, "_append_audit", _boom)
        with pytest.raises(OSError, match="disk full"):
            run_resolve_loop(
                requested_repos=[SANDBOX],
                gate=FakeGate(),
                read_gql=read,
                resolve_gql=object(),
                resolve_fn=_track,
                audit_path=tmp_path / "audit.jsonl",
            )
        assert attempts == []  # never mutated


class TestSystemicFailure:
    def test_all_repos_fail_enumeration_is_systemic(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {}, fail_repos={SANDBOX})
        summary = _run(read, FakeGate())
        assert summary.systemic_failure is True

    def test_one_repo_fails_others_continue(self) -> None:
        read = FakeReadGql(
            {REAL: [1], SANDBOX: [2]},
            {(SANDBOX, 2): [_thread(tid="A")]},
            fail_repos={REAL},
        )
        summary = _run(read, FakeGate(), repos=[REAL, SANDBOX])
        assert summary.systemic_failure is False
        assert summary.resolved == 1
        assert any(e.repo == REAL for e in summary.errors)


class TestAudit:
    def test_audit_records_redacted_decisions(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        read = FakeReadGql({REAL: [1]}, {(REAL, 1): [_thread(tid="SECRET")]})
        _run(read, FakeGate(), repos=[REAL], audit_path=audit)
        text = audit.read_text(encoding="utf-8")
        assert text.strip()  # at least one line
        assert "SECRET" not in text  # redacted in audit too
        assert "redacted" in text


# --------------------------------------------------------------------------- #
# Candidate guard-list transitions
# --------------------------------------------------------------------------- #


def _guard_context(
    read_gql,
    gate,
    *,
    candidate: Candidate | None = None,
    do_resolve=None,
    dry_run: bool = False,
    max_resolves: int = 20,
    approved_count: int = 0,
    audit_path: Path | None = None,
):
    return cl._CandidateFlow(
        repo=SANDBOX,
        pr=1,
        candidate=candidate or Candidate(SANDBOX, 1, "A", (("r", "please fix"),)),
        gate=gate,
        read_gql=read_gql,
        resolve_gql=object(),
        do_resolve=do_resolve or _fake_resolver()[0],
        dry_run=dry_run,
        max_resolves=max_resolves,
        approved_count=approved_count,
        timestamp="2026-06-28T00:00:00+00:00",
        audit_path=audit_path,
    )


class TestCandidateGuardList:
    def test_wire_actions_preserved_without_fsm_indirection(self) -> None:
        assert {
            cl.ACTION_VETOED,
            cl.ACTION_WOULD_RESOLVE,
            cl.ACTION_SKIPPED_STALE,
            cl.ACTION_RESOLVED,
            cl.ACTION_RESOLVE_FAILED,
        } == {"vetoed", "would_resolve", "skipped_stale", "resolved", "resolve_failed"}
        assert not hasattr(cl, "State")
        assert not hasattr(cl, "_TRANSITION_TABLE")
        assert not hasattr(cl, "_drive_candidate_to_terminal")

    def test_cap_guard_terminal_does_not_call_gate(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        gate = FakeGate(GateVerdict(True, "ok"))
        ctx = _guard_context(read, gate, max_resolves=2, approved_count=2)
        decision = cl._drive_candidate(ctx)
        assert decision is None
        assert ctx.capped is True
        assert gate.seen == []

    def test_truncation_guard_vetoes_before_gate(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        gate = FakeGate(GateVerdict(True, "ok"))
        candidate = Candidate(SANDBOX, 1, "A", (("r", "please fix"),), comments_truncated=True)
        decision = cl._gate_guard(_guard_context(read, gate, candidate=candidate))
        assert decision == Decision(SANDBOX, 1, "A", "vetoed", "comments_truncated")
        assert gate.seen == []

    def test_dry_run_terminal_skips_freshness_and_resolve(self) -> None:
        read = FakeReadGql(
            {SANDBOX: [1]},
            {(SANDBOX, 1): [_thread(tid="A", comments=[("r", "please fix")])]},
            comment_counts={"A": 99},
        )
        attempts: list[Candidate] = []

        def _track(cand: Candidate, _gql) -> Decision:
            attempts.append(cand)
            return Decision(cand.repo, cand.pr, cand.thread_id, "resolved", "ok")

        decision = cl._drive_candidate(
            _guard_context(read, FakeGate(GateVerdict(True, "ok")), do_resolve=_track, dry_run=True)
        )
        assert decision == Decision(SANDBOX, 1, "A", "would_resolve", "ok")
        assert attempts == []

    def test_freshness_guard_skips_stale_without_resolve(self) -> None:
        read = FakeReadGql(
            {SANDBOX: [1]},
            {(SANDBOX, 1): [_thread(tid="A", comments=[("r", "please fix")])]},
            comment_counts={"A": 2},
        )
        attempts: list[Candidate] = []

        def _track(cand: Candidate, _gql) -> Decision:
            attempts.append(cand)
            return Decision(cand.repo, cand.pr, cand.thread_id, "resolved", "ok")

        decision = cl._freshness_guard(
            _guard_context(read, FakeGate(GateVerdict(True, "ok")), do_resolve=_track)
        )
        assert decision == Decision(SANDBOX, 1, "A", "skipped_stale", "comments_changed")
        assert attempts == []

    def test_write_ahead_audit_precedes_resolve(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        read = FakeReadGql(
            {SANDBOX: [1]},
            {(SANDBOX, 1): [_thread(tid="A", comments=[("r", "please fix")])]},
        )
        events: list[str] = []

        def _track(cand: Candidate, _gql) -> Decision:
            events.append(audit.read_text(encoding="utf-8"))
            return Decision(cand.repo, cand.pr, cand.thread_id, "resolved", "ok")

        decision = cl._drive_candidate(
            _guard_context(
                read,
                FakeGate(GateVerdict(True, "ok")),
                do_resolve=_track,
                audit_path=audit,
            )
        )
        assert decision == Decision(SANDBOX, 1, "A", "resolved", "ok")
        assert '"action":"resolving"' in events[0]


# --------------------------------------------------------------------------- #
# _do_resolve integration with the real resolve_conversations
# --------------------------------------------------------------------------- #


class TestDoResolveIntegration:
    """Exercise the real resolve_conversations through _do_resolve with a fake gql
    that mimics the resolver's expected responses (identity + node + mutation)."""

    def _resolver_gql(self, *, viewer="iterwheel-countdown-user", resolved_after=True):
        def _gql(query: str, variables: dict) -> dict:
            if "query viewer" in query.lower():
                return {"viewer": {"login": viewer}}
            if "mutation" in query.lower():
                return {"resolveReviewThread": {"thread": {"isResolved": resolved_after}}}
            # node lookup for thread_id mode
            return {
                "node": {
                    "id": variables.get("threadId"),
                    "isResolved": False,
                    "isOutdated": False,
                    "viewerCanResolve": True,
                    "viewerCanReply": True,
                    "pullRequest": {"repository": {"nameWithOwner": REAL}},
                }
            }

        return _gql

    def test_resolve_success(self) -> None:
        cand = Candidate(REAL, 1, "T1", (("a", "b"),))
        d = cl._do_resolve(cand, self._resolver_gql())
        assert d.action == "resolved"

    def test_wrong_identity_is_resolve_failed(self) -> None:
        cand = Candidate(REAL, 1, "T1", (("a", "b"),))
        d = cl._do_resolve(cand, self._resolver_gql(viewer="ryosaeba1985"))
        assert d.action == "resolve_failed"
        assert "ryosaeba1985" not in d.reason or "identity" in d.reason.lower()

    def test_thread_no_longer_resolvable_is_skipped_stale(self) -> None:
        # node lookup reports the thread already resolved → resolve_conversations skips it
        # (resolved=0, action=skipped_guard) → benign skip, not a failure.
        def _gql(query: str, variables: dict) -> dict:
            if "query viewer" in query.lower():
                return {"viewer": {"login": "iterwheel-countdown-user"}}
            return {
                "node": {
                    "id": variables.get("threadId"),
                    "isResolved": True,  # moved out from under us
                    "isOutdated": False,
                    "viewerCanResolve": True,
                    "viewerCanReply": True,
                    "pullRequest": {"repository": {"nameWithOwner": REAL}},
                }
            }

        d = cl._do_resolve(Candidate(REAL, 1, "T1", (("a", "b"),)), _gql)
        assert d.action == "skipped_stale"

    def test_verify_failed_is_resolve_failed(self) -> None:
        # mutation runs but GitHub reports the thread still unresolved → verify_failed,
        # which must surface as resolve_failed (not a benign stale skip).
        d = cl._do_resolve(
            Candidate(REAL, 1, "T1", (("a", "b"),)),
            self._resolver_gql(resolved_after=False),
        )
        assert d.action == "resolve_failed"
        assert d.reason == "verify_failed"
