"""Unit tests for pure helpers in scripts/e2e/run_matrix.py.

Trinity round-1 P1 (3/4 reviewers): the runner was 655 LOC with 0 tests,
particularly leaving _compare / _flatten_writeback / _extract_pr_number /
_poll_for_writeback (the comparator + polling core) untested. This file
pins their behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# Add scripts/ to sys.path so we can import the runner as a module.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e.run_matrix import (  # noqa: E402
    _compare,
    _extract_pr_number,
    _flatten_writeback,
    _matches_allowed_webhook_ids,
    _poll_for_writeback,
    _post_approval_review,
)

# ---------------------------------------------------------------------------
# _extract_pr_number
# ---------------------------------------------------------------------------


def test_extract_pr_number_top_level_int() -> None:
    assert _extract_pr_number({"pr_number": 42}) == 42


def test_extract_pr_number_top_level_str_digit() -> None:
    assert _extract_pr_number({"pr_number": "42"}) == 42


def test_extract_pr_number_under_route_validation() -> None:
    assert _extract_pr_number({"route": {"validation": {"pr_number": 99}}}) == 99


def test_extract_pr_number_under_planned() -> None:
    assert _extract_pr_number({"planned": {"pr_number": 7}}) == 7


def test_extract_pr_number_returns_none_when_absent() -> None:
    assert _extract_pr_number({"event": "pr_review", "applied": True}) is None


def test_extract_pr_number_returns_none_for_non_digit_string() -> None:
    assert _extract_pr_number({"pr_number": "abc"}) is None


# ---------------------------------------------------------------------------
# _flatten_writeback
# ---------------------------------------------------------------------------


def test_flatten_writeback_apply_path() -> None:
    wb = {
        "delivery_id": "abc",
        "event": "pull_request_review",
        "applied": True,
        "dry_run": False,
        "planned": {
            "add_labels": ["clearance-blocked"],
            "add_reactions": [],
        },
        "automation": {"status": "BLOCKED", "head_sha": "deadbeef"},
    }
    flat = _flatten_writeback(wb)
    assert flat["applied"] is True
    assert flat["dry_run"] is False
    assert flat["status"] == "BLOCKED"
    assert flat["automation_status"] == "BLOCKED"
    assert flat["head_sha"] == "deadbeef"
    assert flat["add_labels"] == ["clearance-blocked"]
    assert flat["label_present"] == "clearance-blocked"
    assert flat["writeback_skipped"] is False
    assert flat["delivery_id"] == "abc"


def test_flatten_writeback_stale_verdict_skip() -> None:
    wb = {
        "delivery_id": "xyz",
        "event": "pull_request_review",
        "ok": True,
        "skipped": "stale_verdict",
        "automation": {"status": "stale_verdict_skip", "head_sha": "newsha"},
    }
    flat = _flatten_writeback(wb)
    assert flat["ok"] is True
    assert flat["skipped"] == "stale_verdict"
    assert flat["writeback_skipped"] is True
    assert flat["automation_status"] == "stale_verdict_skip"


def test_flatten_writeback_error_path() -> None:
    wb = {"applied": False, "reason": "clearance enrichment failed: ValueError: bar"}
    flat = _flatten_writeback(wb)
    assert flat["applied"] is False
    assert "clearance enrichment failed" in flat["reason"]
    assert flat["status"] is None
    assert flat["writeback_skipped"] is False


def test_flatten_writeback_empty_record() -> None:
    flat = _flatten_writeback({})
    assert flat["applied"] is None
    assert flat["status"] is None
    assert flat["add_labels"] == []
    assert flat["label_present"] is None
    assert flat["writeback_skipped"] is False


# ---------------------------------------------------------------------------
# _compare
# ---------------------------------------------------------------------------


def test_compare_empty_expected_returns_empty() -> None:
    assert _compare({}, {"status": "READY"}) == []


def test_compare_matching_keys_returns_empty() -> None:
    assert _compare({"status": "READY"}, {"status": "READY", "extra": 1}) == []


def test_compare_mismatch_reports_expected_got() -> None:
    diffs = _compare({"status": "READY"}, {"status": "BLOCKED"})
    assert len(diffs) == 1
    assert "expected 'READY'" in diffs[0]
    assert "got 'BLOCKED'" in diffs[0]


def test_compare_missing_key_in_actual() -> None:
    diffs = _compare({"codex_severity": "P1"}, {"status": "READY"})
    assert len(diffs) == 1
    assert "codex_severity" in diffs[0]
    assert "not surfaced by voyager" in diffs[0]


def test_compare_substring_match_passes() -> None:
    assert (
        _compare(
            {"automation_reason_substring": "low-priority"},
            {
                "automation_reason": "all blocking threads RESOLVED; 1 low-priority thread still open"
            },
        )
        == []
    )


def test_compare_substring_match_fails() -> None:
    diffs = _compare(
        {"automation_reason_substring": "BLOCKED"},
        {"automation_reason": "all RESOLVED"},
    )
    assert len(diffs) == 1
    assert "substring 'BLOCKED'" in diffs[0]


def test_compare_substring_on_none_actual_no_false_positive() -> None:
    """DeepSeek r1 P2 — `str(None)` becomes 'None' which could match 'None'
    in 'NoneType'. We now reject substring matching when actual is None."""
    diffs = _compare(
        {"automation_reason_substring": "None"},
        {"automation_reason": None},
    )
    assert len(diffs) == 1
    assert "cannot do substring match on None" in diffs[0]


def test_compare_multiple_mismatches_aggregated() -> None:
    diffs = _compare(
        {"status": "READY", "dry_run": False},
        {"status": "BLOCKED", "dry_run": True},
    )
    assert len(diffs) == 2


# ---------------------------------------------------------------------------
# _poll_for_writeback (httpx mocked)
# ---------------------------------------------------------------------------


def _mock_transport(responses: list) -> httpx.MockTransport:
    """responses: list of (status_code, json_body) tuples, returned in order."""
    state = {"index": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = state["index"]
        state["index"] = min(i + 1, len(responses) - 1)
        code, body = responses[i]
        return httpx.Response(status_code=code, json=body)

    return httpx.MockTransport(handler)


def test_poll_returns_writeback_on_first_match(monkeypatch) -> None:
    """Happy path: first poll returns a record for our PR."""
    transport = _mock_transport(
        [
            (
                200,
                {
                    "writebacks": [
                        {"pr_number": 42, "event": "pull_request_review", "applied": True}
                    ]
                },
            ),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test", pr_number=42, timeout_s=2, interval_s=0.01
    )
    assert err is None
    assert wb["pr_number"] == 42
    assert wb["applied"] is True


def test_poll_skips_records_for_other_prs(monkeypatch) -> None:
    """Records for other PRs don't trigger a match."""
    transport = _mock_transport(
        [
            (
                200,
                {
                    "writebacks": [
                        {"pr_number": 7, "event": "pull_request_review"},
                        {"pr_number": 42, "event": "pull_request_review", "applied": True},
                    ]
                },
            ),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test", pr_number=42, timeout_s=2, interval_s=0.01
    )
    assert err is None
    assert wb["pr_number"] == 42
    assert wb["applied"] is True


def test_poll_filters_out_pre_review_pull_request_event(monkeypatch) -> None:
    """Codex GH-bot PR #15 P1: voyager's pre-review `pull_request opened`
    writeback for our PR must be skipped; only the later
    `pull_request_review` event matches the default filter."""
    transport = _mock_transport(
        [
            (
                200,
                {
                    "writebacks": [
                        {"pr_number": 42, "event": "pull_request", "status": "READY"},
                        {
                            "pr_number": 42,
                            "event": "pull_request_review",
                            "automation": {"status": "BLOCKED"},
                        },
                    ]
                },
            ),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test", pr_number=42, timeout_s=2, interval_s=0.01
    )
    assert err is None
    assert wb["event"] == "pull_request_review"


def test_poll_since_ts_excludes_old_records(monkeypatch) -> None:
    """`since_ts` defense-in-depth: events older than the marker are skipped."""
    transport = _mock_transport(
        [
            (
                200,
                {
                    "writebacks": [
                        {
                            "pr_number": 42,
                            "event": "pull_request_review",
                            "ts": "2026-01-01T00:00:00+00:00",
                            "stale": True,
                        },
                        {
                            "pr_number": 42,
                            "event": "pull_request_review",
                            "ts": "2026-05-15T12:00:00+00:00",
                            "stale": False,
                        },
                    ]
                },
            ),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test",
        pr_number=42,
        timeout_s=2,
        interval_s=0.01,
        since_ts="2026-03-01T00:00:00+00:00",
    )
    assert err is None
    assert wb["stale"] is False


def test_poll_allowed_webhook_ids_skip_matching_setup_approval(monkeypatch) -> None:
    """A delayed approval can match live state but must not match provenance."""
    transport = _mock_transport(
        [
            (
                200,
                {
                    "writebacks": [
                        {
                            "pr_number": 42,
                            "event": "pull_request_review",
                            "ts": "2026-05-15T12:00:02+00:00",
                            "source": "codex-thread",
                            "webhook": {"review_id": 200},
                            "planned": {"add_labels": ["clearance-ready"]},
                            "automation": {
                                "status": "ready_with_low_priority",
                                "unresolved_codex_thread_count": 1,
                                "sync_actions_count": 0,
                            },
                        },
                        {
                            "pr_number": 42,
                            "event": "pull_request_review",
                            "ts": "2026-05-15T12:00:03+00:00",
                            "source": "setup-approval",
                            "webhook": {"review_id": 100},
                            "planned": {"add_labels": ["clearance-ready"]},
                            "automation": {
                                "status": "ready_with_low_priority",
                                "unresolved_codex_thread_count": 1,
                                "sync_actions_count": 0,
                            },
                        },
                    ]
                },
            ),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test",
        pr_number=42,
        timeout_s=2,
        interval_s=0.01,
        since_ts="2026-05-15T12:00:00+00:00",
        allowed_review_ids=(200,),
        expected_actual={
            "status": "ready_with_low_priority",
            "label_present": "clearance-ready",
            "unresolved_codex_thread_count": 1,
        },
    )
    assert err is None
    assert wb["source"] == "codex-thread"


def test_poll_expected_actual_waits_past_pre_reply_verdict(monkeypatch) -> None:
    """F-class scenarios must not stop on the pre-reply blocked writeback."""
    pre_reply = {
        "pr_number": 42,
        "event": "pull_request_review_comment",
        "ts": "2026-05-15T12:00:02+00:00",
        "source": "pre-reply",
        "webhook": {"review_comment_id": 900},
        "planned": {"add_labels": ["clearance-blocked"]},
        "automation": {
            "status": "blocked",
            "unresolved_codex_thread_count": 1,
            "sync_actions_count": 0,
        },
    }
    final_reply = {
        "pr_number": 42,
        "event": "pull_request_review_comment",
        "ts": "2026-05-15T12:00:04+00:00",
        "source": "final-reply",
        "webhook": {"review_comment_id": 901},
        "planned": {"add_labels": ["clearance-ready"]},
        "automation": {
            "status": "ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    }
    transport = _mock_transport(
        [
            (200, {"writebacks": [pre_reply]}),
            (200, {"writebacks": [pre_reply, final_reply]}),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test",
        pr_number=42,
        timeout_s=2,
        interval_s=0.01,
        since_ts="2026-05-15T12:00:00+00:00",
        allowed_review_comment_ids=(900, 901),
        expected_actual={
            "status": "ready",
            "label_present": "clearance-ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    )
    assert err is None
    assert wb["source"] == "final-reply"


def test_poll_reply_scenario_review_id_matches_only_review_webhook(monkeypatch) -> None:
    """A shared review id must not let a delayed comment delivery match."""
    delayed_original = {
        "pr_number": 42,
        "event": "pull_request_review_comment",
        "ts": "2026-05-15T12:00:02+00:00",
        "source": "delayed-original-review-comment",
        "webhook": {"review_id": 300, "review_comment_id": 900},
        "planned": {"add_labels": ["clearance-ready"]},
        "automation": {
            "status": "ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    }
    reply = {
        "pr_number": 42,
        "event": "pull_request_review",
        "ts": "2026-05-15T12:00:04+00:00",
        "source": "reply-review",
        "webhook": {"review_id": 300},
        "planned": {"add_labels": ["clearance-ready"]},
        "automation": {
            "status": "ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    }
    transport = _mock_transport(
        [
            (200, {"writebacks": [delayed_original]}),
            (200, {"writebacks": [delayed_original, reply]}),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test",
        pr_number=42,
        timeout_s=2,
        interval_s=0.01,
        since_ts="2026-05-15T12:00:00+00:00",
        allowed_review_ids=(300,),
        allowed_review_comment_ids=(901,),
        expected_actual={
            "status": "ready",
            "label_present": "clearance-ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    )
    assert err is None
    assert wb["source"] == "reply-review"


def test_poll_expected_actual_uses_newest_candidate_not_older_match(monkeypatch) -> None:
    """A newer mismatch must block returning an older matching writeback."""
    older_ready = {
        "pr_number": 42,
        "event": "pull_request_review_comment",
        "ts": "2026-05-15T12:00:02+00:00",
        "source": "older-ready",
        "webhook": {"review_comment_id": 900},
        "planned": {"add_labels": ["clearance-ready"]},
        "automation": {
            "status": "ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    }
    newer_blocked = {
        "pr_number": 42,
        "event": "pull_request_review_comment",
        "ts": "2026-05-15T12:00:04+00:00",
        "source": "newer-blocked",
        "webhook": {"review_comment_id": 901},
        "planned": {"add_labels": ["clearance-blocked"]},
        "automation": {
            "status": "blocked",
            "unresolved_codex_thread_count": 1,
            "sync_actions_count": 0,
        },
    }
    newest_ready = {
        "pr_number": 42,
        "event": "pull_request_review_comment",
        "ts": "2026-05-15T12:00:06+00:00",
        "source": "newest-ready",
        "webhook": {"review_comment_id": 902},
        "planned": {"add_labels": ["clearance-ready"]},
        "automation": {
            "status": "ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    }
    transport = _mock_transport(
        [
            (200, {"writebacks": [older_ready, newer_blocked]}),
            (200, {"writebacks": [older_ready, newer_blocked, newest_ready]}),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test",
        pr_number=42,
        timeout_s=2,
        interval_s=0.01,
        since_ts="2026-05-15T12:00:00+00:00",
        allowed_review_comment_ids=(900, 901, 902),
        expected_actual={
            "status": "ready",
            "label_present": "clearance-ready",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 1,
        },
    )
    assert err is None
    assert wb["source"] == "newest-ready"


def test_poll_expected_actual_allows_stale_skip_without_current_approval(monkeypatch) -> None:
    """E-class stale-skip writebacks do not have review-thread signal counts."""
    stale_skip = {
        "pr_number": 42,
        "event": "pull_request_review",
        "ts": "2026-05-15T12:00:02+00:00",
        "webhook": {"review_id": 500},
        "skipped": "stale_verdict",
        "automation": {
            "status": "stale_verdict_skip",
            "unresolved_codex_thread_count": 0,
            "sync_actions_count": 0,
        },
    }
    transport = _mock_transport([(200, {"writebacks": [stale_skip]})])
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test",
        pr_number=42,
        timeout_s=2,
        interval_s=0.01,
        since_ts="2026-05-15T12:00:00+00:00",
        allowed_review_ids=(500,),
        expected_actual={
            "automation_status": "stale_verdict_skip",
            "writeback_skipped": True,
        },
    )
    assert err is None
    assert wb["skipped"] == "stale_verdict"


def test_poll_fail_fast_on_404(monkeypatch) -> None:
    """404 = endpoint not enabled — fail fast, don't retry until timeout."""
    transport = _mock_transport([(404, {"detail": "Not found"})])
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test", pr_number=42, timeout_s=2, interval_s=0.01
    )
    assert wb is None
    assert "404" in err
    assert "VOYAGER_E2E_DEBUG=1" in err


def test_poll_fail_fast_on_401(monkeypatch) -> None:
    """401 = auth — fail fast."""
    transport = _mock_transport([(401, {"detail": "missing token"})])
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test", pr_number=42, timeout_s=2, interval_s=0.01
    )
    assert wb is None
    assert "401" in err
    assert "auth rejected" in err


def test_poll_timeout_when_no_match(monkeypatch) -> None:
    """Records exist but none match our PR → timeout with informative message."""
    transport = _mock_transport(
        [
            (200, {"writebacks": [{"pr_number": 99}]}),
        ]
    )
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    wb, err = _poll_for_writeback(
        voyager_url="http://test", pr_number=42, timeout_s=0.05, interval_s=0.01
    )
    assert wb is None
    assert "timed out" in err
    assert "PR #42" in err


def test_poll_sends_auth_token_header(monkeypatch) -> None:
    """When auth_token is provided, request must carry X-Voyager-E2E-Token."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["token"] = request.headers.get("X-Voyager-E2E-Token")
        return httpx.Response(200, json={"writebacks": [{"pr_number": 42, "applied": True}]})

    transport = httpx.MockTransport(handler)
    _orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _orig(transport=transport, **kw))

    _poll_for_writeback(
        voyager_url="http://test",
        pr_number=42,
        timeout_s=2,
        interval_s=0.01,
        auth_token="secret-abc",
    )
    assert captured["token"] == "secret-abc"


def test_post_approval_review_posts_current_head_approval(monkeypatch) -> None:
    """current_approval setup uses the test bot token and pins the head SHA."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 123, "state": "APPROVED"})

    transport = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=transport, **kw))

    result = _post_approval_review(
        sandbox_repo="iterwheel/voyager-sandbox",
        pr_number=42,
        commit_sha="abc123",
        token="installation-token",
    )

    assert result["state"] == "APPROVED"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/repos/iterwheel/voyager-sandbox/pulls/42/reviews"
    assert request.headers["Authorization"] == "token installation-token"
    payload = json.loads(request.content)
    assert payload["commit_id"] == "abc123"
    assert payload["event"] == "APPROVE"


def test_matches_allowed_webhook_ids_accepts_review_or_comment_ids() -> None:
    assert _matches_allowed_webhook_ids(
        {"event": "pull_request_review", "webhook": {"review_id": 200}},
        allowed_review_ids=(200,),
    )
    assert _matches_allowed_webhook_ids(
        {"event": "pull_request_review_comment", "webhook": {"review_comment_id": 901}},
        allowed_review_comment_ids=(901,),
    )
    assert not _matches_allowed_webhook_ids(
        {"event": "pull_request_review", "webhook": {"review_id": 100}},
        allowed_review_ids=(200,),
    )
    assert not _matches_allowed_webhook_ids(
        {"event": "pull_request_review_comment", "webhook": {"review_id": 200}},
        allowed_review_ids=(200,),
    )
    assert not _matches_allowed_webhook_ids(
        {"event": "pull_request_review", "webhook": {"review_comment_id": 901}},
        allowed_review_comment_ids=(901,),
    )
