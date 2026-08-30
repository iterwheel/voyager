from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from voyager.bots.clearance.author_wakeup import (
    AuthorWakeupReconciler,
    DoorAck,
    DoorReceipt,
    NotificationState,
    ObservationKey,
    ObservationState,
    PfcDoorClient,
    PfcDoorProtocolError,
    WakeupLedger,
    build_wakeup_message,
    is_author_wakeup_eligible,
)
from voyager.bots.clearance.models import PollRecord, Severity, Status, Thread, Verdict
from voyager.bots.clearance.state import StateStore
from voyager.core.config import AuthorWakeupConfig
from voyager.core.github_app import GitHubGraphQLError


def _persisted_thread(**overrides: object) -> Thread:
    data: dict[str, object] = {
        "id": "PRRT_state_a",
        "comment_id": 101,
        "path": "voyager/core/example.py",
        "line": 12,
        "codex_severity": Severity.P2,
        "effective_severity": Severity.P2,
        "verdict": Verdict.OPEN,
        "github_isResolved": False,
        "author_reply_id": None,
    }
    data.update(overrides)
    return Thread.model_validate(data)


def _live_thread(
    *,
    reply_login: str | None = None,
    resolved: bool = False,
    thread_id: str = "PRRT_state_a",
    reply_created_at: str = "2026-08-30T00:50:00Z",
) -> dict:
    comments = [
        {
            "databaseId": 101,
            "author": {"login": "chatgpt-codex-connector"},
            "body": "P2 finding",
            "createdAt": "2026-08-30T00:00:00Z",
        }
    ]
    if reply_login:
        comments.append(
            {
                "databaseId": 102,
                "author": {"login": reply_login},
                "body": "reply",
                "createdAt": reply_created_at,
            }
        )
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": False,
        "comments": {"nodes": comments},
    }


