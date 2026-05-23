"""Unit tests for Assembly routing (VOY-1817 Surface 17)."""

from __future__ import annotations

from voyager.bots.assembly import (
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_COMMENT_MARKER,
    route_assembly_event,
    should_run_assembly,
)


def _comment_payload(body: str, *, with_labels: bool = True) -> dict:
    labels = [{"name": "blueprint-ready"}, {"name": "stack-type-feature"}] if with_labels else []
    return {
        "action": "created",
        "repository": {"full_name": "iterwheel/voyager-sandbox"},
        "comment": {"body": body},
        "issue": {
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "html_url": "https://github.com/iterwheel/voyager-sandbox/issues/69",
            "body": "## Acceptance Criteria\n\n- [ ] Do the thing\n",
            "state": "open",
            "labels": labels,
        },
    }


def test_should_run_assembly_true_for_command_comment() -> None:
    assert should_run_assembly("issue_comment", _comment_payload("/assembly"))


def test_should_run_assembly_false_for_unrelated_event() -> None:
    assert not should_run_assembly("issues", _comment_payload("/assembly"))
    assert not should_run_assembly("pull_request", _comment_payload("/assembly"))


def test_should_run_assembly_false_for_edited_action() -> None:
    payload = _comment_payload("/assembly")
    payload["action"] = "edited"
    assert not should_run_assembly("issue_comment", payload)


def test_should_run_assembly_false_when_no_command() -> None:
    assert not should_run_assembly("issue_comment", _comment_payload("just chatting"))


def test_route_shape_for_happy_path() -> None:
    routes = route_assembly_event("issue_comment", _comment_payload("/assembly"))
    assert len(routes) == 1
    route = routes[0]
    assert route["agent"] == ASSEMBLY_AGENT_SLUG
    assert route["kind"] == "assembly_implementation"
    assert route["validation"]["status"] == "assembly_ready"
    writeback = route["writeback"]
    assert writeback["dynamic"] == "assembly_implementation"
    assert writeback["comment_marker"] == ASSEMBLY_COMMENT_MARKER
    assert writeback["contract"] is not None
    assert writeback["contract"]["issue_number"] == 69
    assert writeback["branch_name"] == "69-implement-assembly-bot-mvp"
    assert writeback["refusal"] is None


def test_route_carries_command_flags() -> None:
    routes = route_assembly_event(
        "issue_comment",
        _comment_payload("/assembly --dry-run --allow-missing-stack"),
    )
    flags = routes[0]["validation"]["command_flags"]
    assert flags == {"dry_run": True, "allow_missing_stack": True}


def test_route_refusal_on_missing_blueprint_ready() -> None:
    routes = route_assembly_event("issue_comment", _comment_payload("/assembly", with_labels=False))
    assert len(routes) == 1
    route = routes[0]
    assert route["validation"]["status"] == "assembly_refused"
    assert route["writeback"]["refusal"] is not None
    assert "blueprint-ready" in route["writeback"]["refusal"]["missing_labels"]
    # Refusal path does not build a contract.
    assert route["writeback"]["contract"] is None


def test_route_refusal_when_allow_missing_stack_still_keeps_blueprint() -> None:
    routes = route_assembly_event(
        "issue_comment",
        _comment_payload("/assembly --allow-missing-stack", with_labels=False),
    )
    assert routes[0]["validation"]["status"] == "assembly_refused"


def test_route_empty_for_non_matching_event() -> None:
    assert route_assembly_event("issues", _comment_payload("/assembly")) == []
    assert route_assembly_event("issue_comment", _comment_payload("hello")) == []
