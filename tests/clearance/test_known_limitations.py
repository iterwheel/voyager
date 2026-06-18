"""Unit tests for KnownLimitationStore and pipeline suppression."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voyager.bots.clearance.known_limitations import (
    KnownLimitationEntry,
    KnownLimitationStore,
    compute_fingerprint,
    compute_scoped_fingerprint,
)
from voyager.bots.clearance.models import Verdict
from voyager.bots.clearance.pipeline import _known_limitation_line_candidates, _process_thread

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

    def test_scoped_fingerprint_includes_repository(self) -> None:
        """The same path/line/body differs across repositories."""
        fp1 = compute_scoped_fingerprint("org/repo-a", "src/main.py", 42, "same body")
        fp2 = compute_scoped_fingerprint("org/repo-b", "src/main.py", 42, "same body")
        assert fp1 != fp2


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

    def test_record_for_finding_is_scoped_by_repo(self, tmp_store: KnownLimitationStore) -> None:
        """Repo-scoped findings do not suppress another repository."""
        decision = "https://github.com/org/repo-a/issues/123"
        tmp_store.record_for_finding(
            repo="org/repo-a",
            path="src/main.py",
            line=42,
            body="same finding",
            decision_link=decision,
            pr_number=123,
        )

        assert (
            tmp_store.lookup_for_finding(
                repo="org/repo-a",
                path="src/main.py",
                line_candidates=[42],
                body="same finding",
            )
            is not None
        )
        assert (
            tmp_store.lookup_for_finding(
                repo="org/repo-b",
                path="src/main.py",
                line_candidates=[42],
                body="same finding",
            )
            is None
        )

    def test_legacy_lookup_filters_by_recorded_repo(self, tmp_store: KnownLimitationStore) -> None:
        """Old unscoped records only match their stored repo or global entries."""
        fp = compute_fingerprint("src/main.py", 42, "same finding")
        tmp_store.record(
            fp,
            "https://github.com/org/repo-a/issues/123",
            repo="org/repo-a",
            pr_number=123,
        )

        assert (
            tmp_store.lookup_for_finding(
                repo="org/repo-a",
                path="src/main.py",
                line_candidates=[42],
                body="same finding",
            )
            is not None
        )
        assert (
            tmp_store.lookup_for_finding(
                repo="org/repo-b",
                path="src/main.py",
                line_candidates=[42],
                body="same finding",
            )
            is None
        )

    def test_lookup_uses_original_line_candidate_for_outdated_threads(
        self, tmp_store: KnownLimitationStore
    ) -> None:
        """A current-thread acceptance still matches after GitHub nulls line."""
        tmp_store.record_for_finding(
            repo="org/repo",
            path="src/main.py",
            line=42,
            body="same finding",
            decision_link="https://github.com/org/repo/issues/123",
        )

        entry = tmp_store.lookup_for_finding(
            repo="org/repo",
            path="src/main.py",
            line_candidates=[None, 42],
            body="same finding",
        )

        assert entry is not None
        assert entry.decision_link == "https://github.com/org/repo/issues/123"

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
        fp = compute_fingerprint("a.py", 1, "good finding")
        good_line = KnownLimitationEntry(fp, "link1").to_json()
        invalid_object = "[]"
        invalid_timestamp = json.dumps(
            {
                "created_at": "not-a-date",
                "decision_link": "link2",
                "fingerprint": compute_fingerprint("b.py", 2, "bad finding"),
            }
        )
        store_path.write_text(
            "\n".join([invalid_object, invalid_timestamp, good_line, "not-json"]) + "\n",
            encoding="utf-8",
        )
        store = KnownLimitationStore(path=store_path)
        assert store.lookup(fp) is not None

    def test_non_string_fingerprint_line_skipped_before_indexing(self, tmp_path: Path) -> None:
        """A structurally corrupt fingerprint cannot crash index construction."""
        store_path = tmp_path / "known_limitations.jsonl"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        fp = compute_fingerprint("a.py", 1, "good finding")
        bad_line = json.dumps(
            {
                "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "decision_link": "https://github.com/org/repo/issues/bad",
                "fingerprint": [],
            }
        )
        good_line = KnownLimitationEntry(
            fp,
            "https://github.com/org/repo/issues/1",
        ).to_json()
        store_path.write_text(f"{bad_line}\n{good_line}\n", encoding="utf-8")

        store = KnownLimitationStore(path=store_path)
        entry = store.lookup(fp)
        assert entry is not None
        assert entry.decision_link == "https://github.com/org/repo/issues/1"

    def test_unreadable_store_fails_open(self, tmp_path: Path) -> None:
        """A bad store path disables suppression instead of crashing Clearance."""
        store_path = tmp_path / "known_limitations.jsonl"
        store_path.mkdir()
        store = KnownLimitationStore(path=store_path)

        assert store.lookup("missing") is None
        assert store.all() == []

    def test_append_separates_new_record_after_unterminated_corrupt_tail(
        self, tmp_path: Path
    ) -> None:
        """A corrupt unterminated tail does not swallow the next appended record."""
        store_path = tmp_path / "known_limitations.jsonl"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text('{"fingerprint":', encoding="utf-8")
        fp = compute_fingerprint("a.py", 1, "new finding")

        KnownLimitationStore(path=store_path).record(
            fp,
            "https://github.com/org/repo/issues/1",
            repo="org/repo",
            pr_number=1,
        )

        store = KnownLimitationStore(path=store_path)
        entry = store.lookup(fp)
        assert entry is not None
        assert entry.decision_link == "https://github.com/org/repo/issues/1"
        assert store_path.read_text(encoding="utf-8").startswith('{"fingerprint":\n{')


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


def test_known_limitation_line_candidates_keep_explicit_null_line() -> None:
    """Explicit null line fingerprints stay eligible before original anchors."""
    assert _known_limitation_line_candidates({"line": None, "originalLine": 42}) == [
        None,
        42,
    ]
    assert _known_limitation_line_candidates({"originalLine": 42}) == [42]


@pytest.mark.asyncio
async def test_known_limitation_fast_path_preserves_existing_marker_flags(
    tmp_store: KnownLimitationStore,
) -> None:
    """Suppressed threads retain current-head marker flags used by reply dedupe."""
    thread_id = "PRRT_known_limitation"
    head_sha = "abcdef1234567890abcdef1234567890abcdef12"
    body = "**P2** accepted known limitation."
    fp = compute_fingerprint("app.py", 10, body)
    tmp_store.record(fp, "https://github.com/org/repo/issues/42")
    thread_dict = {
        "id": thread_id,
        "isResolved": False,
        "isOutdated": False,
        "viewerCanResolve": True,
        "path": "app.py",
        "line": 10,
        "comments": {
            "nodes": [
                {
                    "databaseId": 101,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": body,
                    "url": "https://example/comments/101",
                    "createdAt": "2026-06-17T00:00:00Z",
                },
                {
                    "databaseId": 102,
                    "author": {"login": "iterwheel-clearance"},
                    "body": (
                        f"<!-- clearance-close-reason:{thread_id}:{head_sha[:12]} -->\n"
                        "**Clearance: resolved**"
                    ),
                    "url": "https://example/comments/102",
                    "createdAt": "2026-06-17T00:01:00Z",
                },
            ]
        },
    }

    processed = await _process_thread(
        thread_dict,
        repo="org/repo",
        pr=42,
        head_sha=head_sha,
        pr_title="Test PR",
        now=datetime.now(UTC),
        base_branch="main",
        branch_protected_state=True,
        client=object(),  # not used by the known-limitation fast path
        known_limitation_store=tmp_store,
    )

    assert processed is not None
    thread, _snapshot = processed
    assert thread.verdict == Verdict.RESOLVED
    assert thread.known_limitation_link == "https://github.com/org/repo/issues/42"
    assert thread.existing_close_reason_marker is True
    assert thread.existing_thread_conclusion_marker is True
    assert thread.existing_head_verdict_marker is True


@pytest.mark.asyncio
async def test_known_limitation_fast_path_matches_outdated_original_line(
    tmp_store: KnownLimitationStore,
) -> None:
    """Outdated GitHub threads keep matching via originalLine when line is null."""
    body = "**P2** accepted known limitation."
    tmp_store.record_for_finding(
        repo="org/repo",
        path="app.py",
        line=10,
        body=body,
        decision_link="https://github.com/org/repo/issues/42",
        pr_number=42,
    )
    thread_dict = {
        "id": "PRRT_known_limitation_outdated",
        "isResolved": False,
        "isOutdated": True,
        "viewerCanResolve": True,
        "path": "app.py",
        "line": None,
        "originalLine": 10,
        "comments": {
            "nodes": [
                {
                    "databaseId": 201,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": body,
                    "url": "https://example/comments/201",
                    "createdAt": "2026-06-17T00:00:00Z",
                },
            ]
        },
    }

    processed = await _process_thread(
        thread_dict,
        repo="org/repo",
        pr=42,
        head_sha="abcdef1234567890abcdef1234567890abcdef12",
        pr_title="Test PR",
        now=datetime.now(UTC),
        base_branch="main",
        branch_protected_state=True,
        client=object(),
        known_limitation_store=tmp_store,
    )

    assert processed is not None
    thread, snapshot = processed
    assert thread.verdict == Verdict.RESOLVED
    assert thread.known_limitation_link == "https://github.com/org/repo/issues/42"
    assert thread.line is None
    assert snapshot.current_line is None


@pytest.mark.asyncio
async def test_known_limitation_fast_path_matches_outdated_null_line_fingerprint(
    tmp_store: KnownLimitationStore,
) -> None:
    """Outdated acceptances recorded with line=None keep matching after anchors appear."""
    body = "**P2** accepted outdated known limitation."
    tmp_store.record_for_finding(
        repo="org/repo",
        path="app.py",
        line=None,
        body=body,
        decision_link="https://github.com/org/repo/issues/42",
        pr_number=42,
    )
    thread_dict = {
        "id": "PRRT_known_limitation_outdated_null_line",
        "isResolved": False,
        "isOutdated": True,
        "viewerCanResolve": True,
        "path": "app.py",
        "line": None,
        "originalLine": 10,
        "comments": {
            "nodes": [
                {
                    "databaseId": 301,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": body,
                    "url": "https://example/comments/301",
                    "createdAt": "2026-06-17T00:00:00Z",
                },
            ]
        },
    }

    processed = await _process_thread(
        thread_dict,
        repo="org/repo",
        pr=42,
        head_sha="abcdef1234567890abcdef1234567890abcdef12",
        pr_title="Test PR",
        now=datetime.now(UTC),
        base_branch="main",
        branch_protected_state=True,
        client=object(),
        known_limitation_store=tmp_store,
    )

    assert processed is not None
    thread, snapshot = processed
    assert thread.verdict == Verdict.RESOLVED
    assert thread.known_limitation_link == "https://github.com/org/repo/issues/42"
    assert thread.line is None
    assert snapshot.current_line is None


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
