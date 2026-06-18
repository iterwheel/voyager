"""Persistent store for accepted known limitations, keyed by finding fingerprint.

Layout::

    ~/.voyager/state/known_limitations.jsonl

Each line is one accepted limitation. The file is append-only; entries are
never removed or mutated (a limitation is a permanent decision record).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger(__name__)

# Default path for the global known-limitations store.
_DEFAULT_STORE_DIR = Path.home() / ".voyager" / "state"


def _fingerprint_input(path: str, line: int | None, body: str) -> str:
    """Stable, deterministic input for the fingerprint hash.

    Normalises whitespace in *body* so that formatting differences between
    runs do not change the fingerprint. Returns a plain text string; the
    caller feeds it through :func:`compute_fingerprint`.
    """
    # Normalise: collapse runs of whitespace, strip leading/trailing space.
    normalised_body = " ".join(body.split())
    parts = [path, str(line) if line is not None else "0", normalised_body]
    return "|".join(parts)


def compute_fingerprint(path: str, line: int | None, body: str) -> str:
    """Return a SHA-256 hex digest for a finding.

    The fingerprint is derived from the file path, line number, and
    whitespace-normalised Codex comment body — stable across runs and
    unaffected by formatting drift.
    """
    raw = _fingerprint_input(path, line, body)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_scoped_fingerprint(repo: str, path: str, line: int | None, body: str) -> str:
    """Return a repository-scoped fingerprint for a finding."""
    raw = _fingerprint_input(f"{repo}|{path}", line, body)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class KnownLimitationEntry:
    """One accepted known limitation — serialised as a JSON object."""

    __slots__ = ("created_at", "decision_link", "fingerprint", "pr_number", "repo")

    def __init__(
        self,
        fingerprint: str,
        decision_link: str,
        *,
        created_at: datetime | None = None,
        repo: str | None = None,
        pr_number: int | None = None,
    ) -> None:
        self.fingerprint = fingerprint
        self.decision_link = decision_link
        self.created_at = created_at or datetime.now(UTC).replace(microsecond=0)
        self.repo = repo
        self.pr_number = pr_number

    def to_json(self) -> str:
        data: dict[str, object] = {
            "fingerprint": self.fingerprint,
            "decision_link": self.decision_link,
            "created_at": self.created_at.isoformat(),
        }
        if self.repo is not None:
            data["repo"] = self.repo
        if self.pr_number is not None:
            data["pr_number"] = self.pr_number
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> KnownLimitationEntry:
        data = json.loads(line)
        if not isinstance(data, dict):
            raise TypeError("known limitation entry must be an object")
        fingerprint = data.get("fingerprint")
        if not isinstance(fingerprint, str):
            raise TypeError("known limitation fingerprint must be a string")
        decision_link = data.get("decision_link")
        if not isinstance(decision_link, str):
            raise TypeError("known limitation decision_link must be a string")
        created_at_raw = data.get("created_at")
        if not isinstance(created_at_raw, str):
            raise TypeError("known limitation created_at must be a string")
        repo = data.get("repo")
        if repo is not None and not isinstance(repo, str):
            raise TypeError("known limitation repo must be a string")
        pr_number = data.get("pr_number")
        if pr_number is not None and type(pr_number) is not int:
            raise TypeError("known limitation pr_number must be an integer")
        created_at = datetime.fromisoformat(created_at_raw)
        return cls(
            fingerprint=fingerprint,
            decision_link=decision_link,
            created_at=created_at,
            repo=repo,
            pr_number=pr_number,
        )

    def __repr__(self) -> str:
        return (
            f"KnownLimitationEntry("
            f"fingerprint={self.fingerprint[:12]}..., "
            f"decision_link={self.decision_link!r})"
        )


class KnownLimitationStore:
    """Persistent store for accepted known limitations.

    The store is a global (cross-repo) append-only JSONL file at
    ``~/.voyager/state/known_limitations.jsonl``. Entries are indexed by
    fingerprint for fast lookup.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_STORE_DIR / "known_limitations.jsonl"
        self._index: dict[str, KnownLimitationEntry] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, fingerprint: str) -> KnownLimitationEntry | None:
        """Return the entry for *fingerprint*, or ``None`` if not found."""
        idx = self._load_index()
        return idx.get(fingerprint)

    def lookup_for_finding(
        self,
        *,
        repo: str,
        path: str,
        line_candidates: list[int | None],
        body: str,
    ) -> KnownLimitationEntry | None:
        """Return an accepted limitation for a repo/path/body finding.

        New entries are keyed by repository so a shared ``~/.voyager/state``
        store cannot suppress another repository's matching path/body. Legacy
        unscoped fingerprints remain readable, but only when the stored repo
        is absent (global) or matches the current repository.
        """
        idx = self._load_index()
        for line in _unique_lines(line_candidates):
            entry = idx.get(compute_scoped_fingerprint(repo, path, line, body))
            if entry is not None:
                return entry

        for line in _unique_lines(line_candidates):
            entry = idx.get(compute_fingerprint(path, line, body))
            if entry is None:
                continue
            if entry.repo is None or entry.repo == repo:
                return entry
        return None

    def record(
        self,
        fingerprint: str,
        decision_link: str,
        *,
        repo: str | None = None,
        pr_number: int | None = None,
    ) -> KnownLimitationEntry:
        """Append a new accepted limitation and update the in-memory index.

        *decision_link* SHOULD be a URL or issue/PR reference documenting
        the decision to accept this limitation. Returns the new entry.
        """
        entry = KnownLimitationEntry(
            fingerprint=fingerprint,
            decision_link=decision_link,
            repo=repo,
            pr_number=pr_number,
        )
        self._append(entry)
        index = self._ensure_index()
        index[fingerprint] = entry
        return entry

    def record_for_finding(
        self,
        *,
        repo: str,
        path: str,
        line: int | None,
        body: str,
        decision_link: str,
        pr_number: int | None = None,
    ) -> KnownLimitationEntry:
        """Record a repo-scoped accepted limitation for a finding."""
        return self.record(
            compute_scoped_fingerprint(repo, path, line, body),
            decision_link,
            repo=repo,
            pr_number=pr_number,
        )

    def __contains__(self, fingerprint: str) -> bool:
        return self.lookup(fingerprint) is not None

    def all(self) -> list[KnownLimitationEntry]:
        """Return every recorded limitation, oldest-first."""
        return list(self._iter())

    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _store_path(self) -> Path:
        return self._path

    def _ensure_index(self) -> dict[str, KnownLimitationEntry]:
        if self._index is None:
            self._index = {}
            for entry in self._iter():
                self._index[entry.fingerprint] = entry
        return self._index

    def _load_index(self) -> dict[str, KnownLimitationEntry]:
        return self._ensure_index()

    def _iter(self) -> Iterator[KnownLimitationEntry]:
        store_path = self._store_path()
        if not store_path.exists():
            return
        try:
            with store_path.open() as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        yield KnownLimitationEntry.from_json(line)
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                        _log.warning(
                            "known_limitations: skipping malformed line: %s",
                            exc,
                        )
        except OSError as exc:
            _log.warning(
                "known_limitations: unable to read store %s: %s",
                store_path,
                exc,
            )

    def _append(self, entry: KnownLimitationEntry) -> None:
        store_path = self._store_path()
        store_path.parent.mkdir(parents=True, exist_ok=True)
        line = entry.to_json() + "\n"
        needs_separator = False
        if store_path.exists() and store_path.stat().st_size > 0:
            with store_path.open("rb") as f:
                f.seek(-1, 2)
                needs_separator = f.read(1) != b"\n"
        with store_path.open("a") as f:
            if needs_separator:
                f.write("\n")
            f.write(line)


def _unique_lines(lines: list[int | None]) -> list[int | None]:
    seen: set[int | None] = set()
    unique: list[int | None] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        unique.append(line)
    return unique or [None]