def _delivery_dependencies(
    tmp_path: Path,
    notification: NotificationState,
    now: datetime,
) -> tuple[StateStore, AsyncMock]:
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now - timedelta(minutes=1),
            repo=notification.repository,
            pr=notification.pull_number,
            head_sha=notification.head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread(id=notification.thread_ids[0])],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": notification.pull_number,
        "state": "open",
        "head": {"sha": notification.head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [
        _live_thread(thread_id=notification.thread_ids[0])
    ]
    return clearance_store, github


def test_author_wakeup_eligibility_keys_replies_to_pr_author() -> None:
    persisted = _persisted_thread()

    assert is_author_wakeup_eligible(
        persisted,
        _live_thread(reply_login="maintainer"),
        pr_author_login="ryosaeba1985",
    )
    assert not is_author_wakeup_eligible(
        persisted,
        _live_thread(reply_login="ryosaeba1985"),
        pr_author_login="ryosaeba1985",
    )
    assert not is_author_wakeup_eligible(
        persisted,
        _live_thread(resolved=True),
        pr_author_login="ryosaeba1985",
    )
    assert not is_author_wakeup_eligible(
        persisted,
        _live_thread(),
        pr_author_login=None,
    )


def test_wakeup_message_is_v1_and_has_no_delivery_deadline() -> None:
    message = build_wakeup_message(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_two", "PRRT_one"),
        notify_after_minutes=10,
        fallback_after_minutes=20,
    )

    assert message == (
        "[voyager-clearance-author-wakeup/v1]\n"
        f"notification_id: {'a' * 32}\n"
        "repository: iterwheel/voyager-sandbox\n"
        "pull_request: 42\n"
        f"head_sha: {'b' * 40}\n"
        "thread_ids: PRRT_one,PRRT_two\n"
        "notify_after_minutes: 10\n"
        "fallback_after_minutes: 20\n"
        "instruction: route this to the citizen that opened the PR; claim by replying "
        "as the PR author, resolving a listed thread, or pushing a new head"
    )
    assert "deadline" not in message


def test_wakeup_ledger_survives_restart_and_keeps_event_history(tmp_path) -> None:
    path = tmp_path / "private" / "author-wakeup.db"
    key = ObservationKey("iterwheel/voyager-sandbox", 42, "b" * 40, "PRRT_one")
    first_seen = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    later = datetime(2026, 8, 30, 0, 5, tzinfo=UTC)
    ledger = WakeupLedger(path)

    assert ledger.observe(key, first_seen).first_seen == first_seen
    assert WakeupLedger(path).observe(key, later).first_seen == first_seen

    notification = NotificationState(
        notification_id="a" * 32,
        repository=key.repository,
        pull_number=key.pull_number,
        head_sha=key.head_sha,
        thread_ids=(key.thread_id,),
        state="notify_intent",
        created_at=later,
    )
    ledger.save_notification(notification, event="notify_intent", at=later)
    delivered = notification.with_updates(state="notified", recipient_citizen="voyager")
    ledger.save_notification(delivered, event="notified", at=later)

    restarted = WakeupLedger(path)
    assert restarted.notifications() == (delivered,)
    assert [event["event"] for event in restarted.events()] == [
        "state_a_observed",
        "notify_intent",
        "notified",
    ]
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cleared_observation_reactivates_with_a_new_continuous_window(tmp_path: Path) -> None:
    ledger = WakeupLedger(tmp_path / "state.db")
    key = ObservationKey("iterwheel/voyager-sandbox", 42, "b" * 40, "PRRT_one")
    first = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    reactivated = first + timedelta(minutes=2)

    ledger.observe(key, first)
    ledger.reconcile_active_observations(
        repository=key.repository,
        pull_number=key.pull_number,
        current_head_sha=key.head_sha,
        eligible_keys=set(),
        at=first + timedelta(seconds=30),
    )
    current = ledger.observe(key, reactivated)

    assert current.status == "active"
    assert current.first_seen == reactivated
    assert current.notification_id is None
    assert [event["event"] for event in ledger.events()] == [
        "state_a_observed",
        "state_a_cleared",
        "state_a_observed",
    ]


@pytest.mark.asyncio
async def test_pfc_client_posts_v1_message_and_derives_receipt_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "queued": True,
                    "send_id": "c" * 32,
                    "idempotency_retention_seconds": 86400,
                },
            )
        return httpx.Response(
            200,
            json={
                "found": True,
                "outcome": {
                    "stage": "author_delivered",
                    "ok": True,
                    "notification_id": "a" * 32,
                    "recipient_citizen": "voyager",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PfcDoorClient(
            "http://127.0.0.1:8421/api/agent-send",
            required_retention_seconds=86400,
            http=http,
        )
        ack = await client.post(
            message="hello",
            transport_send_id="c" * 32,
        )
        receipt = await client.receipt(
            transport_send_id="c" * 32,
            notification_id="a" * 32,
        )

    assert ack.retention_seconds == 86400
    assert receipt.state == "author_delivered"
    assert receipt.recipient_citizen == "voyager"
    assert requests[0].url == httpx.URL("http://127.0.0.1:8421/api/agent-send")
    assert requests[0].read() == (
        b'{"citizen":"pfc","message":"hello","send_id":"cccccccccccccccccccccccccccccccc"}'
    )
    assert requests[1].url == httpx.URL(
        "http://127.0.0.1:8421/api/agent-send-result/cccccccccccccccccccccccccccccccc"
    )


@pytest.mark.asyncio
async def test_pfc_client_fails_closed_on_short_retention_and_pending_receipt() -> None:
    responses = iter(
        (
            {"ok": True, "queued": True, "send_id": "d" * 32},
            {"found": True, "outcome": {"stage": "pfc_received", "ok": True}},
        )
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PfcDoorClient(
            "http://localhost:8420/api/agent-send",
            required_retention_seconds=86400,
            http=http,
        )
        with pytest.raises(PfcDoorProtocolError, match="idempotency_retention_seconds"):
            await client.post(message="hello", transport_send_id="d" * 32)
        receipt = await client.receipt(
            transport_send_id="d" * 32,
            notification_id="a" * 32,
        )

    assert receipt.state == "pfc_received"


@pytest.mark.asyncio
async def test_pfc_client_treats_outcome_without_boolean_ok_as_pending() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"found": True, "outcome": {"stage": "author_delivered"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PfcDoorClient(
            "http://localhost:8420/api/agent-send",
            required_retention_seconds=86400,
            http=http,
        )
        receipt = await client.receipt(
            transport_send_id="d" * 32,
            notification_id="a" * 32,
        )

    assert receipt.state == "pending"


@pytest.mark.asyncio
async def test_pfc_client_rejects_non_object_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PfcDoorClient(
            "http://localhost:8420/api/agent-send",
            required_retention_seconds=86400,
            http=http,
        )
        with pytest.raises(PfcDoorProtocolError, match="JSON object"):
            await client.post(message="hello", transport_send_id="d" * 32)
        with pytest.raises(PfcDoorProtocolError, match="JSON object"):
            await client.receipt(
                transport_send_id="d" * 32,
                notification_id="a" * 32,
            )


def test_pfc_client_disables_environment_proxy_for_owned_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    fake_http = AsyncMock()

    def build_http(**kwargs: object) -> AsyncMock:
        created.update(kwargs)
        return fake_http

    monkeypatch.setattr(httpx, "AsyncClient", build_http)

    client = PfcDoorClient(
        "http://localhost:8420/api/agent-send",
        required_retention_seconds=86400,
    )

    assert client.http is fake_http
    assert created == {"timeout": 20, "trust_env": False}


@pytest.mark.asyncio
async def test_reconciler_waits_for_n_then_batches_and_dedupes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    door = AsyncMock()
    door.post.return_value = DoorAck("c" * 32, 86400)
    door.receipt.return_value = DoorReceipt(state="pfc_received")
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    monkeypatch.setattr(
        ledger,
        "observations",
        lambda: pytest.fail("scan must not materialize full observation history"),
    )
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            notify_after_minutes=1,
            allowed_repositories=(repository,),
            audit_dir=tmp_path / "wakeup",
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
    )

    first = await reconciler.tick(now=now, scan=True)
    due = await reconciler.tick(now=now + timedelta(minutes=1), scan=True)
    duplicate = await reconciler.tick(now=now + timedelta(minutes=1, seconds=2), scan=True)

    assert first.observed == 1
    assert first.notifications_started == 0
    assert due.notifications_started == 1
    assert duplicate.notifications_started == 0
    door.post.assert_awaited_once()
    posted_message = door.post.await_args.kwargs["message"]
    assert "repository: iterwheel/voyager-sandbox" in posted_message
    assert "pull_request: 42" in posted_message
    assert "thread_ids: PRRT_state_a" in posted_message
    assert ledger.notifications()[0].state == "pfc_received"


@pytest.mark.asyncio
async def test_state_a_change_before_n_clears_observation(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.side_effect = [
        [_live_thread()],
        [
            _live_thread(
                reply_login="ryosaeba1985",
                reply_created_at="2026-08-30T01:00:00Z",
            )
        ],
    ]
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            notify_after_minutes=1,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=True)
    await reconciler.tick(now=now + timedelta(minutes=1), scan=True)

    assert ledger.observations()[0].status == "cleared"
    assert ledger.notifications() == ()
    assert [event["event"] for event in ledger.events()] == [
        "state_a_observed",
        "state_a_cleared",
    ]


@pytest.mark.asyncio
async def test_new_head_supersedes_old_observation(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    old_head = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=old_head,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    github = AsyncMock()
    github.pull_request.side_effect = [
        {
            "number": 42,
            "state": "open",
            "head": {"sha": old_head},
            "user": {"login": "ryosaeba1985"},
        },
        {
            "number": 42,
            "state": "open",
            "head": {"sha": "c" * 40},
            "user": {"login": "ryosaeba1985"},
        },
    ]
    github.pull_request_review_threads.return_value = [_live_thread()]
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(enabled=True, allowed_repositories=(repository,)),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=True)
    await reconciler.tick(now=now + timedelta(seconds=1), scan=True)

    assert ledger.observations()[0].status == "superseded"
    assert [event["event"] for event in ledger.events()][-1] == "state_a_superseded"


@pytest.mark.asyncio
async def test_due_observation_waits_for_a_successful_current_scan(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.side_effect = [
        [_live_thread()],
        RuntimeError("temporary read failure"),
        [_live_thread()],
    ]
    door = AsyncMock()
    door.post.return_value = DoorAck("c" * 32, 86400)
    door.receipt.return_value = DoorReceipt(state="pending")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            notify_after_minutes=1,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=WakeupLedger(tmp_path / "wakeup" / "state.db"),
        github=github,
        door=door,
    )

    await reconciler.tick(now=now, scan=True)
    await reconciler.tick(now=now + timedelta(minutes=1), scan=True)

    door.post.assert_not_awaited()

    await reconciler.tick(now=now + timedelta(minutes=1, seconds=1), scan=True)

    door.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_graphql_failure_isolated_to_one_scan_target(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    for pull_number in (41, 42):
        clearance_store.append_poll(
            PollRecord(
                ts=now,
                repo=repository,
                pr=pull_number,
                head_sha=head_sha,
                status=Status.BLOCKED,
                threads=[_persisted_thread()],
            )
        )
    github = AsyncMock()
    github.pull_request.side_effect = [
        {
            "number": pull_number,
            "state": "open",
            "head": {"sha": head_sha},
            "user": {"login": "ryosaeba1985"},
        }
        for pull_number in (41, 42)
    ]
    github.pull_request_review_threads.side_effect = [
        GitHubGraphQLError([{"type": "FORBIDDEN", "message": "fixture"}]),
        [_live_thread()],
    ]
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    summary = await reconciler.tick(now=now, scan=True)

    assert summary.observed == 1
    assert ledger.active_observations()[0].key.pull_number == 42


@pytest.mark.asyncio
async def test_repo_removal_clears_due_observation_without_notifying(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    door = AsyncMock()
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            notify_after_minutes=1,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
    )

    await reconciler.tick(now=now, scan=True)
    reconciler.config = AuthorWakeupConfig(enabled=True, allowed_repositories=())
    await reconciler.tick(now=now + timedelta(minutes=1), scan=True)

    door.post.assert_not_awaited()
    assert ledger.observations()[0].status == "cleared"


@pytest.mark.asyncio
async def test_clearance_gate_removal_clears_observation_without_notifying(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    scope = {"allowed": True}
    door = AsyncMock()
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            notify_after_minutes=1,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
        clearance_repository_allowed=lambda _repository: scope["allowed"],
    )

    await reconciler.tick(now=now, scan=True)
    scope["allowed"] = False
    await reconciler.tick(now=now + timedelta(minutes=1), scan=True)

    door.post.assert_not_awaited()
    assert github.pull_request.await_count == 1
    assert ledger.observations()[0].status == "cleared"


@pytest.mark.asyncio
async def test_terminal_scan_checkpoint_skips_history_until_a_new_poll(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")

    def append_poll(at: datetime) -> None:
        clearance_store.append_poll(
            PollRecord(
                ts=at,
                repo=repository,
                pr=42,
                head_sha=head_sha,
                status=Status.BLOCKED,
                threads=[_persisted_thread()],
            )
        )

    append_poll(now)
    github = AsyncMock()
    github.pull_request.side_effect = [
        {
            "number": 42,
            "state": "closed",
            "head": {"sha": head_sha},
            "user": {"login": "ryosaeba1985"},
        },
        {
            "number": 42,
            "state": "open",
            "head": {"sha": head_sha},
            "user": {"login": "ryosaeba1985"},
        },
    ]
    github.pull_request_review_threads.return_value = [_live_thread()]
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=True)
    await reconciler.tick(now=now + timedelta(seconds=30), scan=True)

    assert github.pull_request.await_count == 1

    append_poll(now + timedelta(minutes=1))
    reconciler.nudge(repository, 42)
    await reconciler.tick(now=now + timedelta(minutes=1), scan=True)

    assert github.pull_request.await_count == 2
    assert ledger.observations()[0].status == "active"


@pytest.mark.asyncio
async def test_terminal_checkpoint_distinguishes_semantic_polls_in_same_second(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.READY,
            threads=[_persisted_thread(verdict=Verdict.RESOLVED)],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=True)
    assert github.pull_request.await_count == 0

    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    reconciler.nudge(repository, 42)
    await reconciler.tick(now=now + timedelta(milliseconds=1), scan=True)

    assert github.pull_request.await_count == 1
    assert ledger.observations()[0].status == "active"


@pytest.mark.asyncio
async def test_nudge_preserves_canonical_repository_for_state_store_lookup(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    canonical_repository = "IterWheel/Voyager-Sandbox"
    clearance_store = StateStore(tmp_path / "clearance")

    def append_poll(head_sha: str, at: datetime) -> None:
        clearance_store.append_poll(
            PollRecord(
                ts=at,
                repo=canonical_repository,
                pr=42,
                head_sha=head_sha,
                status=Status.BLOCKED,
                threads=[_persisted_thread()],
            )
        )

    append_poll("b" * 40, now)
    github = AsyncMock()
    github.pull_request.side_effect = [
        {
            "number": 42,
            "state": "open",
            "head": {"sha": head_sha},
            "user": {"login": "ryosaeba1985"},
        }
        for head_sha in ("b" * 40, "c" * 40)
    ]
    github.pull_request_review_threads.return_value = [_live_thread()]
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(canonical_repository.lower(),),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=True)
    append_poll("c" * 40, now + timedelta(seconds=1))
    reconciler.nudge(canonical_repository, 42)
    await reconciler.tick(now=now + timedelta(seconds=1), scan=True)

    observations = ledger.observations()
    assert [item.key.head_sha for item in observations] == ["b" * 40, "c" * 40]
    assert observations[0].status == "superseded"
    assert observations[1].status == "active"
    assert observations[1].key.repository == canonical_repository


@pytest.mark.asyncio
async def test_reversible_live_ineligibility_is_rechecked_without_a_new_poll(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now,
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.side_effect = [
        [_live_thread(resolved=True)],
        [_live_thread()],
    ]
    ledger = WakeupLedger(tmp_path / "wakeup" / "state.db")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=True)
    await reconciler.tick(now=now + timedelta(seconds=30), scan=True)

    assert github.pull_request.await_count == 2
    assert ledger.observations()[0].status == "active"


@pytest.mark.asyncio
async def test_reconciler_reposts_same_id_after_ambiguous_window(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_state_a",),
        state="pfc_received",
        created_at=now - timedelta(minutes=2),
        transport_send_id="c" * 32,
        attempt_number=1,
        first_posted_at=now - timedelta(seconds=90),
        retained_until=now + timedelta(hours=23),
    )
    ledger.save_notification(notification, event="pfc_received", at=now)
    door = AsyncMock()
    door.receipt.return_value = DoorReceipt(state="pending")
    door.post.return_value = DoorAck("c" * 32, 86400)
    clearance_store, github = _delivery_dependencies(tmp_path, notification, now)
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(enabled=True, allowed_repositories=(notification.repository,)),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
        send_id_factory=lambda: "d" * 32,
    )

    await reconciler.tick(now=now, scan=False)

    door.post.assert_awaited_once()
    assert door.post.await_args.kwargs["transport_send_id"] == "c" * 32
    current = ledger.notifications()[0]
    assert current.same_id_reposts == 1
    assert current.attempt_number == 1


@pytest.mark.asyncio
async def test_reconciler_uses_new_id_after_terminal_failure(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_one",),
        state="pfc_received",
        created_at=now - timedelta(minutes=2),
        transport_send_id="c" * 32,
        attempt_number=1,
        first_posted_at=now - timedelta(seconds=10),
        retained_until=now + timedelta(hours=23),
    )
    ledger.save_notification(notification, event="pfc_received", at=now)
    door = AsyncMock()
    door.receipt.return_value = DoorReceipt(state="routing_failed", reason="no author route")
    door.post.return_value = DoorAck("d" * 32, 86400)
    clearance_store, github = _delivery_dependencies(tmp_path, notification, now)
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(enabled=True, allowed_repositories=(notification.repository,)),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
        send_id_factory=lambda: "d" * 32,
    )

    await reconciler.tick(now=now, scan=False)

    door.post.assert_not_awaited()
    assert ledger.notifications()[0].state == "notify_attempt_failed"

    await reconciler.tick(now=now + timedelta(seconds=1), scan=False)
    door.post.assert_not_awaited()

    await reconciler.tick(now=now + timedelta(seconds=2), scan=False)

    door.post.assert_awaited_once()
    assert door.post.await_args.kwargs["transport_send_id"] == "d" * 32
    current = ledger.notifications()[0]
    assert current.attempt_number == 2
    assert current.transport_send_id == "d" * 32


@pytest.mark.asyncio
async def test_post_intent_is_durable_before_door_side_effect(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_one",),
        state="notify_intent",
        created_at=now,
    )
    key = ObservationKey(
        notification.repository,
        notification.pull_number,
        notification.head_sha,
        notification.thread_ids[0],
    )
    ledger.observe(key, now - timedelta(seconds=1))
    ledger.save_notification(notification, event="notify_intent", at=now)
    durable_states: list[NotificationState] = []
    durable_observations: list[ObservationState] = []

    async def post(**_kwargs: object) -> DoorAck:
        durable_states.extend(ledger.notifications())
        durable_observations.extend(ledger.observations())
        return DoorAck("c" * 32, 86400)

    door = AsyncMock()
    door.post.side_effect = post
    door.receipt.return_value = DoorReceipt(state="pending")
    clearance_store, github = _delivery_dependencies(tmp_path, notification, now)
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(notification.repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
        send_id_factory=lambda: "c" * 32,
    )

    await reconciler.tick(now=now, scan=False)

    assert durable_states[0].state == "notify_attempt_intent"
    assert durable_states[0].transport_send_id == "c" * 32
    assert durable_states[0].first_posted_at == now
    assert durable_observations[0].status == "notified"
    assert durable_observations[0].notification_id == notification.notification_id


@pytest.mark.asyncio
async def test_ambiguous_initial_post_reuses_same_transport_id(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_one",),
        state="notify_intent",
        created_at=now,
    )
    ledger.save_notification(notification, event="notify_intent", at=now)
    door = AsyncMock()
    door.post.side_effect = [
        httpx.ReadTimeout("lost response"),
        DoorAck("c" * 32, 86400),
    ]
    door.receipt.return_value = DoorReceipt(state="pending")
    clearance_store, github = _delivery_dependencies(tmp_path, notification, now)
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(notification.repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
        send_id_factory=lambda: "c" * 32,
    )

    await reconciler.tick(now=now, scan=False)
    assert ledger.notifications()[0].state == "notify_attempt_uncertain"

    await reconciler.tick(now=now + timedelta(seconds=90), scan=False)

    assert [call.kwargs["transport_send_id"] for call in door.post.await_args_list] == [
        "c" * 32,
        "c" * 32,
    ]
    assert ledger.notifications()[0].same_id_reposts == 1

    await reconciler.tick(now=now + timedelta(seconds=91), scan=False)

    assert door.post.await_count == 2


@pytest.mark.asyncio
async def test_restart_recovers_persisted_attempt_before_reposting(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    path = tmp_path / "state.db"
    ledger = WakeupLedger(path)
    notification = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_one",),
        state="notify_attempt_intent",
        created_at=now - timedelta(seconds=1),
        transport_send_id="c" * 32,
        attempt_number=1,
        first_posted_at=now - timedelta(seconds=1),
        retained_until=now + timedelta(hours=23),
    )
    ledger.save_notification(notification, event="notify_attempt_intent", at=now)
    door = AsyncMock()
    door.receipt.return_value = DoorReceipt(
        state="author_delivered",
        recipient_citizen="voyager",
    )
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = []
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(notification.repository,),
        ),
        clearance_store=StateStore(tmp_path / "clearance"),
        ledger=WakeupLedger(path),
        github=github,
        door=door,
    )

    await reconciler.tick(now=now, scan=False)

    door.post.assert_not_awaited()
    assert WakeupLedger(path).notifications()[0].state == "notified"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["notify_intent", "notify_attempt_failed"])
