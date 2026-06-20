from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from voyager.bots.assembly.adapters import AdapterExecutionContext, AdapterResult
from voyager.bots.assembly.constants import ASSEMBLY_AGENT_SLUG, AUTHORIZED_ASSOCIATIONS_ENV
from voyager.bots.review_fix import (
    REVIEW_FIX_AGENT_SLUG,
    REVIEW_FIX_DYNAMIC,
    route_review_fix_event,
    should_run_review_fix,
)
from voyager.bots.review_fix import writeback as review_fix_writeback
from voyager.bots.review_fix.constants import REVIEW_FIX_COMMENT_MARKER
from voyager.core.config import AssemblyConfig, BridgeConfig, ReviewFixConfig, VoyagerConfig
from voyager.core.writeback import dispatch_route_writeback
from voyager.governance.audit_log import ReviewFixAuditLog
from voyager.governance.enablement import Autonomy, EnablementConfig, SafetyEnvelope


@pytest.fixture(autouse=True)
def _authorize_owner_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")


class _FakeReviewFixClient:
    def __init__(
        self,
        *,
        pull: dict[str, Any] | None = None,
        pull_batches: list[dict[str, Any]] | None = None,
        threads: list[dict[str, Any]] | None = None,
        thread_batches: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.pull = pull or _pull()
        self.pull_batches = list(pull_batches or [])
        self.threads = threads or [_codex_thread()]
        self.thread_batches = list(thread_batches or [])
        self.pull_calls: list[tuple[str, str, int]] = []
        self.thread_calls: list[tuple[str, str, int]] = []
        self.thread_loop_ids: list[int] = []
        self.upsert_issue_comment = AsyncMock(
            return_value={
                "id": 777,
                "html_url": "https://github.com/iterwheel/voyager/pull/187#issuecomment-777",
            }
        )

    async def pull_request(self, app_slug: str, repo: str, pull_number: int) -> dict[str, Any]:
        self.pull_calls.append((app_slug, repo, pull_number))
        if self.pull_batches:
            self.pull = self.pull_batches.pop(0)
        return self.pull

    async def pull_request_review_threads(
        self,
        app_slug: str,
        repo: str,
        pull_number: int,
    ) -> list[dict[str, Any]]:
        self.thread_calls.append((app_slug, repo, pull_number))
        self.thread_loop_ids.append(id(asyncio.get_running_loop()))
        if self.thread_batches:
            return self.thread_batches.pop(0)
        return self.threads


def _payload(body: str = "/review-fix") -> dict[str, Any]:
    return {
        "action": "created",
        "sender": {"login": "ryosaeba1985"},
        "repository": {"full_name": "iterwheel/voyager"},
        "comment": {
            "body": body,
            "author_association": "OWNER",
            "user": {"login": "ryosaeba1985", "type": "User"},
        },
        "issue": {
            "number": 187,
            "title": "Review fix target PR",
            "html_url": "https://github.com/iterwheel/voyager/pull/187",
            "pull_request": {"url": "https://api.github.com/repos/iterwheel/voyager/pulls/187"},
        },
    }


def _route() -> dict[str, Any]:
    route = route_review_fix_event("issue_comment", _payload())[0]
    route["delivery_id"] = "delivery-review-fix"
    return route


def _pull(
    *,
    head_ref: str = "feature/review-fix",
    head_sha: str = "a" * 40,
) -> dict[str, Any]:
    return {
        "number": 187,
        "state": "open",
        "html_url": "https://github.com/iterwheel/voyager/pull/187",
        "user": {"login": "ryosaeba1985"},
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {"full_name": "iterwheel/voyager"},
        },
        "base": {
            "ref": "main",
            "repo": {"full_name": "iterwheel/voyager", "default_branch": "main"},
        },
    }


def _codex_thread(
    *,
    thread_id: str = "PRRT_review_fix_1",
    with_reviewer_reply: bool = False,
    path: str = "voyager/example.py",
    line: int = 12,
) -> dict[str, Any]:
    comments = [
        {
            "databaseId": 101,
            "author": {"login": "chatgpt-codex-connector"},
            "body": f"Codex Review: handle the missing error branch in {path}.",
            "url": "https://github.com/iterwheel/voyager/pull/187#discussion_r101",
        }
    ]
    if with_reviewer_reply:
        comments.append(
            {
                "databaseId": 102,
                "author": {"login": "maintainer-reviewer"},
                "body": "I agree this needs a code change before merge.",
                "url": "https://github.com/iterwheel/voyager/pull/187#discussion_r102",
            }
        )
    return {
        "id": thread_id,
        "isResolved": False,
        "isOutdated": False,
        "path": path,
        "line": line,
        "comments": {"nodes": comments},
    }


