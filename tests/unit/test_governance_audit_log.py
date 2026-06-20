from __future__ import annotations

import json
import stat
from datetime import UTC, datetime

import pytest

from voyager.governance.audit_log import (
    ReviewFixAuditLog,
    ReviewFixAuditLogError,
    ReviewFixAuditRecord,
)


def _record(round_number: int, verdict: str = "fixed") -> ReviewFixAuditRecord:
    return ReviewFixAuditRecord(
        round=round_number,
        ts=datetime(2026, 6, 20, 1, round_number, tzinfo=UTC),
        commit=f"deadbeef{round_number}",
        finding_id=f"codex-title:finding-{round_number}",
        category="codex-review",
        verdict=verdict,
        tests=("pytest tests/unit/test_governance_audit_log.py",),
    )


def test_append_then_read_yields_typed_records_in_order(tmp_path) -> None:
    path = tmp_path / "review-fix.jsonl"
    log = ReviewFixAuditLog(path)

    log.append(_record(1, "fixed"))
    log.append(_record(2, "accepted"))

    records = log.read_all()

    assert records == [_record(1, "fixed"), _record(2, "accepted")]


def test_log_persists_across_writer_instances(tmp_path) -> None:
    path = tmp_path / "review-fix.jsonl"

    ReviewFixAuditLog(path).append(_record(1))
    ReviewFixAuditLog(path).append(_record(2))

    assert ReviewFixAuditLog(path).read_all() == [_record(1), _record(2)]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_read_missing_log_returns_empty_list(tmp_path) -> None:
    assert ReviewFixAuditLog(tmp_path / "missing.jsonl").read_all() == []


def test_append_recovers_when_previous_line_lacks_newline(tmp_path) -> None:
    path = tmp_path / "review-fix.jsonl"
    path.write_text(json.dumps(_record(1).to_dict(), sort_keys=True), encoding="utf-8")

    ReviewFixAuditLog(path).append(_record(2))

    assert ReviewFixAuditLog(path).read_all() == [_record(1), _record(2)]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_append_creates_private_audit_file(tmp_path) -> None:
    path = tmp_path / "review-fix.jsonl"

    ReviewFixAuditLog(path).append(_record(1))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_malformed_line_is_surfaced_as_error(tmp_path) -> None:
    path = tmp_path / "review-fix.jsonl"
    ReviewFixAuditLog(path).append(_record(1))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")

    with pytest.raises(ReviewFixAuditLogError, match="line 2"):
        ReviewFixAuditLog(path).read_all()


def test_non_object_json_line_is_surfaced_as_error(tmp_path) -> None:
    path = tmp_path / "review-fix.jsonl"
    path.write_text('"not-an-object"\n', encoding="utf-8")

    with pytest.raises(ReviewFixAuditLogError, match="record must be a JSON object"):
        ReviewFixAuditLog(path).read_all()


def test_record_rejects_missing_required_field() -> None:
    data = _record(1).to_dict()
    del data["commit"]

    with pytest.raises(ReviewFixAuditLogError, match="missing required field: commit"):
        ReviewFixAuditRecord.from_dict(data)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"round": "1"}, "round must be a JSON integer"),
        ({"round": True}, "round must be a JSON integer"),
        ({"round": 0}, "round must be >= 1"),
        ({"ts": "not-a-date"}, "ts must be an ISO 8601 datetime"),
        ({"commit": 123}, "commit must be a JSON string"),
        ({"finding_id": "   "}, "finding_id must be a non-empty string"),
        ({"tests": "pytest"}, "tests must be a JSON array of strings"),
        ({"tests": [123]}, r"tests\[0\] must be a JSON string"),
        ({"tests": ["   "]}, r"tests\[0\] must be non-empty"),
    ],
)
def test_record_rejects_invalid_fields(
    override: dict[str, object],
    message: str,
) -> None:
    data = _record(1).to_dict()
    data.update(override)

    with pytest.raises(ReviewFixAuditLogError, match=message):
        ReviewFixAuditRecord.from_dict(data)