async def test_recovery_rechecks_current_repository_scope(
    tmp_path: Path,
    state: str,
) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_one",),
        state=state,
        created_at=now - timedelta(seconds=2),
        attempt_number=1 if state == "notify_attempt_failed" else 0,
        next_delivery_attempt_at=(
            now - timedelta(seconds=1) if state == "notify_attempt_failed" else None
        ),
    )
    ledger.save_notification(notification, event=state, at=now)
    door = AsyncMock()
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(enabled=True, allowed_repositories=()),
        clearance_store=StateStore(tmp_path / "clearance"),
        ledger=ledger,
        github=AsyncMock(),
        door=door,
    )

    await reconciler.tick(now=now, scan=False)

    door.post.assert_not_awaited()
    assert ledger.notifications()[0].state == "notify_scope_revoked"


@pytest.mark.asyncio
async def test_tick_does_not_load_terminal_notification_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    terminal = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_one",),
        state="fallback_finished",
        created_at=now,
    )
    ledger.save_notification(terminal, event=terminal.state, at=now)
    monkeypatch.setattr(
        ledger,
        "notifications",
        lambda: pytest.fail("tick must not load terminal notification history"),
    )
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(terminal.repository,),
        ),
        clearance_store=StateStore(tmp_path / "clearance"),
        ledger=ledger,
        github=AsyncMock(),
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=False)