def _cfg(
    tmp_path: Path,
    *,
    enablement: EnablementConfig | None,
    dry_run: bool = True,
) -> VoyagerConfig:
    return VoyagerConfig(
        apps={},
        work_dir=tmp_path / "state",
        profiles={},
        default_profile=None,
        bridge=BridgeConfig(dry_run=dry_run),
        assembly=AssemblyConfig(execution_backend="dry-run"),
        review_fix=ReviewFixConfig(
            enablement=enablement,
            audit_dir=tmp_path / "audit",
        ),
    )


def _l3(tmp_path: Path) -> EnablementConfig:
    return EnablementConfig(
        autonomy=Autonomy.L3,
        envelope=SafetyEnvelope(
            max_rounds=3,
            max_fixes_per_round=2,
            kill_switch_path=tmp_path / "review-fix.disabled",
            escalation="request-human-review",
            verify_command="pytest tests/unit/test_review_fix_bot.py",
        ),
    )


def test_review_fix_route_triggers_only_for_pr_command(
    monkeypatch,
) -> None:
    monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")

    assert should_run_review_fix("issue_comment", _payload("/pr-review-fix"))
    assert not should_run_review_fix("issue_comment", {**_payload(), "issue": {"number": 187}})

    routes = route_review_fix_event("issue_comment", _payload("/review-fix --dry-run"))

    assert len(routes) == 1
    route = routes[0]
    assert route["agent"] == REVIEW_FIX_AGENT_SLUG
    assert route["kind"] == "review_fix_loop"
    assert route["validation"]["status"] == "review_fix_ready"
    assert route["validation"]["pr_number"] == 187
    assert route["writeback"]["dynamic"] == REVIEW_FIX_DYNAMIC
    assert route["writeback"]["command_flags"] == {"dry_run": True}


def test_dispatch_refuses_missing_enablement_without_fetching_github(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    client = _FakeReviewFixClient()

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=_cfg(tmp_path, enablement=None),
        )
    )

    assert result["status"] == "review_fix_refused"
    assert result["refusal"]["reason"] == "missing_review_fix_enablement"
    assert client.pull_calls == []
    assert client.thread_calls == []


def test_dispatch_refuses_default_branch_target_before_thread_poll(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    client = _FakeReviewFixClient(pull=_pull(head_ref="main"))

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=_cfg(tmp_path, enablement=_l3(tmp_path)),
        )
    )

    assert result["status"] == "review_fix_refused"
    assert result["refusal"]["reason"] == "default_branch_target_refused"
    assert len(client.pull_calls) == 1
    assert client.thread_calls == []


def test_dispatch_refuses_missing_pr_repo_metadata_before_thread_poll(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    pull = _pull()
    pull["head"]["repo"] = None
    client = _FakeReviewFixClient(pull=pull)

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=_cfg(tmp_path, enablement=_l3(tmp_path)),
        )
    )

    assert result["status"] == "review_fix_refused"
    assert result["refusal"]["reason"] == "missing_pr_repo_metadata"
    assert len(client.pull_calls) == 1
    assert client.thread_calls == []


def test_dispatch_dry_run_converges_and_writes_audit_log(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    client = _FakeReviewFixClient()

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=_cfg(tmp_path, enablement=_l3(tmp_path), dry_run=True),
        )
    )

    assert result["status"] == "review_fix_converged"
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["auto_merge"] is False
    assert result["outcome"]["rounds_run"] == 2
    assert result["adapter_results"][0]["status"] == "dry_run"
    assert result["contracts"][0]["branch_name"] == "feature/review-fix"
    assert result["contracts"][0]["extra"]["review_fix"]["expected_head_sha"] == "a" * 40
    assert len(client.pull_calls) == 1
    assert client.thread_calls == [(ASSEMBLY_AGENT_SLUG, "iterwheel/voyager", 187)]

    records = ReviewFixAuditLog(result["audit_log_path"]).read_all()
    assert [(record.finding_id, record.verdict) for record in records] == [
        ("PRRT_review_fix_1", "dry_run"),
        ("round:1", "round_fixed"),
        ("round:2", "round_clean"),
        ("loop", "converged"),
    ]


