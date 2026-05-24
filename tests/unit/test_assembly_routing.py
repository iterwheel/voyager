"""Unit tests for Assembly routing (VOY-1817 Surface 17 + VOY-1818 Surface 8)."""

from __future__ import annotations

import pytest

from voyager.bots.assembly import (
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_COMMENT_MARKER,
    AUTHORIZED_ACTORS_ENV,
    AUTHORIZED_ASSOCIATIONS_ENV,
    REFUSAL_UNAUTHORIZED_ACTOR,
    route_assembly_event,
    should_run_assembly,
)


def _comment_payload(
    body: str,
    *,
    with_labels: bool = True,
    actor_login: str = "ryosaeba1985",
    actor_type: str = "User",
    actor_association: str = "OWNER",
    sender_login: str | None = None,
) -> dict:
    """Build an issue_comment payload.

    VOY-1818 Surface 8: helper now injects an authorized actor by default so
    the pre-existing five route-shape tests stay green under the new gate.
    """
    labels = [{"name": "blueprint-ready"}, {"name": "stack-type-feature"}] if with_labels else []
    return {
        "action": "created",
        "sender": {"login": sender_login if sender_login is not None else actor_login},
        "repository": {"full_name": "iterwheel/voyager-sandbox"},
        "comment": {
            "body": body,
            "author_association": actor_association,
            "user": {"login": actor_login, "type": actor_type},
        },
        "issue": {
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "html_url": "https://github.com/iterwheel/voyager-sandbox/issues/69",
            "body": "## Acceptance Criteria\n\n- [ ] Do the thing\n",
            "state": "open",
            "labels": labels,
        },
    }