@pytest.mark.asyncio
async def test_recovered_notification_revalidates_live_state_before_post(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now - timedelta(minutes=1),
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository=repository,
        pull_number=42,
        head_sha=head_sha,
        thread_ids=("PRRT_state_a",),
        state="notify_intent",
        created_at=now - timedelta(seconds=1),
    )
    ledger.save_notification(notification, event="notify_intent", at=now)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "closed",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    door = AsyncMock()
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
    )

    await reconciler.tick(now=now, scan=False)

    door.post.assert_not_awaited()
    current = ledger.notifications()[0]
    assert current.state == "notify_stale"
    assert current.terminal_reason == "pull_not_open"


@pytest.mark.asyncio
async def test_partial_stale_batch_requeues_eligible_survivors(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    repository = "iterwheel/voyager-sandbox"
    head_sha = "b" * 40
    thread_ids = ("PRRT_one", "PRRT_two")
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now - timedelta(minutes=1),
            repo=repository,
            pr=42,
            head_sha=head_sha,
            status=Status.BLOCKED,
            threads=[_persisted_thread(id=thread_id) for thread_id in thread_ids],
        )
    )
    ledger = WakeupLedger(tmp_path / "state.db")
    keys = [ObservationKey(repository, 42, head_sha, thread_id) for thread_id in thread_ids]
    for key in keys:
        ledger.observe(key, now - timedelta(minutes=2))
    notification = NotificationState(
        notification_id="a" * 32,
        repository=repository,
        pull_number=42,
        head_sha=head_sha,
        thread_ids=thread_ids,
        state="notify_intent",
        created_at=now - timedelta(seconds=1),
    )
    ledger.save_notification(notification, event="notify_intent", at=now)
    ledger.assign_notification(keys, notification.notification_id)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": head_sha},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [
        _live_thread(thread_id="PRRT_one"),
        _live_thread(thread_id="PRRT_two", resolved=True),
    ]
    door = AsyncMock()
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=door,
    )

    await reconciler.tick(now=now, scan=False)

    door.post.assert_not_awaited()
    assert ledger.notifications()[0].state == "notify_stale"
    observations = {item.key.thread_id: item for item in ledger.observations()}
    assert observations["PRRT_one"].status == "active"
    assert observations["PRRT_one"].first_seen == now
    assert observations["PRRT_one"].notification_id is None
    assert observations["PRRT_two"].status == "cleared"
    reactivated = ledger.observe(keys[1], now + timedelta(seconds=1))
    assert reactivated.status == "active"
    assert reactivated.first_seen == now + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_reconciler_does_not_repost_inside_retention_margin(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 0, 2, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository="iterwheel/voyager-sandbox",
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_one",),
        state="pfc_received",
        created_at=now - timedelta(minutes=2),
        transport_send_id="c" * 32,
        attempt_number=1,
        first_posted_at=now - timedelta(seconds=90),
        retained_until=now + timedelta(seconds=299),
    )
    ledger.save_notification(notification, event="pfc_received", at=now)
    door = AsyncMock()
    door.receipt.return_value = DoorReceipt(state="pending")
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(enabled=True, allowed_repositories=(notification.repository,)),
        clearance_store=StateStore(tmp_path / "clearance"),
        ledger=ledger,
        github=AsyncMock(),
        door=door,
    )

    await reconciler.tick(now=now, scan=False)

    door.post.assert_not_awaited()
    assert ledger.notifications()[0].state == "notify_delivery_unknown"