def test_dispatch_still_fixes_when_non_author_reviewer_replied(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    client = _FakeReviewFixClient(threads=[_codex_thread(with_reviewer_reply=True)])

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=_cfg(tmp_path, enablement=_l3(tmp_path), dry_run=True),
        )
    )

    assert result["status"] == "review_fix_converged"
    assert result["adapter_results"][0]["status"] == "dry_run"


def test_prepare_adapter_passes_expected_head_sha_to_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False)
    context = review_fix_writeback._LoopContext(
        client=_FakeReviewFixClient(),
        route=_route(),
        repository="iterwheel/voyager",
        pull=_pull(),
        threads=[_codex_thread()],
        enablement=_l3(tmp_path),
        audit_log_path=tmp_path / "audit.jsonl",
        dry_run=False,
        cfg=cfg,
    )
    contract = review_fix_writeback._build_contract(
        context,
        review_fix_writeback.ReviewFixFinding(
            finding_id="PRRT_review_fix_1",
            category="codex-review",
        ),
        _codex_thread(),
    )

    async def fake_build_adapter_context(*args: Any, **kwargs: Any) -> AdapterExecutionContext:
        return AdapterExecutionContext(
            repository="iterwheel/voyager",
            workdir=tmp_path,
            timeout_seconds=120,
            command_path="omp",
        )

    monkeypatch.setattr(
        review_fix_writeback,
        "_build_adapter_context",
        fake_build_adapter_context,
    )

    _adapter, adapter_context = asyncio.run(
        review_fix_writeback._prepare_adapter(context, contract)
    )

    assert adapter_context.expected_remote_sha == "a" * 40


def test_stale_head_guard_escalates_before_adapter_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    cfg = _cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False)
    client = _FakeReviewFixClient(
        pull_batches=[
            _pull(head_sha="a" * 40),
            _pull(head_sha="b" * 40),
        ]
    )

    async def fail_prepare_adapter(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        raise AssertionError("adapter must not run after stale-head guard failure")

    monkeypatch.setattr(review_fix_writeback, "_prepare_adapter", fail_prepare_adapter)

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=cfg,
        )
    )

    assert result["status"] == "review_fix_escalated"
    assert result["adapter_results"] == []
    assert client.pull_calls == [
        (ASSEMBLY_AGENT_SLUG, "iterwheel/voyager", 187),
        (ASSEMBLY_AGENT_SLUG, "iterwheel/voyager", 187),
    ]
    records = ReviewFixAuditLog(result["audit_log_path"]).read_all()
    assert records[-1].verdict == "escalated"
    assert any("stale_pr_head" in item for item in records[-1].tests)


def test_actor_refusal_upserts_visible_comment_when_not_dry_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    payload = _payload()
    payload["sender"] = {"login": "drive-by"}
    payload["comment"]["author_association"] = "CONTRIBUTOR"
    payload["comment"]["user"] = {
        "login": "drive-by",
        "type": "User",
    }
    route = route_review_fix_event("issue_comment", payload)[0]
    route["delivery_id"] = "delivery-review-fix"
    client = _FakeReviewFixClient()

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            route,
            repository="iterwheel/voyager",
            cfg=_cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False),
        )
    )

    assert result["status"] == "review_fix_refused"
    assert result["refusal"]["reason"] == "unauthorized_actor"
    assert client.pull_calls == []
    assert client.thread_calls == []
    client.upsert_issue_comment.assert_awaited_once()
    call = client.upsert_issue_comment.await_args
    assert call.args[:3] == (REVIEW_FIX_AGENT_SLUG, "iterwheel/voyager", 187)
    assert call.kwargs["marker"] == REVIEW_FIX_COMMENT_MARKER
    body = call.kwargs["body"]
    assert "Review-fix refused this invocation" in body
    assert "unauthorized_actor" in body
    assert "drive-by" in body


