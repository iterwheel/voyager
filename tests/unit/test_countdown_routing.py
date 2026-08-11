"""Tests for voyager.bots.countdown.routing (CHG-1838).

Event-driven trigger: a Clearance RESOLVED-verdict reply on a review thread
should touch a machine-local trigger file so the Countdown adaptive scheduler
wakes early. Covers every row of the CHG's Event Matrix plus the fail-open
I/O contract (D6).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from voyager.bots.countdown import route_countdown_trigger
from voyager.bots.countdown.routing import touch_trigger_file

RESOLVED_HEADING = "✅ **Clearance: resolved**"
STILL_OPEN_HEADING = "👀 **Clearance: still open**"
CLEARANCE_BOT_LOGIN = "iterwheel-clearance[bot]"


def _resolved_comment_payload(
    *, login: str = CLEARANCE_BOT_LOGIN, body: str = RESOLVED_HEADING, action: str = "created"
) -> dict[str, Any]:
    return {
        "action": action,
        "comment": {
            "body": f"<!-- clearance-close-reason:PRRT_1:abc123456789 -->\n{body}\n\nsome detail",
            "user": {"login": login},
        },
        "pull_request": {"number": 125},
    }


# ---------------------------------------------------------------------------
# Event Matrix
# ---------------------------------------------------------------------------


class TestRouteCountdownTrigger:
    def test_clearance_resolved_reply_touches_trigger(self, tmp_path, monkeypatch) -> None:
        trigger = tmp_path / "countdown-resolve-loop.trigger"
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))

        routes = route_countdown_trigger("pull_request_review_comment", _resolved_comment_payload())

        assert routes == []
        assert trigger.exists()

    def test_clearance_non_resolved_heading_no_touch(self, tmp_path, monkeypatch) -> None:
        trigger = tmp_path / "countdown-resolve-loop.trigger"
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))

        routes = route_countdown_trigger(
            "pull_request_review_comment",
            _resolved_comment_payload(body=STILL_OPEN_HEADING),
        )

        assert routes == []
        assert not trigger.exists()

    def test_spoofed_author_with_resolved_heading_no_touch(self, tmp_path, monkeypatch) -> None:
        """Author guard fires before the body check (Event Matrix row 3)."""
        trigger = tmp_path / "countdown-resolve-loop.trigger"
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))

        routes = route_countdown_trigger(
            "pull_request_review_comment",
            _resolved_comment_payload(login="malicious-actor"),
        )

        assert routes == []
        assert not trigger.exists()

    def test_non_created_action_no_touch(self, tmp_path, monkeypatch) -> None:
        trigger = tmp_path / "countdown-resolve-loop.trigger"
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))

        routes = route_countdown_trigger(
            "pull_request_review_comment",
            _resolved_comment_payload(action="edited"),
        )

        assert routes == []
        assert not trigger.exists()

    def test_other_event_type_no_touch(self, tmp_path, monkeypatch) -> None:
        trigger = tmp_path / "countdown-resolve-loop.trigger"
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))

        routes = route_countdown_trigger("issue_comment", _resolved_comment_payload())

        assert routes == []
        assert not trigger.exists()

    def test_bare_login_form_also_matches(self, tmp_path, monkeypatch) -> None:
        """REST webhooks carry the `[bot]` suffix; GraphQL surfaces the bare form."""
        trigger = tmp_path / "countdown-resolve-loop.trigger"
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))

        routes = route_countdown_trigger(
            "pull_request_review_comment",
            _resolved_comment_payload(login="iterwheel-clearance"),
        )

        assert routes == []
        assert trigger.exists()

    def test_second_trigger_bumps_mtime(self, tmp_path, monkeypatch) -> None:
        trigger = tmp_path / "countdown-resolve-loop.trigger"
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger))

        route_countdown_trigger("pull_request_review_comment", _resolved_comment_payload())
        first_mtime = trigger.stat().st_mtime_ns

        backdated = first_mtime - 5_000_000_000
        os.utime(trigger, ns=(backdated, backdated))
        older_mtime = trigger.stat().st_mtime_ns
        assert older_mtime < first_mtime

        route_countdown_trigger("pull_request_review_comment", _resolved_comment_payload())

        assert trigger.stat().st_mtime_ns > older_mtime


# ---------------------------------------------------------------------------
# Fail-open (D6)
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_unwritable_trigger_path_does_not_raise(self, tmp_path, monkeypatch, caplog) -> None:
        # "blocker" is a regular file, so the trigger's parent directory can
        # never be created — a deterministic, permission-independent OSError.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(blocker / "trigger"))

        with caplog.at_level(logging.WARNING):
            routes = route_countdown_trigger(
                "pull_request_review_comment", _resolved_comment_payload()
            )

        assert routes == []
        assert any("countdown_trigger_touch_failed" in r.message for r in caplog.records)

    def test_touch_trigger_file_returns_false_on_io_error(self, tmp_path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        assert touch_trigger_file(blocker / "trigger") is False

    def test_touch_trigger_file_returns_true_on_success(self, tmp_path) -> None:
        target = tmp_path / "nested" / "countdown-resolve-loop.trigger"
        assert touch_trigger_file(target) is True
        assert target.exists()