def _notified_reconciler(
    tmp_path: Path,
    *,
    now: datetime,
    github: AsyncMock,
    auto_review_fix: bool = False,
    review_fix: AsyncMock | None = None,
) -> tuple[AuthorWakeupReconciler, WakeupLedger, NotificationState]:
    repository = "iterwheel/voyager-sandbox"
    ledger = WakeupLedger(tmp_path / "state.db")
    notification = NotificationState(
        notification_id="a" * 32,
        repository=repository,
        pull_number=42,
        head_sha="b" * 40,
        thread_ids=("PRRT_state_a",),
        state="notified",
        created_at=now - timedelta(minutes=21),
        author_delivered_at=now - timedelta(minutes=20),
        claim_deadline=now,
        recipient_citizen="voyager",
    )
    ledger.save_notification(notification, event="notified", at=now - timedelta(minutes=20))
    clearance_store = StateStore(tmp_path / "clearance")
    clearance_store.append_poll(
        PollRecord(
            ts=now - timedelta(minutes=21),
            repo=repository,
            pr=42,
            head_sha="b" * 40,
            status=Status.BLOCKED,
            threads=[_persisted_thread()],
        )
    )
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            auto_review_fix=auto_review_fix,
            allowed_repositories=(repository,),
        ),
        clearance_store=clearance_store,
        ledger=ledger,
        github=github,
        door=AsyncMock(),
        review_fix=review_fix,
    )
    return reconciler, ledger, notification


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pull_overrides", "thread", "claim_class"),
    [
        ({"head": {"sha": "c" * 40}}, _live_thread(), "head_superseded"),
        ({"state": "closed"}, _live_thread(), "pull_closed"),
        ({}, _live_thread(resolved=True), "thread_resolved"),
        ({}, _live_thread(reply_login="ryosaeba1985"), "author_reply"),
    ],
)
async def test_claim_evidence_cancels_fallback(
    tmp_path: Path,
    pull_overrides: dict,
    thread: dict,
    claim_class: str,
) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    pull = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    pull.update(pull_overrides)
    github.pull_request.return_value = pull
    github.pull_request_review_threads.return_value = [thread]
    review_fix = AsyncMock()
    reconciler, ledger, _ = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )

    await reconciler.tick(now=now, scan=False)

    current = ledger.notifications()[0]
    assert current.state == "claimed"
    assert current.claim_class == claim_class
    review_fix.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_delivery_author_reply_refuses_fallback(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [
        _live_thread(
            reply_login="ryosaeba1985",
            reply_created_at="2026-08-30T00:30:00Z",
        )
    ]
    review_fix = AsyncMock()
    reconciler, ledger, _ = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )

    await reconciler.tick(now=now, scan=False)

    review_fix.assert_not_awaited()
    current = ledger.notifications()[0]
    assert current.state == "fallback_refused"
    assert current.fallback_status == "author_reply_present"


