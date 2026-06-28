from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from voyager.core.codex_review_watch import (
    CODE_ERROR,
    CODE_FINDINGS,
    CODE_OK,
    GhCliClient,
    WatchOptions,
    watch_codex_review,
)

BOT = "chatgpt-codex-connector[bot]"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class FakeClient:
    head_values: list[str] = field(default_factory=lambda: ["abc123456789"])
    trigger_acks: list[bool] = field(default_factory=lambda: [True])
    inline_comments: list[dict[str, Any]] = field(default_factory=list)
    issue_comments: list[dict[str, Any]] = field(default_factory=list)
    issue_comment_batches: list[list[dict[str, Any]]] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)
    thumbs: list[dict[str, Any]] = field(default_factory=list)
    trigger_count: int = 0

    def pull_head_sha(self, repo: str, pr: int) -> str:
        del repo, pr
        if len(self.head_values) > 1:
            return self.head_values.pop(0)
        return self.head_values[0]

    def post_trigger(self, repo: str, pr: int) -> tuple[int, str, datetime]:
        del repo, pr
        self.trigger_count += 1
        return (
            1000 + self.trigger_count,
            f"https://github.test/pull#issuecomment-{1000 + self.trigger_count}",
            _ts(f"2026-06-28T10:00:0{self.trigger_count}Z"),
        )

    def trigger_was_acked(self, repo: str, comment_id: int, bot_login: str) -> bool:
        del repo, comment_id, bot_login
        return self.trigger_acks.pop(0) if self.trigger_acks else False

    def latest_trigger_created_at(self, repo: str, pr: int) -> datetime | None:
        del repo, pr
        return _ts("2026-06-28T09:59:00Z")

    def pull_inline_comments(self, repo: str, pr: int) -> list[dict[str, Any]]:
        del repo, pr
        return self.inline_comments

    def issue_reactions(self, repo: str, pr: int) -> list[dict[str, Any]]:
        del repo, pr
        return self.thumbs

    def pull_issue_comments(self, repo: str, pr: int) -> list[dict[str, Any]]:
        del repo, pr
        if self.issue_comment_batches:
            return self.issue_comment_batches.pop(0)
        return self.issue_comments

    def pull_reviews(self, repo: str, pr: int) -> list[dict[str, Any]]:
        del repo, pr
        return self.reviews


def _opts(**overrides: Any) -> WatchOptions:
    values: dict[str, Any] = {
        "repo": "iterwheel/voyager",
        "pr": 225,
        "bot_login": BOT,
        "trigger": True,
        "timeout_seconds": 0,
        "poll_interval_seconds": 0,
        "ack_attempts": 1,
        "ack_interval_seconds": 0,
    }
    values.update(overrides)
    return WatchOptions(**values)


def _encoded_records(records: list[dict[str, Any]]) -> str:
    return "\n".join(
        base64.b64encode(json.dumps(item).encode("utf-8")).decode("ascii") for item in records
    )


def test_dropped_trigger_is_retried_and_second_ack_sets_cutoff() -> None:
    client = FakeClient(
        trigger_acks=[False, True],
        thumbs=[{"content": "+1", "user": {"login": BOT}, "created_at": "2026-06-28T10:00:03Z"}],
    )

    result = watch_codex_review(client, _opts())

    assert result.exit_code == CODE_OK
    assert client.trigger_count == 2
    assert "retrying trigger once" in result.output
    assert "detecting codex activity after: 2026-06-28T10:00:02Z" in result.output


def test_reanchored_old_inline_comment_is_rejected_by_created_at_cutoff() -> None:
    client = FakeClient(
        inline_comments=[
            {
                "user": {"login": BOT},
                "created_at": "2026-06-28T09:00:00Z",
                "commit_id": "abc123456789",
                "path": "voyager/core/example.py",
                "line": 10,
                "body": "old re-anchored finding",
            }
        ],
        issue_comments=[
            {
                "user": {"login": BOT},
                "created_at": "2026-06-28T10:00:03Z",
                "body": "Codex Review: Didn't find any major issues.\n\nReviewed commit: abc1234",
            }
        ],
    )

    result = watch_codex_review(client, _opts())

    assert result.exit_code == CODE_OK
    assert "old re-anchored finding" not in result.output


