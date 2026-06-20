"""Append-only JSONL audit log for governed review-fix loops."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_PRIVATE_AUDIT_FILE_MODE = 0o600


class ReviewFixAuditLogError(ValueError):
    """Raised when a review-fix audit log cannot be replayed safely."""


@dataclass(frozen=True, kw_only=True)
class ReviewFixAuditRecord:
    """One durable review-fix loop audit record."""

    round: int
    ts: datetime
    commit: str
    finding_id: str
    category: str
    verdict: str
    tests: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ts"] = self.ts.isoformat()
        data["tests"] = list(self.tests)
        return data

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        line_number: int | None = None,
    ) -> ReviewFixAuditRecord:
        location = f"line {line_number}: " if line_number is not None else ""
        return cls(
            round=_positive_int(data, "round", location),
            ts=_datetime_value(data, "ts", location),
            commit=_non_empty_string(data, "commit", location),
            finding_id=_non_empty_string(data, "finding_id", location),
            category=_non_empty_string(data, "category", location),
            verdict=_non_empty_string(data, "verdict", location),
            tests=_string_tuple(data, "tests", location),
        )


class ReviewFixAuditLog:
    """Append-only JSONL writer and replay reader."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: ReviewFixAuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.to_dict(), sort_keys=True) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _PRIVATE_AUDIT_FILE_MODE)
        os.fchmod(fd, _PRIVATE_AUDIT_FILE_MODE)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                if _needs_leading_newline(handle.fileno(), self.path):
                    payload = "\n" + payload
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def iter_records(self) -> Iterator[ReviewFixAuditRecord]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReviewFixAuditLogError(
                        f"{self.path}: line {line_number}: invalid JSON"
                    ) from exc
                if not isinstance(data, dict):
                    raise ReviewFixAuditLogError(
                        f"{self.path}: line {line_number}: record must be a JSON object"
                    )
                yield ReviewFixAuditRecord.from_dict(data, line_number=line_number)

    def read_all(self) -> list[ReviewFixAuditRecord]:
        return list(self.iter_records())


def _needs_leading_newline(fd: int, path: Path) -> bool:
    size = os.fstat(fd).st_size
    if size <= 0:
        return False
    with path.open("rb") as handle:
        handle.seek(size - 1)
        return handle.read(1) != b"\n"


def _positive_int(data: Mapping[str, Any], key: str, location: str) -> int:
    value = _required(data, key, location)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewFixAuditLogError(
            f"{location}{key} must be a JSON integer, got {type(value).__name__}: {value!r}"
        )
    if value < 1:
        raise ReviewFixAuditLogError(f"{location}{key} must be >= 1, got {value!r}")
    return value


def _datetime_value(data: Mapping[str, Any], key: str, location: str) -> datetime:
    raw = _non_empty_string(data, key, location)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ReviewFixAuditLogError(f"{location}{key} must be an ISO 8601 datetime") from exc


def _non_empty_string(data: Mapping[str, Any], key: str, location: str) -> str:
    value = _required(data, key, location)
    if not isinstance(value, str):
        raise ReviewFixAuditLogError(
            f"{location}{key} must be a JSON string, got {type(value).__name__}: {value!r}"
        )
    normalized = value.strip()
    if not normalized:
        raise ReviewFixAuditLogError(f"{location}{key} must be a non-empty string")
    return normalized


def _string_tuple(data: Mapping[str, Any], key: str, location: str) -> tuple[str, ...]:
    value = _required(data, key, location)
    if not isinstance(value, list):
        raise ReviewFixAuditLogError(
            f"{location}{key} must be a JSON array of strings, got "
            f"{type(value).__name__}: {value!r}"
        )
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ReviewFixAuditLogError(
                f"{location}{key}[{index}] must be a JSON string, got "
                f"{type(item).__name__}: {item!r}"
            )
        normalized = item.strip()
        if not normalized:
            raise ReviewFixAuditLogError(f"{location}{key}[{index}] must be non-empty")
        items.append(normalized)
    return tuple(items)


def _required(data: Mapping[str, Any], key: str, location: str) -> Any:
    if key not in data:
        raise ReviewFixAuditLogError(f"{location}missing required field: {key}")
    return data[key]