@pytest.mark.asyncio
async def test_maintainer_reply_does_not_claim_before_deadline(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread(reply_login="maintainer")]
    review_fix = AsyncMock()
    reconciler, ledger, notification = _notified_reconciler(
        tmp_path,
        now=now + timedelta(seconds=1),
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )

    await reconciler.tick(now=notification.claim_deadline - timedelta(seconds=1), scan=False)

    assert ledger.notifications()[0].state == "notified"
    review_fix.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_reads_use_scan_cadence_before_deadline(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread(reply_login="ryosaeba1985")]
    reconciler, ledger, notification = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=AsyncMock(),
    )
    ledger.save_notification(
        notification.with_updates(claim_deadline=now + timedelta(minutes=20)),
        event="notified",
        at=now,
    )

    await reconciler.tick(now=now + timedelta(seconds=2), scan=False)

    github.pull_request.assert_not_awaited()

    await reconciler.tick(now=now + timedelta(minutes=1), scan=True)

    assert github.pull_request.await_count == 2
    assert ledger.notifications()[0].state == "claimed"


@pytest.mark.asyncio
async def test_disabled_auto_review_fix_records_terminal_refusal(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    review_fix = AsyncMock()
    reconciler, ledger, _ = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=False,
        review_fix=review_fix,
    )

    await reconciler.tick(now=now, scan=False)

    assert ledger.notifications()[0].state == "fallback_refused"
    review_fix.assert_not_awaited()
    assert [event["event"] for event in ledger.events()][-2:] == [
        "fallback_intent",
        "fallback_refused",
    ]


