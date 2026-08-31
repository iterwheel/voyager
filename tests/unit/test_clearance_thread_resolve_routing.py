"""Issue #339: manual thread resolution must refresh the Clearance label.

A PR whose review threads are all manually resolved kept its stale
`clearance-N-pending` label: `pull_request_review_thread` resolved/unresolved
webhooks carried no Clearance trigger, so the readiness evaluation (and its
stage label) never recomputed without an unrelated event. Routing now fires on
thread resolve/unresolved, and the payload's top-level `pull_request` is
accepted as the target.
"""

from __future__ import annotations

from typing import Any

from voyager.bots.clearance import route_clearance_event
from voyager.bots.clearance.routing import should_run_clearance


def _thread_payload(action: str, *, with_pr: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "thread": {"id": "PRRT_test", "isResolved": action == "resolved"},
        "repository": {"full_name": "iterwheel/voyager"},
        "sender": {"login": "frankyxhl"},
    }
    if with_pr:
        payload["pull_request"] = {
            "number": 332,
            "html_url": "https://github.com/iterwheel/voyager/pull/332",
            "base": {"ref": "main"},
            "head": {"sha": "head-sha"},
        }
    return payload


def test_thread_resolved_triggers_clearance():
    assert should_run_clearance("pull_request_review_thread", _thread_payload("resolved")) is True


def test_thread_unresolved_triggers_clearance():
    assert should_run_clearance("pull_request_review_thread", _thread_payload("unresolved")) is True


def test_thread_event_without_pull_request_is_ignored():
    payload = _thread_payload("resolved", with_pr=False)
    assert should_run_clearance("pull_request_review_thread", payload) is False


def test_thread_resolved_produces_one_route_targeting_the_pr():
    routes = route_clearance_event("pull_request_review_thread", _thread_payload("resolved"))
    assert len(routes) == 1
    validation = routes[0]["validation"]
    assert validation["pr_number"] == 332
