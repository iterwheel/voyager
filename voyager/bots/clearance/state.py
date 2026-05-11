"""StateStore — JSONL append-only poll + thread store, organized by PR.

Layout under the configured state directory:

    <owner>/<repo>/pr-<N>/
        polls.jsonl                 # one line per poll for this PR (append-only)
        threads/
            <thread_id>.jsonl       # one line per poll for this Codex thread

All JSONL files are append-only — old records are NEVER rewritten. "Current"
state for a thread is the last line of its file; the full audit trail is the
whole file.

Construct ``StateStore(some_dir)`` for tests; use ``default_store()`` for
production use.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from voyager.bots.clearance.models import BoxMiss, LedgerEntry, PollRecord, ThreadSnapshot

_log = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _atomic_append_jsonl(path: Path, line: str) -> None:
    """Append one JSONL line under an advisory exclusive lock, fsynced before close.

    Guards three failure modes the bare ``with path.open("a") as f: f.write(...)``
    pattern doesn't cover:

    - **Interleaving** — concurrent appenders (webhook background task + poller
      cron) calling ``write()`` simultaneously can produce one line spliced
      into another above the platform's atomic-write threshold. POSIX
      O_APPEND only guarantees atomicity up to ``PIPE_BUF`` (~512 bytes on
      macOS, ~4 KiB on Linux); a PollRecord with embedded threads easily
      exceeds that. ``flock(LOCK_EX)`` serializes appenders.
    - **Durability** — a crash between ``write()`` and ``close()`` leaves the
      new line in the kernel page cache. ``fsync`` forces it to disk so a
      restart sees either the full line or none.
    - **Tail recovery** — if a previous appender crashed after writing some
      bytes but before writing the trailing ``\\n`` (and before fsync flushed
      it), the file's last byte is now part of a corrupt half-record. A naive
      append would concatenate the new payload onto the corrupt tail, merging
      two records into one invalid line — ``_read_jsonl`` would then reject
      the whole merged line, silently dropping the new record. Probe the tail
      after taking the lock; if the last byte isn't ``\\n``, prepend a newline
      to the payload so the new record starts on a fresh line and the corrupt
      tail is isolated as a malformed line that ``_read_jsonl`` skips.

    The lock is advisory (mandatory locking isn't portable). Every cross-process
    appender must call this helper or the guarantees break. POSIX only — on
    Windows this module fails to import because ``fcntl`` is unavailable; the
    deployment targets are Linux and macOS.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = line + "\n"
    with path.open("a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            size = os.fstat(f.fileno()).st_size
            if size > 0:
                # O_APPEND seeks to end on every write, but reading the last
                # byte needs a separate fd: read-mode at the same path.
                with path.open("rb") as r:
                    r.seek(size - 1)
                    last = r.read(1)
                if last != b"\n":
                    payload = "\n" + payload
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _read_jsonl(path: Path) -> Iterator[str]:
    """Yield non-blank, JSON-valid lines from a JSONL file.

    Malformed lines — most plausibly a partial append from an appender that
    crashed before fsync could complete — are skipped with a warning rather
    than propagated to Pydantic, which would raise ValidationError and abort
    the whole read.
    """
    if not path.exists():
        return
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                _log.warning("state: skipping malformed JSONL line in %s: %s", path, exc)
                continue
            yield line


class StateStore:
    """Filesystem-backed state. Each append uses fcntl+fsync; reads skip malformed lines."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    # --- path helpers ---------------------------------------------------------

    def _pr_dir(self, repo: str, pr: int) -> Path:
        owner, name = repo.split("/", 1)
        return self.directory / owner / name / f"pr-{pr}"

    def _polls_path(self, repo: str, pr: int) -> Path:
        return self._pr_dir(repo, pr) / "polls.jsonl"

    def _thread_path(self, repo: str, pr: int, thread_id: str) -> Path:
        return self._pr_dir(repo, pr) / "threads" / f"{thread_id}.jsonl"

    def _ledger_path(self, repo: str, pr: int) -> Path:
        return self._pr_dir(repo, pr) / "ledger.jsonl"

    def _box_misses_path(self, repo: str, pr: int) -> Path:
        return self._pr_dir(repo, pr) / "box-misses.jsonl"

    # --- polls ---------------------------------------------------------------

    def append_poll(self, record: PollRecord) -> None:
        _atomic_append_jsonl(self._polls_path(record.repo, record.pr), record.model_dump_json())

    def read_polls(self, repo: str | None = None, pr: int | None = None) -> Iterator[PollRecord]:
        for path in self._iter_polls_paths(repo, pr):
            for line in _read_jsonl(path):
                yield PollRecord.model_validate_json(line)

    def _iter_polls_paths(self, repo: str | None, pr: int | None) -> Iterator[Path]:
        """Yield every polls.jsonl matching the (repo, pr) filter."""
        if not self.directory.exists():
            return
        if repo is not None and pr is not None:
            yield self._polls_path(repo, pr)
            return
        if repo is not None:
            owner, name = repo.split("/", 1)
            base = self.directory / owner / name
            if base.exists():
                yield from sorted(base.glob("pr-*/polls.jsonl"))
            return
        if pr is not None:
            yield from sorted(self.directory.glob(f"*/*/pr-{pr}/polls.jsonl"))
            return
        yield from sorted(self.directory.glob("*/*/pr-*/polls.jsonl"))

    def latest_poll(self, repo: str, pr: int) -> PollRecord | None:
        last: PollRecord | None = None
        for rec in self.read_polls(repo, pr):
            last = rec
        return last

    def latest_per_pr(self, repo: str) -> dict[int, PollRecord]:
        by_pr: dict[int, PollRecord] = {}
        for path in self._iter_polls_paths(repo, None):
            try:
                pr = int(path.parent.name.removeprefix("pr-"))
            except ValueError:
                continue
            for line in _read_jsonl(path):
                by_pr[pr] = PollRecord.model_validate_json(line)
        return by_pr

    # --- threads -------------------------------------------------------------

    def write_thread(self, snapshot: ThreadSnapshot) -> None:
        """Append the snapshot as a new JSONL line — never overwrites prior history."""
        _atomic_append_jsonl(
            self._thread_path(snapshot.repo, snapshot.pr, snapshot.thread_id),
            snapshot.model_dump_json(),
        )

    def read_thread(self, repo: str, pr: int, thread_id: str) -> ThreadSnapshot | None:
        """Most recent snapshot (last line of JSONL), or None when missing."""
        path = self._thread_path(repo, pr, thread_id)
        last_line = None
        for line in _read_jsonl(path):
            last_line = line
        return ThreadSnapshot.model_validate_json(last_line) if last_line else None

    def read_thread_history(self, repo: str, pr: int, thread_id: str) -> list[ThreadSnapshot]:
        """Every snapshot ever written for this thread, oldest-first."""
        return [
            ThreadSnapshot.model_validate_json(line)
            for line in _read_jsonl(self._thread_path(repo, pr, thread_id))
        ]

    # --- ledger (audit trail of one-shot writes) -----------------------------

    def append_ledger(self, entry: LedgerEntry) -> None:
        """Append one Stage-3+ write record to ledger.jsonl. Never overwritten."""
        _atomic_append_jsonl(self._ledger_path(entry.repo, entry.pr), entry.model_dump_json())

    def read_ledger(self, repo: str, pr: int) -> list[LedgerEntry]:
        """Every ledger entry for this PR, oldest-first. Empty when missing."""
        return [
            LedgerEntry.model_validate_json(line)
            for line in _read_jsonl(self._ledger_path(repo, pr))
        ]

    # --- box misses (classifier blind-spot visibility) -----------------------

    def append_box_miss(self, miss: BoxMiss) -> None:
        _atomic_append_jsonl(self._box_misses_path(miss.repo, miss.pr), miss.model_dump_json())

    def read_box_misses(self, repo: str | None = None) -> Iterator[BoxMiss]:
        """Walk every box-misses.jsonl matching the optional repo filter."""
        for path in self._iter_box_misses_paths(repo):
            for line in _read_jsonl(path):
                yield BoxMiss.model_validate_json(line)

    def _iter_box_misses_paths(self, repo: str | None) -> Iterator[Path]:
        if not self.directory.exists():
            return
        if repo is not None:
            owner, name = repo.split("/", 1)
            base = self.directory / owner / name
            if base.exists():
                yield from sorted(base.glob("pr-*/box-misses.jsonl"))
            return
        yield from sorted(self.directory.glob("*/*/pr-*/box-misses.jsonl"))


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def default_store(state_dir: Path | None = None) -> StateStore:
    """Return a StateStore at ``state_dir``, defaulting to ``<repo_root>/state``.

    The default is anchored to the package install location (resolved via
    ``__file__``) rather than the current working directory — a chdir during
    request handling would otherwise silently scatter state across the filesystem.
    Production should pass an explicit path from the loaded TOML config; the
    default is for dev/test scaffolding only.
    """
    directory = state_dir or _REPO_ROOT / "state"
    return StateStore(directory)