@pytest.mark.asyncio
async def test_missing_pr_author_metadata_refuses_fallback(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    review_fix = AsyncMock()
    reconciler, ledger, _ = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )

    await reconciler.tick(now=now, scan=False)

    assert ledger.notifications()[0].fallback_status == "missing_pr_author_login"
    review_fix.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_allowlist_matches_canonical_repository_case(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    review_fix = AsyncMock(return_value={"status": "review_fix_succeeded"})
    reconciler, ledger, notification = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )
    canonical = notification.with_updates(repository="IterWheel/Voyager-Sandbox")
    ledger.save_notification(canonical, event="notified", at=now)

    await reconciler.tick(now=now, scan=False)

    review_fix.assert_awaited_once_with(
        repository="IterWheel/Voyager-Sandbox",
        pull_number=42,
        expected_head_sha=canonical.head_sha,
        finding_ids=canonical.thread_ids,
        notification_id=canonical.notification_id,
    )
    assert ledger.notifications()[0].state == "fallback_finished"


@pytest.mark.asyncio
async def test_claim_graphql_failure_does_not_starve_later_notification(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    ledger = WakeupLedger(tmp_path / "state.db")
    for index, pull_number in enumerate((41, 42)):
        notification = NotificationState(
            notification_id=("a" if index == 0 else "b") * 32,
            repository="iterwheel/voyager-sandbox",
            pull_number=pull_number,
            head_sha="c" * 40,
            thread_ids=("PRRT_state_a",),
            state="notified",
            created_at=now - timedelta(minutes=2),
            author_delivered_at=now - timedelta(minutes=1),
            claim_deadline=now,
            recipient_citizen="voyager",
        )
        ledger.save_notification(notification, event="notified", at=now)
    github = AsyncMock()
    github.pull_request.side_effect = [
        {
            "number": pull_number,
            "state": "open",
            "head": {"sha": "c" * 40},
            "user": {"login": "ryosaeba1985"},
        }
        for pull_number in (41, 42)
    ]
    github.pull_request_review_threads.side_effect = [
        GitHubGraphQLError([{"type": "FORBIDDEN", "message": "fixture"}]),
        [
            _live_thread(
                reply_login="ryosaeba1985",
                reply_created_at="2026-08-30T01:00:00Z",
            )
        ],
    ]
    reconciler = AuthorWakeupReconciler(
        config=AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=("iterwheel/voyager-sandbox",),
        ),
        clearance_store=StateStore(tmp_path / "clearance"),
        ledger=ledger,
        github=github,
        door=AsyncMock(),
    )

    await reconciler.tick(now=now, scan=False)

    current = ledger.notifications()
    assert current[0].state == "notified"
    assert current[1].state == "claimed"


