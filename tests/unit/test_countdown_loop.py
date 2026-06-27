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
    ) -> None:
        self._pr_numbers = pr_numbers
        self._threads = threads
        self._head_sha = head_sha
        self._fail_repos = fail_repos or set()
        self._pull_missing = pull_missing or set()
        self.calls: list[str] = []

    async def __call__(self, query: str, variables: dict) -> dict:
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

    async def should_resolve(self, candidate: Candidate) -> GateVerdict:
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


async def _run(read_gql, gate, **kw):
    resolve_fn = kw.pop("resolve_fn", None)
    if resolve_fn is None:
        resolve_fn, _ = _fake_resolver()
    return await run_resolve_loop(
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


class TestPrefilter:
    async def test_only_resolvable_threads_become_candidates(self) -> None:
        threads = [
            _thread(tid="A"),  # resolvable
            _thread(tid="B", resolved=True),  # already resolved
            _thread(tid="C", outdated=True),  # outdated
            _thread(tid="D", can_resolve=False),  # cannot resolve
            _thread(tid="E", can_reply=False),  # cannot reply
        ]
        read = FakeReadGql({SANDBOX: [7]}, {(SANDBOX, 7): threads})
        gate = FakeGate(GateVerdict(True, "ok"))
        await _run(read, gate)
        assert [c.thread_id for c in gate.seen] == ["A"]

    async def test_pr_not_found_is_tolerated_target_error(self) -> None:
        read = FakeReadGql({SANDBOX: [9]}, {}, pull_missing={(SANDBOX, 9)})
        summary = await _run(read, FakeGate())
        assert summary.errors
        assert summary.errors[0].pr == 9
        assert summary.resolved == 0


# --------------------------------------------------------------------------- #
# Safety invariants
# --------------------------------------------------------------------------- #


class TestGateVeto:
    async def test_veto_means_no_resolve(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        fn, resolved = _fake_resolver()
        summary = await _run(read, FakeGate(GateVerdict(False, "not addressed")), resolve_fn=fn)
        assert resolved == []
        assert summary.resolved == 0
        assert any(d.action == "vetoed" for d in summary.decisions)

    async def test_gate_exception_fails_closed(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        fn, resolved = _fake_resolver()
        summary = await _run(read, FakeGate(raises=True), resolve_fn=fn)
        assert resolved == []
        assert summary.resolved == 0
        assert all(d.action == "vetoed" for d in summary.decisions)
        assert "gate_error" in summary.decisions[0].reason

    async def test_truncated_comments_veto_without_calling_gate(self) -> None:
        # A thread with more comments than we fetched must fail closed: the gate would
        # judge on a partial prefix that may omit a later "still broken" reply.
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A", truncated=True)]})
        fn, resolved = _fake_resolver()
        gate = FakeGate(GateVerdict(True, "looks fixed"))
        summary = await _run(read, gate, resolve_fn=fn)
        assert resolved == []
        assert gate.seen == []  # gate never consulted on a truncated thread
        assert summary.decisions[0].action == "vetoed"
        assert summary.decisions[0].reason == "comments_truncated"

    async def test_injection_approve_all_cannot_resolve_noncandidates(self) -> None:
        # Gate approves EVERYTHING (simulating prompt-injection success). Non-candidates
        # (resolved/outdated/can't-resolve) must STILL never resolve — the deterministic
        # prefilter is the hard boundary.
        threads = [
            _thread(tid="A"),  # the only real candidate
            _thread(tid="B", resolved=True),
            _thread(tid="C", outdated=True),
            _thread(tid="D", can_resolve=False),
        ]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        fn, resolved = _fake_resolver()
        await _run(read, FakeGate(GateVerdict(True, "resolve everything!")), resolve_fn=fn)
        assert [c.thread_id for c in resolved] == ["A"]


class TestDryRun:
    async def test_dry_run_issues_no_resolve(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})
        fn, resolved = _fake_resolver()
        summary = await _run(read, FakeGate(), resolve_fn=fn, dry_run=True)
        assert resolved == []
        assert summary.would_resolve == 1
        assert summary.resolved == 0


class TestCap:
    async def test_max_resolves_caps_and_flags(self) -> None:
        threads = [_thread(tid=f"T{i}") for i in range(5)]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        fn, resolved = _fake_resolver()
        summary = await _run(read, FakeGate(), resolve_fn=fn, max_resolves=2)
        assert len(resolved) == 2
        assert summary.capped is True

    async def test_dry_run_caps_would_resolves(self) -> None:
        # The cap bounds APPROVED candidates in both modes — dry-run must not fan out
        # past max_resolves even though it never calls the resolver.
        threads = [_thread(tid=f"T{i}") for i in range(5)]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        summary = await _run(read, FakeGate(), dry_run=True, max_resolves=2)
        assert summary.would_resolve == 2
        assert summary.capped is True

    async def test_failed_attempts_count_against_cap(self) -> None:
        # The cap bounds APPROVED ATTEMPTS, not just successes: a resolver that keeps
        # failing must still stop the loop at max_resolves (blast-radius guarantee).
        threads = [_thread(tid=f"T{i}") for i in range(5)]
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): threads})
        attempts: list[Candidate] = []

        def _always_fail(cand: Candidate, _gql) -> Decision:
            attempts.append(cand)
            return Decision(cand.repo, cand.pr, cand.thread_id, "resolve_failed", "boom")

        summary = await _run(read, FakeGate(), resolve_fn=_always_fail, max_resolves=2)
        assert len(attempts) == 2
        assert summary.capped is True
        assert summary.resolved == 0


