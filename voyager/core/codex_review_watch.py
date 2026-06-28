"""Codex PR review watcher.

This module is the tested Python port of the old local bash helper. It keeps the
GitHub interaction behind a small protocol so verdict classification can be
unit-tested without network calls.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

CODE_OK = 0
CODE_ERROR = 1
CODE_FINDINGS = 2

TRIGGER_BODY = "@codex review"
DEFAULT_REPO = "iterwheel/voyager"
DEFAULT_BOT = "chatgpt-codex-connector[bot]"


class CodexWatchError(RuntimeError):
    """Operational watcher failure."""


class CodexReviewClient(Protocol):
    def pull_head_sha(self, repo: str, pr: int) -> str: ...

    def post_trigger(self, repo: str, pr: int) -> tuple[int, str, datetime]: ...

    def trigger_was_acked(self, repo: str, comment_id: int, bot_login: str) -> bool: ...

    def latest_trigger_created_at(self, repo: str, pr: int) -> datetime | None: ...

    def pull_inline_comments(self, repo: str, pr: int) -> list[dict[str, Any]]: ...

    def issue_reactions(self, repo: str, pr: int) -> list[dict[str, Any]]: ...

    def pull_issue_comments(self, repo: str, pr: int) -> list[dict[str, Any]]: ...

    def pull_reviews(self, repo: str, pr: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class WatchOptions:
    repo: str = DEFAULT_REPO
    pr: int = 0
    bot_login: str = DEFAULT_BOT
    trigger: bool = True
    timeout_seconds: int = 30 * 60
    poll_interval_seconds: int = 40
    since: datetime | None = None
    ack_attempts: int = 6
    ack_interval_seconds: int = 10


@dataclass(frozen=True)
class WatchResult:
    exit_code: int
    output: str


class GhCliClient:
    """GitHub client backed by ``gh api --paginate``."""

    def __init__(self, *, run: Callable[[list[str]], str] | None = None) -> None:
        self._run = run or _run_gh

    def pull_head_sha(self, repo: str, pr: int) -> str:
        return self._run(["gh", "api", f"repos/{repo}/pulls/{pr}", "--jq", ".head.sha"]).strip()

    def post_trigger(self, repo: str, pr: int) -> tuple[int, str, datetime]:
        url = self._run(["gh", "pr", "comment", str(pr), "--repo", repo, "--body", TRIGGER_BODY])
        url = url.strip().splitlines()[-1] if url.strip() else ""
        match = re.search(r"(\d+)$", url)
        if not match:
            raise CodexWatchError("could not parse trigger comment id")
        comment_id = int(match.group(1))
        created_raw = self._run(
            ["gh", "api", f"repos/{repo}/issues/comments/{comment_id}", "--jq", ".created_at"]
        )
        return comment_id, url, _parse_time(created_raw.strip())

    def trigger_was_acked(self, repo: str, comment_id: int, bot_login: str) -> bool:
        reactions = self._paginated_records(f"repos/{repo}/issues/comments/{comment_id}/reactions")
        return any(
            item.get("content") == "eyes" and _login(item.get("user")) == bot_login
            for item in reactions
        )

    def latest_trigger_created_at(self, repo: str, pr: int) -> datetime | None:
        triggers = [
            _parse_time(str(item.get("created_at")))
            for item in self.pull_issue_comments(repo, pr)
            if _is_trigger_comment(str(item.get("body") or ""))
        ]
        return max(triggers) if triggers else None

    def pull_inline_comments(self, repo: str, pr: int) -> list[dict[str, Any]]:
        return self._paginated_records(f"repos/{repo}/pulls/{pr}/comments")

    def issue_reactions(self, repo: str, pr: int) -> list[dict[str, Any]]:
        return self._paginated_records(f"repos/{repo}/issues/{pr}/reactions")

    def pull_issue_comments(self, repo: str, pr: int) -> list[dict[str, Any]]:
        return self._paginated_records(f"repos/{repo}/issues/{pr}/comments")

    def pull_reviews(self, repo: str, pr: int) -> list[dict[str, Any]]:
        return self._paginated_records(f"repos/{repo}/pulls/{pr}/reviews")

    def _paginated_records(self, endpoint: str) -> list[dict[str, Any]]:
        raw = self._run(["gh", "api", "--paginate", endpoint, "--jq", ".[] | @base64"])
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            decoded = base64.b64decode(line.strip()).decode("utf-8")
            item = json.loads(decoded)
            if isinstance(item, dict):
                records.append(item)
        return records


def watch_codex_review(
    client: CodexReviewClient,
    options: WatchOptions,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> WatchResult:
    lines: list[str] = []
    head = client.pull_head_sha(options.repo, options.pr)
    since = options.since or datetime.now(UTC)
    lines.append(
        f"watching {options.repo}#{options.pr} @ {head[:7]} "
        f"(bot={options.bot_login}, timeout={options.timeout_seconds // 60}m)"
    )

    if options.trigger:
        acked, since = _trigger_and_confirm_ack(client, options, lines, sleep)
        if not acked:
            lines.append("retrying trigger once")
            _acked, since = _trigger_and_confirm_ack(client, options, lines, sleep)
    elif options.since is None:
        latest = client.latest_trigger_created_at(options.repo, options.pr)
        if latest is not None:
            since = latest

    lines.append(f"detecting codex activity after: {_format_time(since)}")

    poll_count = _poll_count(options.timeout_seconds, options.poll_interval_seconds)
    signal: tuple[str, list[dict[str, Any]]] | None = None
    for i in range(1, poll_count + 1):
        signal = _detect_signal(client, options, since, head)
        if signal is not None:
            kind, items = signal
            lines.append(f"signal @ iter {i}: {kind}={len(items)}")
            break
        if i < poll_count and options.poll_interval_seconds > 0:
            sleep(options.poll_interval_seconds)

    if signal is None:
        lines.append(
            f"=== TIMED OUT after {options.timeout_seconds // 60}m "
            f"with no NEW verdict since {_format_time(since)} ==="
        )
        lines.append("    (re-run with --no-trigger to keep waiting without re-triggering)")
        return WatchResult(CODE_ERROR, "\n".join(lines) + "\n")

    kind, items = signal
    if kind == "inline":
        lines.append(f"=== FINDINGS (codex, after {_format_time(since)}) ===")
        for item in items:
            path = item.get("path") or "(unknown)"
            line = item.get("line") or item.get("original_line") or "?"
            body = str(item.get("body") or "")
            lines.append(f"--- {path}:{line}")
            lines.append(body)
            lines.append("")
        return WatchResult(CODE_FINDINGS, "\n".join(lines))

    current = client.pull_head_sha(options.repo, options.pr)
    if current != head:
        lines.append(
            f"=== HEAD MOVED {head[:7]} -> {current[:7]} during watch; "
            f"{_signal_label(kind)} is for the old head -- NOT clean ==="
        )
        lines.append("    re-run to review the new head")
        return WatchResult(CODE_ERROR, "\n".join(lines) + "\n")

    lines.append(f"=== CLEAN -- codex verdict on {options.repo}#{options.pr} @ {head[:7]} ===")
    return WatchResult(CODE_OK, "\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trigger and watch a Codex PR review.")
    parser.add_argument("pr", type=int, help="Pull request number.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository owner/name.")
    parser.add_argument("--bot", default=DEFAULT_BOT, help="Codex bot login.")
    parser.add_argument("--no-trigger", action="store_true", help="Do not post @codex review.")
    parser.add_argument(
        "--since", default="", help="ISO8601 cutoff. Overrides last trigger lookup."
    )
    parser.add_argument("--timeout-min", type=int, default=30, help="Timeout in minutes.")
    args = parser.parse_args(argv)

    since = _parse_time(args.since) if args.since else None
    result = watch_codex_review(
        GhCliClient(),
        WatchOptions(
            repo=args.repo,
            pr=args.pr,
            bot_login=args.bot,
            trigger=not args.no_trigger,
            since=since,
            timeout_seconds=args.timeout_min * 60,
        ),
    )
    stream = sys.stderr if result.exit_code == CODE_ERROR else sys.stdout
    stream.write(result.output)
    return result.exit_code


def _run_gh(args: list[str]) -> str:
    try:
        proc = subprocess.run(  # nosec B603
            args,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexWatchError(f"gh invocation failed: {type(exc).__name__}") from None
    if proc.returncode != 0:
        raise CodexWatchError(f"gh exited with returncode={proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def _trigger_and_confirm_ack(
    client: CodexReviewClient,
    options: WatchOptions,
    lines: list[str],
    sleep: Callable[[float], None],
) -> tuple[bool, datetime]:
    comment_id, url, created_at = client.post_trigger(options.repo, options.pr)
    lines.append(f"triggered: {url}")
    for attempt in range(options.ack_attempts):
        if client.trigger_was_acked(options.repo, comment_id, options.bot_login):
            lines.append("  codex acked eyes")
            return True, created_at
        if attempt + 1 < options.ack_attempts and options.ack_interval_seconds > 0:
            sleep(options.ack_interval_seconds)
    lines.append("  WARN: no ack within ack window (Codex may have dropped it)")
    return False, created_at


def _detect_signal(
    client: CodexReviewClient,
    options: WatchOptions,
    since: datetime,
    head_sha: str,
) -> tuple[str, list[dict[str, Any]]] | None:
    inline = _new_items(
        client.pull_inline_comments(options.repo, options.pr), since, options.bot_login
    )
    if inline:
        return ("inline", inline)

    clean_comments = [
        item
        for item in _new_items(
            client.pull_issue_comments(options.repo, options.pr), since, options.bot_login
        )
        if _is_clean_summary(str(item.get("body") or ""), head_sha)
    ]
    if clean_comments:
        return ("clean_comment", clean_comments)

    clean_reviews = [
        item
        for item in _new_items(
            client.pull_reviews(options.repo, options.pr), since, options.bot_login
        )
        if _is_clean_summary(str(item.get("body") or ""), head_sha)
    ]
    if clean_reviews:
        return ("clean_review", clean_reviews)

    thumbs = [
        item
        for item in _new_items(
            client.issue_reactions(options.repo, options.pr), since, options.bot_login
        )
        if item.get("content") == "+1"
    ]
    if thumbs:
        return ("thumbs", thumbs)
    return None


def _new_items(
    items: list[dict[str, Any]],
    since: datetime,
    bot_login: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if _login(item.get("user")) != bot_login:
            continue
        created = _parse_time(str(item.get("created_at") or item.get("submitted_at") or ""))
        if created > since:
            out.append(item)
    return out


def _is_clean_summary(body: str, head_sha: str) -> bool:
    lowered = body.lower()
    if not body.startswith("Codex Review:"):
        return False
    if "reviewed commit:" not in lowered:
        return False
    if any(marker in lowered for marker in ("however", "but ", " p1", " p2", " p3", "remains")):
        return False
    clean_phrase = any(
        phrase in lowered
        for phrase in (
            "didn't find any major issues",
            "did not find any major issues",
            "no major issues found",
        )
    )
    if not clean_phrase:
        return False
    match = re.search(r"reviewed commit:\s*`?([0-9a-f]{7,40})`?", body, flags=re.IGNORECASE)
    return bool(match and head_sha.startswith(match.group(1).lower()))


def _poll_count(timeout_seconds: int, poll_interval_seconds: int) -> int:
    if timeout_seconds <= 0:
        return 1
    if poll_interval_seconds <= 0:
        return 1
    return max(1, math.ceil(timeout_seconds / poll_interval_seconds))


def _is_trigger_comment(body: str) -> bool:
    return body.strip() == TRIGGER_BODY


def _signal_label(kind: str) -> str:
    if kind == "thumbs":
        return "thumbs-up"
    if kind == "clean_review":
        return "clean review"
    return "clean comment"


def _login(user: object) -> str:
    return str(user.get("login") or "") if isinstance(user, dict) else ""


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise CodexWatchError("missing timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