@pytest.mark.asyncio
async def test_due_unclaimed_notification_invokes_exact_fallback_once(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    review_fix = AsyncMock(return_value={"status": "review_fix_succeeded"})
    reconciler, ledger, notification = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )

    await reconciler.tick(now=now, scan=False)
    await reconciler.tick(now=now + timedelta(seconds=1), scan=False)

    review_fix.assert_awaited_once_with(
        repository=notification.repository,
        pull_number=notification.pull_number,
        expected_head_sha=notification.head_sha,
        finding_ids=notification.thread_ids,
        notification_id=notification.notification_id,
    )
    assert ledger.notifications()[0].state == "fallback_finished"
    assert [event["event"] for event in ledger.events()][-3:] == [
        "fallback_intent",
        "fallback_started",
        "fallback_finished",
    ]


@pytest.mark.asyncio
async def test_restart_resumes_fallback_intent_once(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    github.pull_request.return_value = {
        "number": 42,
        "state": "open",
        "head": {"sha": "b" * 40},
        "user": {"login": "ryosaeba1985"},
    }
    github.pull_request_review_threads.return_value = [_live_thread()]
    review_fix = AsyncMock(return_value={"status": "review_fix_succeeded"})
    reconciler, ledger, notification = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )
    ledger.save_notification(
        notification.with_updates(state="fallback_intent"),
        event="fallback_intent",
        at=now,
    )

    await reconciler.tick(now=now, scan=False)

    review_fix.assert_awaited_once()
    assert ledger.notifications()[0].state == "fallback_finished"


@pytest.mark.asyncio
async def test_restart_fails_closed_for_ambiguous_fallback_started(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    github = AsyncMock()
    review_fix = AsyncMock()
    reconciler, ledger, notification = _notified_reconciler(
        tmp_path,
        now=now,
        github=github,
        auto_review_fix=True,
        review_fix=review_fix,
    )
    ledger.save_notification(
        notification.with_updates(state="fallback_started"),
        event="fallback_started",
        at=now,
    )

    await reconciler.tick(now=now, scan=False)

    review_fix.assert_not_awaited()
    current = ledger.notifications()[0]
    assert current.state == "fallback_refused"
    assert current.fallback_status == "restart_after_fallback_started"
