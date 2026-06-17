"""Unit tests for KnownLimitationStore and pipeline suppression."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from voyager.bots.clearance.known_limitations import (
    KnownLimitationEntry,
    KnownLimitationStore,
    compute_fingerprint,
)

# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------


class TestComputeFingerprint:
    def test_deterministic_same_input(self) -> None:
        """Same path, line, body → same fingerprint."""
        fp1 = compute_fingerprint("src/main.py", 42, "some finding body")
        fp2 = compute_fingerprint("src/main.py", 42, "some finding body")
        assert fp1 == fp2

    def test_deterministic_different_line(self) -> None:
        """Different line numbers → different fingerprints."""
        fp1 = compute_fingerprint("src/main.py", 42, "same body")
        fp2 = compute_fingerprint("src/main.py", 99, "same body")
        assert fp1 != fp2

    def test_body_whitespace_normalisation(self) -> None:
        """Different whitespace in body → same fingerprint."""
        fp1 = compute_fingerprint("a.py", 1, "  hello   world  ")
        fp2 = compute_fingerprint("a.py", 1, "hello world")
        assert fp1 == fp2

    def test_none_line(self) -> None:
        """None line is treated as 0 for fingerprinting."""
        fp1 = compute_fingerprint("a.py", None, "body")
        fp2 = compute_fingerprint("a.py", 0, "body")
        assert fp1 == fp2

    def test_has_sha256_length(self) -> None:
        """Fingerprint is a hex-encoded SHA-256 digest (64 chars)."""
        fp = compute_fingerprint("x.py", 1, "body text")
        assert len(fp) == 64
        int(fp, 16)  # valid hex


# ---------------------------------------------------------------------------
# KnownLimitationEntry tests
# ---------------------------------------------------------------------------


class TestKnownLimitationEntry:
    def test_roundtrip_json(self) -> None:
        """to_json → from_json recovers the same data."""
        fp = compute_fingerprint("x.py", 1, "body")
        now = datetime.now(UTC).replace(microsecond=0)
        entry = KnownLimitationEntry(
            fingerprint=fp,
            decision_link="https://github.com/org/repo/issues/42",
            created_at=now,
            repo="org/repo",
            pr_number=42,
        )
        raw = entry.to_json()
        restored = KnownLimitationEntry.from_json(raw)
        assert restored.fingerprint == fp
        assert restored.decision_link == "https://github.com/org/repo/issues/42"
        assert restored.created_at == now
        assert restored.repo == "org/repo"
        assert restored.pr_number == 42

    def test_minimal_json_no_optional_fields(self) -> None:
        """Serialization works without repo/pr_number."""
        fp = compute_fingerprint("a.py", 1, "body")
        entry = KnownLimitationEntry(
            fingerprint=fp,
            decision_link="https://github.com/org/repo/issues/1",
        )
        raw = entry.to_json()
        restored = KnownLimitationEntry.from_json(raw)
        assert restored.fingerprint == fp
        assert restored.repo is None
        assert restored.pr_number is None


# ---------------------------------------------------------------------------
# KnownLimitationStore tests
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store(tmp_path: Path) -> KnownLimitationStore:
    """A KnownLimitationStore backed by a temp file."""
    return KnownLimitationStore(path=tmp_path / "known_limitations.jsonl")


class TestKnownLimitationStore:
    def test_lookup_missing(self, tmp_store: KnownLimitationStore) -> None:
        """A fingerprint not in the store returns None."""
        fp = compute_fingerprint("x.py", 1, "some finding")
        assert tmp_store.lookup(fp) is None

    def test_record_and_lookup(self, tmp_store: KnownLimitationStore) -> None:
        """A recorded fingerprint is found by lookup."""
        fp = compute_fingerprint("src/main.py", 42, "use of unsafe function")
        decision = "https://github.com/org/repo/issues/123"
        tmp_store.record(fp, decision, repo="org/repo", pr_number=42)
        entry = tmp_store.lookup(fp)
        assert entry is not None
        assert entry.decision_link == decision
        assert entry.repo == "org/repo"
        assert entry.pr_number == 42

    def test_contains(self, tmp_store: KnownLimitationStore) -> None:
        """__contains__ works for recorded and missing fingerprints."""
        fp_recorded = compute_fingerprint("a.py", 1, "finding one")
        fp_missing = compute_fingerprint("b.py", 2, "finding two")
        tmp_store.record(fp_recorded, "https://github.com/org/repo/issues/1")
        assert fp_recorded in tmp_store
        assert fp_missing not in tmp_store

    def test_storage_is_persistent(self, tmp_path: Path) -> None:
        """Multiple store instances share the same backing file."""
        fp = compute_fingerprint("a.py", 1, "persistent finding")
        store_a = KnownLimitationStore(path=tmp_path / "known_limitations.jsonl")
        store_a.record(fp, "https://github.com/org/repo/issues/1")
        store_b = KnownLimitationStore(path=tmp_path / "known_limitations.jsonl")
        assert store_b.lookup(fp) is not None
        assert store_b.lookup(fp).decision_link == "https://github.com/org/repo/issues/1"

    def test_all_oldest_first(self, tmp_store: KnownLimitationStore) -> None:
        """all() returns entries in append order."""
        fp1 = compute_fingerprint("a.py", 1, "first")
        fp2 = compute_fingerprint("b.py", 2, "second")
        tmp_store.record(fp1, "link1")
        tmp_store.record(fp2, "link2")
        entries = tmp_store.all()
        assert len(entries) == 2
        assert entries[0].fingerprint == fp1
        assert entries[1].fingerprint == fp2

    def test_empty_all(self, tmp_store: KnownLimitationStore) -> None:
        """An empty store returns an empty list from all()."""
        assert tmp_store.all() == []

    def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        """A corrupt line in the JSONL file is skipped without crashing the store."""
        store_path = tmp_path / "known_limitations.jsonl"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a good line, then a malformed one
        fp = compute_fingerprint("a.py", 1, "good finding")
        good_line = KnownLimitationEntry(fp, "link1").to_json()
        store_path.write_text(good_line + "\nnot-json\n")
        store = KnownLimitationStore(path=store_path)
        # The good entry is still found
        assert store.lookup(fp) is not None


# ---------------------------------------------------------------------------
# Pipeline suppression integration test pattern
# ---------------------------------------------------------------------------


def test_matched_fingerprint_is_suppressed(tmp_store: KnownLimitationStore) -> None:
    """A finding whose fingerprint matches a recorded entry is suppressed.

    This exercises the contract used by _process_thread: storing a fingerprint
    and verifying lookup returns the known limitation.
    """
    fp = compute_fingerprint("src/unsafe.py", 15, "Potential SQL injection in query builder")
    tmp_store.record(
        fp,
        "https://github.com/org/repo/issues/42",
        repo="org/repo",
        pr_number=42,
    )
    matched = tmp_store.lookup(fp)
    assert matched is not None
    assert matched.decision_link == "https://github.com/org/repo/issues/42"

    # A different finding is NOT matched
    fp_other = compute_fingerprint("src/safe.py", 1, "Other issue")
    assert tmp_store.lookup(fp_other) is None


def test_unrecorded_finding_handled_normally(tmp_store: KnownLimitationStore) -> None:
    """An unrecorded fingerprint returns None — normal processing continues."""
    fp = compute_fingerprint("src/new.py", 1, "Brand new finding not yet accepted")
    assert tmp_store.lookup(fp) is None


def test_store_default_path_is_under_home() -> None:
    """Default store path is under ~/.voyager/state/known_limitations.jsonl."""
    store = KnownLimitationStore()
    path_str = str(store.path())
    assert ".voyager/state/known_limitations.jsonl" in path_str
    assert path_str.startswith("/")  # absolute path