def test_no_changes_result_does_not_clear_finding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    cfg = _cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False)
    client = _FakeReviewFixClient()

    class FakeAdapter:
        name = "fake"
        requires_installation_token = False
        supports_resume = False

    async def fake_build_adapter_context(*args: Any, **kwargs: Any) -> AdapterExecutionContext:
        return AdapterExecutionContext(
            repository="iterwheel/voyager",
            workdir=tmp_path,
            timeout_seconds=120,
            command_path="omp",
        )

    async def fake_execute(*args: Any, **kwargs: Any) -> AdapterResult:
        return AdapterResult(
            status="no_changes",
            commit_shas=[],
            summary="adapter found no changes",
        )

    monkeypatch.setattr(
        review_fix_writeback,
        "select_execution_adapter",
        lambda cfg=None: FakeAdapter(),
    )
    monkeypatch.setattr(
        review_fix_writeback,
        "_build_adapter_context",
        fake_build_adapter_context,
    )
    monkeypatch.setattr(review_fix_writeback, "_execute_adapter", fake_execute)

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=cfg,
        )
    )

    assert result["status"] == "review_fix_escalated"
    assert result["outcome"]["status"] == "escalated"
    assert [item["status"] for item in result["adapter_results"]] == [
        "no_changes",
        "no_changes",
        "no_changes",
    ]
    records = ReviewFixAuditLog(result["audit_log_path"]).read_all()
    assert records[-1].verdict == "escalated"
    assert "converged" not in [record.verdict for record in records]


def test_backend_dry_run_result_does_not_clear_when_writes_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    cfg = _cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False)
    client = _FakeReviewFixClient()

    class FakeAdapter:
        name = "fake"
        requires_installation_token = False
        supports_resume = False

    async def fake_build_adapter_context(*args: Any, **kwargs: Any) -> AdapterExecutionContext:
        return AdapterExecutionContext(
            repository="iterwheel/voyager",
            workdir=tmp_path,
            timeout_seconds=120,
            command_path="omp",
        )

    async def fake_execute(*args: Any, **kwargs: Any) -> AdapterResult:
        return AdapterResult(
            status="dry_run",
            commit_shas=[],
            summary="backend is dry-run",
        )

    monkeypatch.setattr(
        review_fix_writeback,
        "select_execution_adapter",
        lambda cfg=None: FakeAdapter(),
    )
    monkeypatch.setattr(
        review_fix_writeback,
        "_build_adapter_context",
        fake_build_adapter_context,
    )
    monkeypatch.setattr(review_fix_writeback, "_execute_adapter", fake_execute)

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=cfg,
        )
    )

    assert result["status"] == "review_fix_escalated"
    assert [item["status"] for item in result["adapter_results"]] == [
        "dry_run",
        "dry_run",
        "dry_run",
    ]
    records = ReviewFixAuditLog(result["audit_log_path"]).read_all()
    assert records[-1].verdict == "escalated"
    assert "converged" not in [record.verdict for record in records]


def test_successful_fix_refreshes_expected_head_sha_for_next_fix(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    cfg = _cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False)
    first_thread = _codex_thread(
        thread_id="PRRT_review_fix_1",
        path="voyager/example.py",
        line=12,
    )
    second_thread = _codex_thread(
        thread_id="PRRT_review_fix_2",
        path="voyager/other.py",
        line=34,
    )
    client = _FakeReviewFixClient(
        thread_batches=[
            [first_thread, second_thread],
            [second_thread],
            [],
        ]
    )
    observed_context_shas: list[str | None] = []
    observed_contract_shas: list[str] = []
    observed_audit_ids: list[str] = []

    class FakeAdapter:
        name = "fake"
        requires_installation_token = False
        supports_resume = False

    async def fake_build_adapter_context(*args: Any, **kwargs: Any) -> AdapterExecutionContext:
        observed_audit_ids.append(kwargs["audit_id"])
        return AdapterExecutionContext(
            repository="iterwheel/voyager",
            workdir=tmp_path,
            timeout_seconds=120,
            command_path="omp",
        )

    async def fake_execute(
        adapter: Any,
        contract: Any,
        adapter_context: AdapterExecutionContext,
    ) -> AdapterResult:
        observed_context_shas.append(adapter_context.expected_remote_sha)
        observed_contract_shas.append(contract.extra["review_fix"]["expected_head_sha"])
        next_sha = ("b" if len(observed_context_shas) == 1 else "c") * 40
        client.pull["head"]["sha"] = next_sha
        return AdapterResult(
            status="executed",
            commit_shas=[next_sha],
            summary="adapter pushed a fix",
        )

    monkeypatch.setattr(
        review_fix_writeback,
        "select_execution_adapter",
        lambda cfg=None: FakeAdapter(),
    )
    monkeypatch.setattr(
        review_fix_writeback,
        "_build_adapter_context",
        fake_build_adapter_context,
    )
    monkeypatch.setattr(review_fix_writeback, "_execute_adapter", fake_execute)

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=cfg,
        )
    )

    assert result["status"] == "review_fix_converged"
    assert observed_context_shas == ["a" * 40, "b" * 40]
    assert observed_contract_shas == ["a" * 40, "b" * 40]
    assert observed_audit_ids == [
        "review-fix-187-delivery-review-fix-r1-PRRT_review_fix_1",
        "review-fix-187-delivery-review-fix-r1-PRRT_review_fix_2",
    ]
    assert len(set(client.thread_loop_ids)) == 1