def test_findings_win_over_clean_comment_and_are_printed() -> None:
    client = FakeClient(
        inline_comments=[
            {
                "user": {"login": BOT},
                "created_at": "2026-06-28T10:00:03Z",
                "path": "voyager/core/example.py",
                "line": 42,
                "body": "Fix this branch.",
            }
        ],
        issue_comments=[
            {
                "user": {"login": BOT},
                "created_at": "2026-06-28T10:00:04Z",
                "body": "Codex Review: No major issues found.\n\nReviewed commit: abc1234",
            }
        ],
    )

    result = watch_codex_review(client, _opts())

    assert result.exit_code == CODE_FINDINGS
    assert "=== FINDINGS" in result.output
    assert "voyager/core/example.py:42" in result.output
    assert "Fix this branch." in result.output


def test_head_moved_before_clean_reaction_is_not_clean() -> None:
    client = FakeClient(
        head_values=["abc123456789", "def987654321"],
        thumbs=[{"content": "+1", "user": {"login": BOT}, "created_at": "2026-06-28T10:00:03Z"}],
    )

    result = watch_codex_review(client, _opts())

    assert result.exit_code == CODE_ERROR
    assert "HEAD MOVED abc1234 -> def9876" in result.output


def test_no_trigger_uses_last_trigger_cutoff_and_detects_paginated_clean_comment() -> None:
    client = FakeClient(
        issue_comments=[
            {
                "user": {"login": BOT},
                "created_at": "2026-06-28T10:01:00Z",
                "body": "Codex Review: Did not find any major issues.\n\nReviewed commit: abc123456789",
            }
        ],
    )

    result = watch_codex_review(client, _opts(trigger=False))

    assert result.exit_code == CODE_OK
    assert client.trigger_count == 0
    assert "detecting codex activity after: 2026-06-28T09:59:00Z" in result.output


def test_gh_cli_latest_trigger_ignores_operator_notes_that_mention_codex_review() -> None:
    def fake_run(args: list[str]) -> str:
        assert "--paginate" in args
        assert "repos/iterwheel/voyager/issues/225/comments" in args
        return _encoded_records(
            [
                {
                    "created_at": "2026-06-28T09:59:00Z",
                    "body": "@codex review",
                },
                {
                    "created_at": "2026-06-28T10:05:00Z",
                    "body": "waiting on @codex review before resolving threads",
                },
            ]
        )

    client = GhCliClient(run=fake_run)

    assert client.latest_trigger_created_at("iterwheel/voyager", 225) == _ts("2026-06-28T09:59:00Z")


def test_short_timeout_polls_with_ceiling_count() -> None:
    client = FakeClient(
        issue_comment_batches=[
            [],
            [
                {
                    "user": {"login": BOT},
                    "created_at": "2026-06-28T10:00:03Z",
                    "body": "Codex Review: No major issues found.\n\nReviewed commit: abc1234",
                }
            ],
        ]
    )

    result = watch_codex_review(
        client,
        _opts(timeout_seconds=60, poll_interval_seconds=40),
        sleep=lambda _seconds: None,
    )

    assert result.exit_code == CODE_OK
    assert "signal @ iter 2" in result.output


def test_clean_pr_review_body_is_a_verdict_surface() -> None:
    client = FakeClient(
        reviews=[
            {
                "user": {"login": BOT},
                "submitted_at": "2026-06-28T10:00:03Z",
                "body": "Codex Review: Didn't find any major issues.\n\nReviewed commit: abc1234",
            }
        ]
    )

    result = watch_codex_review(client, _opts())

    assert result.exit_code == CODE_OK
    assert "signal @ iter 1: clean_review=1" in result.output


def test_gh_cli_uses_paginate_for_detection_lists() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        return ""

    client = GhCliClient(run=fake_run)

    assert client.pull_inline_comments("iterwheel/voyager", 225) == []
    assert client.issue_reactions("iterwheel/voyager", 225) == []
    assert client.pull_issue_comments("iterwheel/voyager", 225) == []
    assert client.pull_reviews("iterwheel/voyager", 225) == []

    detection_calls = [
        args
        for args in calls
        if any(
            "pulls/225/comments" in arg or "pulls/225/reviews" in arg or "issues/225" in arg
            for arg in args
        )
    ]
    assert detection_calls
    assert all("--paginate" in args for args in detection_calls)


@pytest.mark.parametrize(
    "body",
    [
        "Codex Review: No major issues found.",
        "Codex Review: Didn't find any major issues. However, P1 remains.\n\nReviewed commit: abc1234",
        "### Codex Review\n\nHere are some automated review suggestions.\n\nReviewed commit: abc1234",
    ],
)
def test_ambiguous_or_unanchored_clean_comments_are_not_clean(body: str) -> None:
    client = FakeClient(
        issue_comments=[
            {"user": {"login": BOT}, "created_at": "2026-06-28T10:00:03Z", "body": body}
        ]
    )

    result = watch_codex_review(client, _opts())

    assert result.exit_code == CODE_ERROR