@pytest.fixture(autouse=True)
def _default_authorize_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per CHG §Testing: every routing test sets the gate to set-but-empty.

    This activates DEFAULT_AUTHORIZED_ASSOCIATIONS (OWNER/MEMBER/COLLABORATOR)
    so the default OWNER actor in ``_comment_payload`` authorizes.  Tests
    that need a different gate state must call ``monkeypatch.setenv`` after
    this autouse fixture runs.
    """
    monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
    monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")


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
    assert flags == {"dry_run": True, "allow_missing_stack": True, "resume": False}


def test_route_carries_resume_flag() -> None:
    routes = route_assembly_event("issue_comment", _comment_payload("/assembly --resume"))
    flags = routes[0]["validation"]["command_flags"]
    assert flags == {"dry_run": False, "allow_missing_stack": False, "resume": True}


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


# ---------------------------------------------------------------------------
# VOY-1818 Surface 8 — actor-gate scenarios
# ---------------------------------------------------------------------------


class TestActorGateAuthorized:
    """Scenario 1 — authorized actor → route built."""

    def test_authorized_actor_builds_full_route(self) -> None:
        routes = route_assembly_event(
            "issue_comment",
            _comment_payload("/assembly", actor_login="ryosaeba1985", actor_association="OWNER"),
        )
        assert len(routes) == 1
        route = routes[0]
        assert route["validation"]["status"] == "assembly_ready"
        assert route["writeback"]["refusal"] is None
        assert route["writeback"]["contract"] is not None

    def test_authorized_route_carries_actor_audit_block(self) -> None:
        routes = route_assembly_event("issue_comment", _comment_payload("/assembly"))
        validation = routes[0]["validation"]
        # D11 — actor block on the validation payload (visible to writeback ring).
        actor_block = validation.get("actor")
        assert actor_block is not None
        assert actor_block["login"] == "ryosaeba1985"
        assert actor_block["association"] == "OWNER"
        assert actor_block["type"] == "User"
        assert actor_block["matched_signal"] in ("allow_list", "association")


class TestActorGateUnauthorized:
    """Scenario 2 — unauthorized actor → refusal route with unauthorized_actor."""

    def test_unauthorized_contributor_refusal_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Set associations to defaults; CONTRIBUTOR not in defaults.
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        routes = route_assembly_event(
            "issue_comment",
            _comment_payload(
                "/assembly",
                actor_login="drive-by",
                actor_association="CONTRIBUTOR",
            ),
        )
        assert len(routes) == 1
        route = routes[0]
        assert route["validation"]["status"] == "assembly_refused"
        refusal = route["writeback"]["refusal"]
        assert refusal["reason"] == REFUSAL_UNAUTHORIZED_ACTOR
        # Refusal carries actor identity for the comment renderer.
        assert refusal["actor_login"] == "drive-by"
        assert refusal["actor_association"] == "CONTRIBUTOR"
        # Refusal-shape route has no contract / branch.
        assert route["writeback"]["contract"] is None
        assert route["writeback"]["branch_name"] is None

    def test_unauthorized_route_has_actor_audit_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        routes = route_assembly_event(
            "issue_comment",
            _comment_payload(
                "/assembly",
                actor_login="drive-by",
                actor_association="NONE",
            ),
        )
        validation = routes[0]["validation"]
        actor_block = validation.get("actor")
        assert actor_block is not None
        assert actor_block["login"] == "drive-by"
        assert actor_block["association"] == "NONE"
        assert actor_block["matched_signal"] is None


class TestActorGateOrdering:
    """Scenario 3 — D3 actor gate first, preconditions second."""

    def test_unauthorized_plus_missing_blueprint_picks_actor_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unauthorized actor on an issue that ALSO lacks blueprint-ready.
        # The actor refusal must win (D3).
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        routes = route_assembly_event(
            "issue_comment",
            _comment_payload(
                "/assembly",
                actor_login="drive-by",
                actor_association="NONE",
                with_labels=False,
            ),
        )
        refusal = routes[0]["writeback"]["refusal"]
        assert refusal["reason"] == REFUSAL_UNAUTHORIZED_ACTOR
        # The blueprint-ready issue-state info must NOT leak through.
        assert refusal.get("missing_labels") in ([], None)

    def test_authorized_plus_missing_blueprint_picks_precondition_reason(
        self,
    ) -> None:
        # When the actor IS authorized, preconditions become the next gate.
        routes = route_assembly_event(
            "issue_comment",
            _comment_payload("/assembly", with_labels=False),
        )
        refusal = routes[0]["writeback"]["refusal"]
        assert refusal["reason"] == "missing_blueprint_ready_label"


class TestRefusalNegativeAssertions:
    """Scenario 4 — pre-existing refusals MUST NOT carry actor fields."""

    def test_missing_blueprint_ready_refusal_has_no_actor_keys(self) -> None:
        routes = route_assembly_event(
            "issue_comment", _comment_payload("/assembly", with_labels=False)
        )
        refusal = routes[0]["writeback"]["refusal"]
        assert refusal["reason"] == "missing_blueprint_ready_label"
        assert "actor_login" not in refusal
        assert "actor_association" not in refusal

    def test_missing_stack_type_refusal_has_no_actor_keys(self) -> None:
        # blueprint-ready present, stack-type-* missing.
        payload = _comment_payload("/assembly")
        payload["issue"]["labels"] = [{"name": "blueprint-ready"}]
        routes = route_assembly_event("issue_comment", payload)
        refusal = routes[0]["writeback"]["refusal"]
        assert refusal["reason"] == "missing_stack_type_label"
        assert "actor_login" not in refusal
        assert "actor_association" not in refusal

    def test_pr_not_issue_refusal_has_no_actor_keys(self) -> None:
        payload = _comment_payload("/assembly")
        payload["issue"]["pull_request"] = {"url": "https://api.github.com/repos/o/r/pulls/1"}
        routes = route_assembly_event("issue_comment", payload)
        refusal = routes[0]["writeback"]["refusal"]
        assert refusal["reason"] == "pr_not_issue"
        assert "actor_login" not in refusal
        assert "actor_association" not in refusal


class TestHelperDefaultsAuthorize:
    """Scenario 5 — verify the updated helper defaults still authorize.

    The five pre-existing route-shape tests above implicitly cover this,
    but assert here explicitly so the helper contract is anchored.
    """

    def test_default_helper_payload_is_authorized(self) -> None:
        routes = route_assembly_event("issue_comment", _comment_payload("/assembly"))
        validation = routes[0]["validation"]
        assert validation["status"] == "assembly_ready"
        assert validation["actor"]["matched_signal"] == "association"
        assert validation["actor"]["association"] == "OWNER"