def test_executed_result_escalates_when_thread_refresh_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    cfg = _cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False)
    thread = _codex_thread()
    client = _FakeReviewFixClient()
    thread_fetches = 0

    async def flaky_review_threads(
        app_slug: str,
        repo: str,
        pull_number: int,
    ) -> list[dict[str, Any]]:
        nonlocal thread_fetches
        thread_fetches += 1
        client.thread_calls.append((app_slug, repo, pull_number))
        client.thread_loop_ids.append(id(asyncio.get_running_loop()))
        if thread_fetches == 1:
            return [thread]
        raise TimeoutError

    client.pull_request_review_threads = flaky_review_threads

    class FakeAdapter:
        name = "fake"
        requires_installation_token = False
        supports_resume = False

    async def fake_build_adapter_context(*args: Any, **kwargs: Any) -> AdapterExecutionContext:
        return AdapterExecutionContext(
            repository="iterwheel/voyager",
            workdir=tmp_path,
            timeout_seconds=120,
            command_path="omp",
        )

    async def fake_execute(*args: Any, **kwargs: Any) -> AdapterResult:
        return AdapterResult(
            status="executed",
            commit_shas=["b" * 40],
            summary="adapter pushed a fix",
        )

    monkeypatch.setattr(
        review_fix_writeback,
        "select_execution_adapter",
        lambda cfg=None: FakeAdapter(),
    )
    monkeypatch.setattr(
        review_fix_writeback,
        "_build_adapter_context",
        fake_build_adapter_context,
    )
    monkeypatch.setattr(review_fix_writeback, "_execute_adapter", fake_execute)

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=cfg,
        )
    )

    assert result["status"] == "review_fix_escalated"
    assert [item["status"] for item in result["adapter_results"]] == ["executed"]
    assert len(client.thread_calls) == 2
    records = ReviewFixAuditLog(result["audit_log_path"]).read_all()
    assert records[-1].verdict == "escalated"
    assert any("post_execution_thread_refresh_failed" in item for item in records[-1].tests)


def test_executed_result_does_not_clear_still_actionable_thread(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DRY_RUN", raising=False)
    cfg = _cfg(tmp_path, enablement=_l3(tmp_path), dry_run=False)
    thread = _codex_thread()
    client = _FakeReviewFixClient(
        thread_batches=[
            [thread],
            [thread],
            [thread],
            [thread],
        ]
    )
    commit_index = 0

    class FakeAdapter:
        name = "fake"
        requires_installation_token = False
        supports_resume = False

    async def fake_build_adapter_context(*args: Any, **kwargs: Any) -> AdapterExecutionContext:
        return AdapterExecutionContext(
            repository="iterwheel/voyager",
            workdir=tmp_path,
            timeout_seconds=120,
            command_path="omp",
        )

    async def fake_execute(*args: Any, **kwargs: Any) -> AdapterResult:
        nonlocal commit_index
        commit_index += 1
        next_sha = str(commit_index) * 40
        client.pull["head"]["sha"] = next_sha
        return AdapterResult(
            status="executed",
            commit_shas=[next_sha],
            summary="adapter pushed a fix",
        )

    monkeypatch.setattr(
        review_fix_writeback,
        "select_execution_adapter",
        lambda cfg=None: FakeAdapter(),
    )
    monkeypatch.setattr(
        review_fix_writeback,
        "_build_adapter_context",
        fake_build_adapter_context,
    )
    monkeypatch.setattr(review_fix_writeback, "_execute_adapter", fake_execute)

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            _route(),
            repository="iterwheel/voyager",
            cfg=cfg,
        )
    )

    assert result["status"] == "review_fix_escalated"
    assert [item["status"] for item in result["adapter_results"]] == [
        "executed",
        "executed",
        "executed",
    ]
    assert len(client.thread_calls) == 4
    records = ReviewFixAuditLog(result["audit_log_path"]).read_all()
    assert records[-1].verdict == "escalated"
