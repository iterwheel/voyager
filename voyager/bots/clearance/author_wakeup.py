"""Durable Clearance author-wakeup reconciliation (VOY-1843 / CHG-1844)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from voyager.bots.clearance.classify import is_codex_thread, latest_author_reply
from voyager.bots.clearance.constants import CLEARANCE_AGENT_SLUG
from voyager.bots.clearance.models import PollRecord, Thread, Verdict
from voyager.bots.clearance.state import StateStore
from voyager.core.config import AuthorWakeupConfig

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ObservationKey:
    repository: str
    pull_number: int
    head_sha: str
    thread_id: str


@dataclass(frozen=True)
class ObservationState:
    key: ObservationKey
    first_seen: datetime
    status: str = "active"
    notification_id: str | None = None


@dataclass(frozen=True)
class NotificationState:
    notification_id: str
    repository: str
    pull_number: int
    head_sha: str
    thread_ids: tuple[str, ...]
    state: str
    created_at: datetime
    transport_send_id: str | None = None
    attempt_number: int = 0
    same_id_reposts: int = 0
    first_posted_at: datetime | None = None
    receipt_window_started_at: datetime | None = None
    retained_until: datetime | None = None
    author_delivered_at: datetime | None = None
    claim_deadline: datetime | None = None
    next_delivery_attempt_at: datetime | None = None
    recipient_citizen: str | None = None
    claim_class: str | None = None
    fallback_status: str | None = None
    terminal_reason: str | None = None

    def with_updates(self, **changes: Any) -> NotificationState:
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["thread_ids"] = list(self.thread_ids)
        for key, value in tuple(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationState:
        values = dict(data)
        values["thread_ids"] = tuple(values.get("thread_ids") or ())
        for key in (
            "created_at",
            "first_posted_at",
            "receipt_window_started_at",
            "retained_until",
            "author_delivered_at",
            "claim_deadline",
            "next_delivery_attempt_at",
        ):
            if values.get(key):
                values[key] = datetime.fromisoformat(str(values[key]))
        return cls(**values)


class WakeupLedger:
    """SQLite current state plus an append-only audit event table."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    repository TEXT NOT NULL,
                    pull_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notification_id TEXT,
                    PRIMARY KEY (repository, pull_number, head_sha, thread_id)
                );
                CREATE INDEX IF NOT EXISTS idx_observations_active_pull
                    ON observations (status, repository, pull_number);
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS terminal_scan_checkpoints (
                    repository TEXT NOT NULL,
                    pull_number INTEGER NOT NULL,
                    poll_identity TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (repository, pull_number)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    notification_id TEXT,
                    repository TEXT NOT NULL,
                    pull_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def observe(self, key: ObservationKey, at: datetime) -> ObservationState:
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO observations
                    (repository, pull_number, head_sha, thread_id, first_seen, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (
                    key.repository,
                    key.pull_number,
                    key.head_sha,
                    key.thread_id,
                    at.isoformat(),
                ),
            )
            if cursor.rowcount:
                self._append_event(
                    db,
                    at=at,
                    event="state_a_observed",
                    repository=key.repository,
                    pull_number=key.pull_number,
                    head_sha=key.head_sha,
                    payload={"thread_id": key.thread_id},
                )
            else:
                reactivated = db.execute(
                    """
                    UPDATE observations
                    SET first_seen=?, status='active', notification_id=NULL
                    WHERE repository=? AND pull_number=? AND head_sha=? AND thread_id=?
                      AND status='cleared'
                    """,
                    (
                        at.isoformat(),
                        key.repository,
                        key.pull_number,
                        key.head_sha,
                        key.thread_id,
                    ),
                )
                if reactivated.rowcount:
                    self._append_event(
                        db,
                        at=at,
                        event="state_a_observed",
                        repository=key.repository,
                        pull_number=key.pull_number,
                        head_sha=key.head_sha,
                        payload={"thread_id": key.thread_id, "reactivated": True},
                    )
            row = db.execute(
                """
                SELECT first_seen, status, notification_id FROM observations
                WHERE repository=? AND pull_number=? AND head_sha=? AND thread_id=?
                """,
                (key.repository, key.pull_number, key.head_sha, key.thread_id),
            ).fetchone()
        assert row is not None
        return ObservationState(
            key=key,
            first_seen=datetime.fromisoformat(str(row["first_seen"])),
            status=str(row["status"]),
            notification_id=row["notification_id"],
        )

    def save_notification(
        self,
        notification: NotificationState,
        *,
        event: str,
        at: datetime,
    ) -> None:
        data = notification.to_dict()
        encoded = json.dumps(data, separators=(",", ":"), sort_keys=True)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO notifications (notification_id, data) VALUES (?, ?)
                ON CONFLICT(notification_id) DO UPDATE SET data=excluded.data
                """,
                (notification.notification_id, encoded),
            )
            self._append_event(
                db,
                at=at,
                event=event,
                notification_id=notification.notification_id,
                repository=notification.repository,
                pull_number=notification.pull_number,
                head_sha=notification.head_sha,
                payload=data,
            )

    def observations(self) -> tuple[ObservationState, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT repository, pull_number, head_sha, thread_id,
                       first_seen, status, notification_id
                FROM observations ORDER BY repository, pull_number, head_sha, thread_id
                """
            ).fetchall()
        return tuple(
            ObservationState(
                key=ObservationKey(
                    repository=str(row["repository"]),
                    pull_number=int(row["pull_number"]),
                    head_sha=str(row["head_sha"]),
                    thread_id=str(row["thread_id"]),
                ),
                first_seen=datetime.fromisoformat(str(row["first_seen"])),
                status=str(row["status"]),
                notification_id=row["notification_id"],
            )
            for row in rows
        )

    def active_observations(
        self,
        repository: str | None = None,
        pull_number: int | None = None,
    ) -> tuple[ObservationState, ...]:
        query = (
            """
            SELECT repository, pull_number, head_sha, thread_id,
                   first_seen, status, notification_id
            FROM observations
            WHERE repository=? AND pull_number=? AND status='active'
            ORDER BY head_sha, thread_id
            """
            if repository is not None and pull_number is not None
            else """
            SELECT repository, pull_number, head_sha, thread_id,
                   first_seen, status, notification_id
            FROM observations
            WHERE status='active'
            ORDER BY repository, pull_number, head_sha, thread_id
            """
        )
        params = (
            (repository, pull_number) if repository is not None and pull_number is not None else ()
        )
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return tuple(
            ObservationState(
                key=ObservationKey(
                    repository=str(row["repository"]),
                    pull_number=int(row["pull_number"]),
                    head_sha=str(row["head_sha"]),
                    thread_id=str(row["thread_id"]),
                ),
                first_seen=datetime.fromisoformat(str(row["first_seen"])),
                status=str(row["status"]),
                notification_id=row["notification_id"],
            )
            for row in rows
        )

    def assign_notification(
        self,
        keys: Sequence[ObservationKey],
        notification_id: str,
    ) -> None:
        with self._connect() as db:
            for key in keys:
                db.execute(
                    """
                    UPDATE observations SET status='notified', notification_id=?
                    WHERE repository=? AND pull_number=? AND head_sha=? AND thread_id=?
                      AND status='active'
                    """,
                    (
                        notification_id,
                        key.repository,
                        key.pull_number,
                        key.head_sha,
                        key.thread_id,
                    ),
                )

    def create_notification_intent(
        self,
        notification: NotificationState,
        keys: Sequence[ObservationKey],
        *,
        at: datetime,
    ) -> None:
        data = notification.to_dict()
        encoded = json.dumps(data, separators=(",", ":"), sort_keys=True)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO notifications (notification_id, data) VALUES (?, ?)
                ON CONFLICT(notification_id) DO UPDATE SET data=excluded.data
                """,
                (notification.notification_id, encoded),
            )
            self._append_event(
                db,
                at=at,
                event="notify_intent",
                notification_id=notification.notification_id,
                repository=notification.repository,
                pull_number=notification.pull_number,
                head_sha=notification.head_sha,
                payload=data,
            )
            for key in keys:
                db.execute(
                    """
                    UPDATE observations SET status='notified', notification_id=?
                    WHERE repository=? AND pull_number=? AND head_sha=? AND thread_id=?
                      AND status='active'
                    """,
                    (
                        notification.notification_id,
                        key.repository,
                        key.pull_number,
                        key.head_sha,
                        key.thread_id,
                    ),
                )

    def reconcile_active_observations(
        self,
        *,
        repository: str,
        pull_number: int,
        current_head_sha: str,
        eligible_keys: set[ObservationKey],
        at: datetime,
    ) -> None:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT repository, pull_number, head_sha, thread_id
                FROM observations
                WHERE repository=? AND pull_number=? AND status='active'
                """,
                (repository, pull_number),
            ).fetchall()
            for row in rows:
                key = ObservationKey(
                    repository=str(row["repository"]),
                    pull_number=int(row["pull_number"]),
                    head_sha=str(row["head_sha"]),
                    thread_id=str(row["thread_id"]),
                )
                if key.head_sha != current_head_sha:
                    status = "superseded"
                    event = "state_a_superseded"
                elif key not in eligible_keys:
                    status = "cleared"
                    event = "state_a_cleared"
                else:
                    continue
                db.execute(
                    """
                    UPDATE observations SET status=?
                    WHERE repository=? AND pull_number=? AND head_sha=? AND thread_id=?
                    """,
                    (
                        status,
                        key.repository,
                        key.pull_number,
                        key.head_sha,
                        key.thread_id,
                    ),
                )
                self._append_event(
                    db,
                    at=at,
                    event=event,
                    repository=key.repository,
                    pull_number=key.pull_number,
                    head_sha=key.head_sha,
                    payload={"thread_id": key.thread_id},
                )

    def notifications(self) -> tuple[NotificationState, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT data FROM notifications ORDER BY notification_id").fetchall()
        return tuple(NotificationState.from_dict(json.loads(str(row["data"]))) for row in rows)

    def terminal_scan_matches(
        self,
        *,
        repository: str,
        pull_number: int,
        poll_identity: str,
        head_sha: str,
    ) -> bool:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT 1 FROM terminal_scan_checkpoints
                WHERE repository=? AND pull_number=? AND poll_identity=? AND head_sha=?
                """,
                (repository, pull_number, poll_identity, head_sha),
            ).fetchone()
        return row is not None

    def mark_terminal_scan(
        self,
        *,
        repository: str,
        pull_number: int,
        poll_identity: str,
        head_sha: str,
        reason: str,
        at: datetime,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO terminal_scan_checkpoints
                    (repository, pull_number, poll_identity, head_sha, reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, pull_number) DO UPDATE SET
                    poll_identity=excluded.poll_identity,
                    head_sha=excluded.head_sha,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (
                    repository,
                    pull_number,
                    poll_identity,
                    head_sha,
                    reason,
                    at.isoformat(),
                ),
            )

    def events(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT at, event, notification_id, repository, pull_number, head_sha, payload
                FROM events ORDER BY id
                """
            ).fetchall()
        return tuple(
            {
                "at": str(row["at"]),
                "event": str(row["event"]),
                "notification_id": row["notification_id"],
                "repository": str(row["repository"]),
                "pull_number": int(row["pull_number"]),
                "head_sha": str(row["head_sha"]),
                "payload": json.loads(str(row["payload"])),
            }
            for row in rows
        )

    @staticmethod
    def _append_event(
        db: sqlite3.Connection,
        *,
        at: datetime,
        event: str,
        repository: str,
        pull_number: int,
        head_sha: str,
        payload: dict[str, Any],
        notification_id: str | None = None,
    ) -> None:
        db.execute(
            """
            INSERT INTO events
                (at, event, notification_id, repository, pull_number, head_sha, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                at.isoformat(),
                event,
                notification_id,
                repository,
                pull_number,
                head_sha,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
            ),
        )


class PfcDoorProtocolError(RuntimeError):
    """The loopback PFC door violated the VOY-1843 transport contract."""


class PfcDoorRetentionError(PfcDoorProtocolError):
    """The PFC door did not prove the required send-ID retention window."""


@dataclass(frozen=True)
class DoorAck:
    transport_send_id: str
    retention_seconds: int


@dataclass(frozen=True)
class DoorReceipt:
    state: str
    recipient_citizen: str | None = None
    reason: str | None = None


class PfcDoorClient:
    """Narrow client for the single Voyager→PFC notification edge."""

    def __init__(
        self,
        door_url: str,
        *,
        required_retention_seconds: int,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(door_url)
        self.door_url = door_url
        self.receipt_base_url = urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/agent-send-result", "", "")
        )
        self.required_retention_seconds = required_retention_seconds
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(timeout=20)

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def post(self, *, message: str, transport_send_id: str) -> DoorAck:
        response = await self.http.post(
            self.door_url,
            json={"citizen": "pfc", "message": message, "send_id": transport_send_id},
        )
        response.raise_for_status()
        payload = _response_object(response)
        if payload.get("ok") is not True or payload.get("queued") is not True:
            raise PfcDoorProtocolError("PFC door did not acknowledge ok=true, queued=true")
        if payload.get("send_id") != transport_send_id:
            raise PfcDoorProtocolError("PFC door returned a mismatched send_id")
        retention = payload.get("idempotency_retention_seconds")
        if (
            isinstance(retention, bool)
            or not isinstance(retention, int)
            or retention < self.required_retention_seconds
        ):
            raise PfcDoorRetentionError(
                "PFC door idempotency_retention_seconds is missing or too short"
            )
        return DoorAck(transport_send_id=transport_send_id, retention_seconds=retention)

    async def receipt(
        self,
        *,
        transport_send_id: str,
        notification_id: str,
    ) -> DoorReceipt:
        response = await self.http.get(f"{self.receipt_base_url}/{transport_send_id}")
        response.raise_for_status()
        payload = _response_object(response)
        if payload.get("found") is not True:
            return DoorReceipt(state="pending")
        outcome = payload.get("outcome")
        if not isinstance(outcome, dict):
            raise PfcDoorProtocolError("PFC receipt outcome must be an object")
        if not isinstance(outcome.get("ok"), bool):
            return DoorReceipt(state="pending")
        stage = str(outcome.get("stage") or "pending")
        if stage == "pending":
            return DoorReceipt(state=stage)
        if stage == "pfc_received" and outcome.get("ok") is True:
            return DoorReceipt(state=stage)
        if stage == "author_delivered":
            recipient = outcome.get("recipient_citizen")
            if (
                outcome.get("ok") is not True
                or outcome.get("notification_id") != notification_id
                or not isinstance(recipient, str)
                or not recipient.strip()
            ):
                raise PfcDoorProtocolError("PFC author_delivered receipt is malformed")
            return DoorReceipt(state=stage, recipient_citizen=recipient.strip())
        if stage == "routing_failed" and outcome.get("ok") is False:
            return DoorReceipt(state=stage, reason=str(outcome.get("reason") or "routing_failed"))
        raise PfcDoorProtocolError(f"PFC receipt has unknown terminal stage {stage!r}")


def _response_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise PfcDoorProtocolError("PFC response must contain a JSON object") from exc
    if not isinstance(payload, dict):
        raise PfcDoorProtocolError("PFC response must contain a JSON object")
    return payload


@dataclass(frozen=True)
class TickSummary:
    observed: int = 0
    notifications_started: int = 0
    receipts_checked: int = 0


class AuthorWakeupReconciler:
    """Reconcile persisted Clearance OPEN state with live PR/thread state."""

    def __init__(
        self,
        *,
        config: AuthorWakeupConfig,
        clearance_store: StateStore,
        ledger: WakeupLedger,
        github: Any,
        door: Any,
        review_fix: Any = None,
        send_id_factory: Callable[[], str] | None = None,
        clearance_repository_allowed: Callable[[str], bool] | None = None,
    ) -> None:
        self.config = config
        self.clearance_store = clearance_store
        self.ledger = ledger
        self.github = github
        self.door = door
        self.review_fix = review_fix
        self.send_id_factory = send_id_factory or (lambda: uuid4().hex)
        self.clearance_repository_allowed = clearance_repository_allowed
        self._poll_cache: dict[tuple[str, int], PollRecord] = {}
        self._poll_cache_bootstrapped = False
        self._dirty_scan_targets: set[tuple[str, int]] = set()

    def nudge(self, repository: str, pull_number: int) -> None:
        """Mark one PR's Clearance poll snapshot dirty after webhook writeback."""
        self._dirty_scan_targets.add((repository.lower(), int(pull_number)))

    async def tick(self, *, now: datetime, scan: bool) -> TickSummary:
        observed = notifications_started = receipts_checked = 0
        if scan:
            observed, notifications_started = await self._scan(now)
        for notification in self.ledger.notifications():
            if notification.state == "notify_intent":
                await self._post_notification(notification, now)
        for notification in self.ledger.notifications():
            if notification.state not in {
                "notify_attempt_intent",
                "notify_attempt_uncertain",
                "notify_queued",
                "pfc_received",
            }:
                continue
            await self._check_receipt(notification, now)
            receipts_checked += 1
        for notification in self.ledger.notifications():
            if (
                notification.state == "notify_attempt_failed"
                and notification.next_delivery_attempt_at is not None
                and now >= notification.next_delivery_attempt_at
            ):
                await self._post_notification(notification, now)
        for notification in self.ledger.notifications():
            if notification.state == "fallback_started":
                refused = notification.with_updates(
                    state="fallback_refused",
                    fallback_status="restart_after_fallback_started",
                )
                self.ledger.save_notification(refused, event=refused.state, at=now)
                continue
            if notification.state not in {"notified", "fallback_intent"}:
                continue
            await self._check_claim_or_fallback(notification, now)
        return TickSummary(
            observed=observed,
            notifications_started=notifications_started,
            receipts_checked=receipts_checked,
        )

    async def _scan(self, now: datetime) -> tuple[int, int]:
        latest = self._scan_poll_snapshot()
        observed_count = 0
        revalidated_keys: set[ObservationKey] = set()
        terminal_targets: set[tuple[str, int]] = set()
        for (repository, pull_number), poll in latest.items():
            if not self._repository_in_scope(repository):
                self.ledger.reconcile_active_observations(
                    repository=repository,
                    pull_number=pull_number,
                    current_head_sha=poll.head_sha,
                    eligible_keys=set(),
                    at=now,
                )
                terminal_targets.add((repository, pull_number))
                continue
            poll_identity = _poll_identity(poll)
            if self.ledger.terminal_scan_matches(
                repository=repository,
                pull_number=pull_number,
                poll_identity=poll_identity,
                head_sha=poll.head_sha,
            ):
                terminal_targets.add((repository, pull_number))
                continue
            persisted_candidates = [
                thread
                for thread in poll.threads
                if thread.verdict is Verdict.OPEN and not thread.github_isResolved
            ]
            if not persisted_candidates:
                self.ledger.reconcile_active_observations(
                    repository=repository,
                    pull_number=pull_number,
                    current_head_sha=poll.head_sha,
                    eligible_keys=set(),
                    at=now,
                )
                self.ledger.mark_terminal_scan(
                    repository=repository,
                    pull_number=pull_number,
                    poll_identity=poll_identity,
                    head_sha=poll.head_sha,
                    reason="no_open_persisted_threads",
                    at=now,
                )
                terminal_targets.add((repository, pull_number))
                continue
            try:
                pull = await self.github.pull_request(CLEARANCE_AGENT_SLUG, repository, pull_number)
                live_threads = await self.github.pull_request_review_threads(
                    CLEARANCE_AGENT_SLUG, repository, pull_number
                )
            except (httpx.HTTPError, TimeoutError, RuntimeError):
                continue
            head_sha = str((pull.get("head") or {}).get("sha") or "")
            if str(pull.get("state") or "").lower() != "open" or head_sha != poll.head_sha:
                self.ledger.reconcile_active_observations(
                    repository=repository,
                    pull_number=pull_number,
                    current_head_sha=head_sha,
                    eligible_keys=set(),
                    at=now,
                )
                self.ledger.mark_terminal_scan(
                    repository=repository,
                    pull_number=pull_number,
                    poll_identity=poll_identity,
                    head_sha=poll.head_sha,
                    reason=(
                        "pull_not_open"
                        if str(pull.get("state") or "").lower() != "open"
                        else "head_superseded"
                    ),
                    at=now,
                )
                terminal_targets.add((repository, pull_number))
                continue
            author_login = ((pull.get("user") or {}).get("login")) or None
            live_by_id = {str(thread.get("id") or ""): thread for thread in live_threads or []}
            before = {item.key for item in self.ledger.active_observations(repository, pull_number)}
            eligible_keys: set[ObservationKey] = set()
            for persisted in persisted_candidates:
                live = live_by_id.get(persisted.id)
                if live is None or not is_author_wakeup_eligible(
                    persisted, live, pr_author_login=author_login
                ):
                    continue
                key = ObservationKey(repository, pull_number, head_sha, persisted.id)
                eligible_keys.add(key)
                revalidated_keys.add(key)
                self.ledger.observe(key, now)
                if key not in before:
                    observed_count += 1
            self.ledger.reconcile_active_observations(
                repository=repository,
                pull_number=pull_number,
                current_head_sha=head_sha,
                eligible_keys=eligible_keys,
                at=now,
            )
        for target in terminal_targets:
            self._poll_cache.pop(target, None)
        started = await self._start_due_notifications(now, revalidated_keys)
        return observed_count, started

    def _scan_poll_snapshot(self) -> dict[tuple[str, int], PollRecord]:
        if not self._poll_cache_bootstrapped:
            for poll in self.clearance_store.read_polls():
                self._poll_cache[(poll.repo.lower(), poll.pr)] = poll
            self._poll_cache_bootstrapped = True
            self._dirty_scan_targets.clear()
            return dict(self._poll_cache)
        for repository, pull_number in self._dirty_scan_targets:
            latest_poll = self.clearance_store.latest_poll(repository, pull_number)
            if latest_poll is not None:
                self._poll_cache[(repository, pull_number)] = latest_poll
        self._dirty_scan_targets.clear()
        return dict(self._poll_cache)

    async def _start_due_notifications(
        self,
        now: datetime,
        revalidated_keys: set[ObservationKey],
    ) -> int:
        cutoff = now - timedelta(minutes=self.config.notify_after_minutes)
        due_groups: dict[tuple[str, int, str], list[ObservationState]] = {}
        for observation in self.ledger.active_observations():
            if (
                observation.status != "active"
                or observation.notification_id is not None
                or observation.first_seen > cutoff
                or observation.key not in revalidated_keys
            ):
                continue
            group = (
                observation.key.repository,
                observation.key.pull_number,
                observation.key.head_sha,
            )
            due_groups.setdefault(group, []).append(observation)
        for (repository, pull_number, head_sha), observations in due_groups.items():
            thread_ids = tuple(sorted(item.key.thread_id for item in observations))
            notification_id = _notification_id(repository, pull_number, head_sha, thread_ids)
            notification = NotificationState(
                notification_id=notification_id,
                repository=repository,
                pull_number=pull_number,
                head_sha=head_sha,
                thread_ids=thread_ids,
                state="notify_intent",
                created_at=now,
            )
            self.ledger.create_notification_intent(
                notification,
                [item.key for item in observations],
                at=now,
            )
            await self._post_notification(notification, now, live_revalidated=True)
        return len(due_groups)

    async def _post_notification(
        self,
        notification: NotificationState,
        now: datetime,
        *,
        transport_send_id: str | None = None,
        same_id_repost: bool = False,
        live_revalidated: bool = False,
    ) -> None:
        if not self._repository_in_scope(notification.repository):
            revoked = notification.with_updates(
                state="notify_scope_revoked",
                next_delivery_attempt_at=None,
            )
            self.ledger.save_notification(revoked, event=revoked.state, at=now)
            return
        if not live_revalidated:
            eligible, reason = await self._delivery_revalidation(notification)
            if eligible is None:
                return
            if not eligible:
                stale = notification.with_updates(
                    state="notify_stale",
                    next_delivery_attempt_at=None,
                    terminal_reason=reason,
                )
                self.ledger.save_notification(stale, event=stale.state, at=now)
                return
        self.ledger.assign_notification(
            [
                ObservationKey(
                    notification.repository,
                    notification.pull_number,
                    notification.head_sha,
                    thread_id,
                )
                for thread_id in notification.thread_ids
            ],
            notification.notification_id,
        )
        transport_send_id = transport_send_id or self.send_id_factory()
        message = build_wakeup_message(
            notification_id=notification.notification_id,
            repository=notification.repository,
            pull_number=notification.pull_number,
            head_sha=notification.head_sha,
            thread_ids=notification.thread_ids,
            notify_after_minutes=self.config.notify_after_minutes,
            fallback_after_minutes=self.config.fallback_after_minutes,
        )
        attempt = notification.with_updates(
            state="notify_attempt_intent",
            transport_send_id=transport_send_id,
            attempt_number=(
                notification.attempt_number if same_id_repost else notification.attempt_number + 1
            ),
            same_id_reposts=(notification.same_id_reposts + 1 if same_id_repost else 0),
            first_posted_at=notification.first_posted_at if same_id_repost else now,
            receipt_window_started_at=now,
            retained_until=(
                notification.retained_until
                if same_id_repost
                else now + timedelta(seconds=self.config.required_send_id_retention_seconds)
            ),
            next_delivery_attempt_at=None,
        )
        self.ledger.save_notification(attempt, event=attempt.state, at=now)
        try:
            ack = await self.door.post(
                message=message,
                transport_send_id=transport_send_id,
            )
        except PfcDoorRetentionError:
            unknown = attempt.with_updates(state="notify_delivery_unknown")
            self.ledger.save_notification(unknown, event=unknown.state, at=now)
            _log.error(
                "Clearance author-wakeup delivery is unknown: PFC retention contract failed; "
                "automatic fallback remains disarmed"
            )
            return
        except (httpx.HTTPError, TimeoutError, PfcDoorProtocolError):
            uncertain = attempt.with_updates(state="notify_attempt_uncertain")
            self.ledger.save_notification(uncertain, event=uncertain.state, at=now)
            return
        queued = attempt.with_updates(
            state="notify_queued",
            transport_send_id=ack.transport_send_id,
            retained_until=(
                notification.retained_until
                if same_id_repost
                else (attempt.first_posted_at or now) + timedelta(seconds=ack.retention_seconds)
            ),
        )
        event = "notify_same_id_repost" if same_id_repost else queued.state
        self.ledger.save_notification(queued, event=event, at=now)

    async def _delivery_revalidation(
        self,
        notification: NotificationState,
    ) -> tuple[bool | None, str]:
        try:
            pull = await self.github.pull_request(
                CLEARANCE_AGENT_SLUG,
                notification.repository,
                notification.pull_number,
            )
            live_threads = await self.github.pull_request_review_threads(
                CLEARANCE_AGENT_SLUG,
                notification.repository,
                notification.pull_number,
            )
            latest = self.clearance_store.latest_poll(
                notification.repository,
                notification.pull_number,
            )
        except Exception:
            return None, "revalidation_unavailable"
        if str(pull.get("state") or "").lower() != "open":
            return False, "pull_not_open"
        if str((pull.get("head") or {}).get("sha") or "") != notification.head_sha:
            return False, "head_superseded"
        if latest is None or latest.head_sha != notification.head_sha:
            return False, "clearance_predicates_changed"
        author_login = (pull.get("user") or {}).get("login")
        if not isinstance(author_login, str) or not author_login.strip():
            return False, "missing_pr_author_login"
        persisted_by_id = {thread.id: thread for thread in latest.threads}
        live_by_id = {str(thread.get("id") or ""): thread for thread in live_threads or []}
        for thread_id in notification.thread_ids:
            persisted = persisted_by_id.get(thread_id)
            live = live_by_id.get(thread_id)
            if (
                persisted is None
                or live is None
                or not is_author_wakeup_eligible(
                    persisted,
                    live,
                    pr_author_login=author_login,
                )
            ):
                return False, "listed_thread_not_eligible"
        return True, "eligible"

    async def _check_receipt(self, notification: NotificationState, now: datetime) -> None:
        if not notification.transport_send_id:
            return
        try:
            receipt = await self.door.receipt(
                transport_send_id=notification.transport_send_id,
                notification_id=notification.notification_id,
            )
        except (httpx.HTTPError, TimeoutError, PfcDoorProtocolError):
            receipt = DoorReceipt(state="pending")
        if receipt.state == "pfc_received":
            updated = notification.with_updates(state=receipt.state)
            self.ledger.save_notification(updated, event=updated.state, at=now)
        elif receipt.state == "author_delivered":
            delivered = notification.with_updates(
                state="notified",
                recipient_citizen=receipt.recipient_citizen,
                author_delivered_at=now,
                claim_deadline=now + timedelta(minutes=self.config.fallback_after_minutes),
            )
            self.ledger.save_notification(delivered, event=delivered.state, at=now)
        elif receipt.state == "routing_failed":
            failed = notification.with_updates(
                state="notify_attempt_failed",
                next_delivery_attempt_at=(
                    now + timedelta(seconds=self.config.receipt_poll_interval_seconds)
                ),
            )
            self.ledger.save_notification(failed, event=failed.state, at=now)
            if notification.attempt_number >= self.config.max_delivery_attempts:
                terminal = failed.with_updates(
                    state="notify_failed",
                    next_delivery_attempt_at=None,
                )
                self.ledger.save_notification(terminal, event=terminal.state, at=now)
                _log.error(
                    "Clearance author-wakeup delivery failed after bounded attempts; "
                    "automatic fallback remains disarmed"
                )
            else:
                return
            return
        if receipt.state not in {"pending", "pfc_received"}:
            return
        window_started_at = notification.receipt_window_started_at or notification.first_posted_at
        if window_started_at is None:
            return
        if now - window_started_at < timedelta(seconds=self.config.receipt_timeout_seconds):
            return
        retained_until = notification.retained_until
        margin = timedelta(seconds=self.config.receipt_repost_safety_margin_seconds)
        if retained_until is None or retained_until - now < margin:
            unknown = notification.with_updates(state="notify_delivery_unknown")
            self.ledger.save_notification(unknown, event=unknown.state, at=now)
            _log.error(
                "Clearance author-wakeup delivery is unknown at the retention boundary; "
                "automatic fallback remains disarmed"
            )
            return
        if notification.same_id_reposts >= self.config.max_same_id_reposts:
            unknown = notification.with_updates(state="notify_delivery_unknown")
            self.ledger.save_notification(unknown, event=unknown.state, at=now)
            _log.error(
                "Clearance author-wakeup delivery is unknown after bounded same-ID reposts; "
                "automatic fallback remains disarmed"
            )
            return
        await self._post_notification(
            notification,
            now,
            transport_send_id=notification.transport_send_id,
            same_id_repost=True,
        )

    async def _check_claim_or_fallback(
        self,
        notification: NotificationState,
        now: datetime,
    ) -> None:
        try:
            pull = await self.github.pull_request(
                CLEARANCE_AGENT_SLUG,
                notification.repository,
                notification.pull_number,
            )
            threads = await self.github.pull_request_review_threads(
                CLEARANCE_AGENT_SLUG,
                notification.repository,
                notification.pull_number,
            )
        except (httpx.HTTPError, TimeoutError, RuntimeError):
            return

        claim_class = _claim_class(notification, pull, list(threads or []))
        if claim_class is not None:
            claimed = notification.with_updates(
                state="claimed",
                claim_class=claim_class,
            )
            self.ledger.save_notification(claimed, event=claimed.state, at=now)
            return

        deadline = notification.claim_deadline
        if deadline is None or now < deadline:
            return

        intent = notification.with_updates(state="fallback_intent")
        self.ledger.save_notification(intent, event=intent.state, at=now)
        refusal = self._fallback_refusal(notification, pull, list(threads or []))
        if refusal is not None:
            refused = intent.with_updates(
                state="fallback_refused",
                fallback_status=refusal,
            )
            self.ledger.save_notification(refused, event=refused.state, at=now)
            return

        started = intent.with_updates(state="fallback_started")
        self.ledger.save_notification(started, event=started.state, at=now)
        try:
            result = await self.review_fix(
                repository=notification.repository,
                pull_number=notification.pull_number,
                expected_head_sha=notification.head_sha,
                finding_ids=notification.thread_ids,
                notification_id=notification.notification_id,
            )
        except Exception as exc:
            finished = started.with_updates(
                state="fallback_finished",
                fallback_status=f"error:{type(exc).__name__}",
            )
            self.ledger.save_notification(finished, event=finished.state, at=now)
            return
        result_status = str((result or {}).get("status") or "unknown")
        state = "fallback_refused" if result_status == "review_fix_refused" else "fallback_finished"
        finished = started.with_updates(state=state, fallback_status=result_status)
        self.ledger.save_notification(finished, event=finished.state, at=now)

    def _fallback_refusal(
        self,
        notification: NotificationState,
        pull: dict[str, Any],
        threads: list[dict[str, Any]],
    ) -> str | None:
        if not self.config.auto_review_fix:
            return "auto_review_fix_disabled"
        if self.review_fix is None:
            return "review_fix_invoker_missing"
        if notification.repository not in set(self.config.allowed_repositories):
            return "repository_not_allowlisted"
        if not self._repository_in_scope(notification.repository):
            return "clearance_repository_not_allowlisted"
        if str(pull.get("state") or "").lower() != "open":
            return "pull_request_not_open"
        if str((pull.get("head") or {}).get("sha") or "") != notification.head_sha:
            return "notification_head_mismatch"
        author_login = (pull.get("user") or {}).get("login")
        if not isinstance(author_login, str) or not author_login.strip():
            return "missing_pr_author_login"

        live_by_id = {str(thread.get("id") or ""): thread for thread in threads}
        if any(
            thread_id not in live_by_id
            or not is_codex_thread(live_by_id[thread_id])
            or live_by_id[thread_id].get("isResolved") is True
            or live_by_id[thread_id].get("isOutdated") is True
            for thread_id in notification.thread_ids
        ):
            return "listed_thread_not_actionable"
        if not _persisted_predicates_hold(self.clearance_store, notification):
            return "clearance_predicates_changed"
        return None

    def _repository_in_scope(self, repository: str) -> bool:
        if repository not in set(self.config.allowed_repositories):
            return False
        if self.clearance_repository_allowed is None:
            return True
        try:
            return self.clearance_repository_allowed(repository)
        except Exception:
            return False


def _notification_id(
    repository: str,
    pull_number: int,
    head_sha: str,
    thread_ids: Sequence[str],
) -> str:
    material = "\0".join((repository, str(pull_number), head_sha, *sorted(thread_ids)))
    return sha256(material.encode("utf-8")).hexdigest()[:32]


def _poll_identity(poll: PollRecord) -> str:
    return sha256(poll.model_dump_json().encode("utf-8")).hexdigest()


def is_author_wakeup_eligible(
    persisted: Thread,
    live_thread: dict,
    *,
    pr_author_login: str | None,
) -> bool:
    """True for current unresolved OPEN Codex threads with no PR-author reply."""
    return (
        isinstance(pr_author_login, str)
        and bool(pr_author_login.strip())
        and persisted.verdict is Verdict.OPEN
        and not persisted.github_isResolved
        and is_codex_thread(live_thread)
        and live_thread.get("isResolved") is not True
        and live_thread.get("isOutdated") is not True
        and latest_author_reply(live_thread, author_login=pr_author_login) is None
    )


def _claim_class(
    notification: NotificationState,
    pull: dict[str, Any],
    threads: Sequence[dict[str, Any]],
) -> str | None:
    if str(pull.get("state") or "").lower() != "open":
        return "pull_closed"
    if str((pull.get("head") or {}).get("sha") or "") != notification.head_sha:
        return "head_superseded"
    live_by_id = {str(thread.get("id") or ""): thread for thread in threads}
    author_login = ((pull.get("user") or {}).get("login")) or None
    author_login = author_login if isinstance(author_login, str) and author_login.strip() else None
    for thread_id in notification.thread_ids:
        thread = live_by_id.get(thread_id)
        if thread is None:
            continue
        if thread.get("isResolved") is True:
            return "thread_resolved"
        reply = (
            latest_author_reply(thread, author_login=author_login)
            if author_login is not None
            else None
        )
        if reply is not None and _comment_at_or_after(
            reply,
            notification.author_delivered_at,
        ):
            return "author_reply"
    return None


def _comment_at_or_after(comment: dict[str, Any], boundary: datetime | None) -> bool:
    if boundary is None:
        return False
    raw = str(comment.get("createdAt") or "")
    if not raw:
        return False
    try:
        created_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return created_at >= boundary


def _persisted_predicates_hold(
    clearance_store: StateStore,
    notification: NotificationState,
) -> bool:
    latest: PollRecord | None = None
    for poll in clearance_store.read_polls():
        if poll.repo.lower() == notification.repository and poll.pr == notification.pull_number:
            latest = poll
    if latest is None or latest.head_sha != notification.head_sha:
        return False
    persisted_by_id = {thread.id: thread for thread in latest.threads}
    return all(
        thread_id in persisted_by_id
        and persisted_by_id[thread_id].verdict is Verdict.OPEN
        and not persisted_by_id[thread_id].github_isResolved
        for thread_id in notification.thread_ids
    )


def build_wakeup_message(
    *,
    notification_id: str,
    repository: str,
    pull_number: int,
    head_sha: str,
    thread_ids: Sequence[str],
    notify_after_minutes: int,
    fallback_after_minutes: int,
) -> str:
    """Render the versioned application message; delivery timing stays local."""
    joined_threads = ",".join(sorted(thread_ids))
    return "\n".join(
        (
            "[voyager-clearance-author-wakeup/v1]",
            f"notification_id: {notification_id}",
            f"repository: {repository}",
            f"pull_request: {pull_number}",
            f"head_sha: {head_sha}",
            f"thread_ids: {joined_threads}",
            f"notify_after_minutes: {notify_after_minutes}",
            f"fallback_after_minutes: {fallback_after_minutes}",
            "instruction: route this to the citizen that opened the PR; claim by replying "
            "as the PR author, resolving a listed thread, or pushing a new head",
        )
    )