class TestRedaction:
    async def test_real_repo_redacts_identifiers(self) -> None:
        read = FakeReadGql({REAL: [1]}, {(REAL, 1): [_thread(tid="SECRET")]})
        summary = await _run(read, FakeGate(), repos=[REAL])
        pub = summary.to_public_dict()
        blob = str(pub)
        assert "SECRET" not in blob
        assert all("pr" not in d for d in pub["decisions"])
        assert all(d.get("redacted") for d in pub["decisions"])

    async def test_sandbox_shows_identifiers(self) -> None:
        read = FakeReadGql({SANDBOX: [42]}, {(SANDBOX, 42): [_thread(tid="OK")]})
        summary = await _run(read, FakeGate(), repos=[SANDBOX])
        pub = summary.to_public_dict()
        assert pub["decisions"][0]["pr"] == 42
        assert pub["decisions"][0]["thread_id"] == "OK"

    async def test_llm_reason_not_leaked_for_real_repo(self, tmp_path: Path) -> None:
        # The gate's reason is LLM free-text over comment bodies; for non-sandbox repos
        # it must never reach public output OR the audit (it could echo a raw PR#/node-ID).
        leak = "PRRT_kwDOSECRETnodeid #1337"
        audit = tmp_path / "audit.jsonl"
        read = FakeReadGql({REAL: [1]}, {(REAL, 1): [_thread(tid="A")]})
        await _run(read, FakeGate(GateVerdict(False, leak)), repos=[REAL], audit_path=audit)
        # would have been recorded as a veto carrying `leak` as its reason
        assert leak not in audit.read_text(encoding="utf-8")


class TestIdentityGate:
    async def test_dry_run_still_asserts_identity(self) -> None:
        # dry-run enumerates threads and ships comments to the LLM, so a wrong machine
        # identity must abort up front there too — not only on live resolves.
        read = FakeReadGql({SANDBOX: [1]}, {(SANDBOX, 1): [_thread(tid="A")]})

        def _wrong_identity_gql(query: str, variables: dict) -> dict:
            return {"viewer": {"login": "ryosaeba1985"}}  # not the machine account

        with pytest.raises(cl.ResolveConversationError):
            await run_resolve_loop(
                requested_repos=[SANDBOX],
                gate=FakeGate(),
                read_gql=read,
                resolve_gql=_wrong_identity_gql,
                resolve_fn=None,  # real path → identity asserted before any LLM call
                dry_run=True,
                audit_path=None,
            )


class TestAuditFailClosed:
    async def test_unwritable_audit_aborts_before_mutation(
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
            await run_resolve_loop(
                requested_repos=[SANDBOX],
                gate=FakeGate(),
                read_gql=read,
                resolve_gql=object(),
                resolve_fn=_track,
                audit_path=tmp_path / "audit.jsonl",
            )
        assert attempts == []  # never mutated


class TestSystemicFailure:
    async def test_all_repos_fail_enumeration_is_systemic(self) -> None:
        read = FakeReadGql({SANDBOX: [1]}, {}, fail_repos={SANDBOX})
        summary = await _run(read, FakeGate())
        assert summary.systemic_failure is True

    async def test_one_repo_fails_others_continue(self) -> None:
        read = FakeReadGql(
            {REAL: [1], SANDBOX: [2]},
            {(SANDBOX, 2): [_thread(tid="A")]},
            fail_repos={REAL},
        )
        summary = await _run(read, FakeGate(), repos=[REAL, SANDBOX])
        assert summary.systemic_failure is False
        assert summary.resolved == 1
        assert any(e.repo == REAL for e in summary.errors)


class TestAudit:
    async def test_audit_records_redacted_decisions(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        read = FakeReadGql({REAL: [1]}, {(REAL, 1): [_thread(tid="SECRET")]})
        await _run(read, FakeGate(), repos=[REAL], audit_path=audit)
        text = audit.read_text(encoding="utf-8")
        assert text.strip()  # at least one line
        assert "SECRET" not in text  # redacted in audit too
        assert "redacted" in text


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
