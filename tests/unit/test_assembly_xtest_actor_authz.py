"""Independent cross-test for the Assembly actor-authorization gate.

Per VOY-1817 §Phase 6 + VOY-1818 §Surface 13: this suite is authored
*independently* of the primary test suite — it exercises only the
public-package surface of ``voyager.bots.assembly`` (specifically
``evaluate_actor_authorization`` and ``route_assembly_event``) and asserts
against documented schemas in VOY-1818, not implementation details of
``voyager.bots.assembly.actor``.

Coverage shape (intentionally minimal — one happy path + one deny path):

  HAPPY — authorized OWNER on a blueprint-ready issue:
    1) ``evaluate_actor_authorization`` returns ``ok=True`` with a
       ``matched_signal`` of either ``"allow_list"`` or ``"association"``.
    2) ``route_assembly_event`` produces an ``assembly_ready`` route whose
       writeback carries a contract, branch_name, and ``refusal is None``.

  DENY — unauthorized CONTRIBUTOR on the same issue:
    1) ``evaluate_actor_authorization`` returns ``ok=False`` with
       ``reason="unauthorized_actor"`` and ``matched_signal is None``.
    2) ``route_assembly_event`` produces an ``assembly_refused`` route
       whose ``writeback.refusal`` matches the documented refusal-payload
       extension (reason / missing_labels / outside_allow_list /
       actor_login / actor_association).

Independence guarantees:
  - Imports are from ``voyager.bots.assembly`` only (never
    ``voyager.bots.assembly.actor``).
  - The payload helper is local to this file (no shared fixture import).
  - Env mutation uses ``monkeypatch.setenv`` only.
"""

from __future__ import annotations

from typing import Any

import pytest

# Import the symbols that VOY-1818 §Surfaces row 6 documents as public.
from voyager.bots.assembly import (
    ASSEMBLY_AGENT_SLUG,
    AUTHORIZED_ACTORS_ENV,
    AUTHORIZED_ASSOCIATIONS_ENV,
    REFUSAL_UNAUTHORIZED_ACTOR,
    ActorAuthorization,
    evaluate_actor_authorization,
    route_assembly_event,
)

# ---------------------------------------------------------------------------
# Independent payload builder — no shared helpers with the primary suite.
# ---------------------------------------------------------------------------


def _build_issue_comment_payload(
    *,
    login: str,
    association: str,
    user_type: str = "User",
    issue_number: int = 4242,
    issue_title: str = "[Feature]: Add the widget",
    issue_labels: list[str] | None = None,
    command_body: str = "/assembly",
    repo: str = "iterwheel/voyager-sandbox",
) -> dict[str, Any]:
    """Construct a minimal issue_comment webhook payload from scratch.

    Mirrors the public webhook schema (sender + comment.user.* +
    comment.author_association) documented in VOY-1818 §Impact Analysis
    "External dependencies" without copying any test-internal helper.
    """
    labels = (
        issue_labels
        if issue_labels is not None
        else [
            "blueprint-ready",
            "stack-type-feature",
        ]
    )
    return {
        "action": "created",
        "sender": {"login": login},
        "repository": {"full_name": repo},
        "comment": {
            "body": command_body,
            "author_association": association,
            "user": {"login": login, "type": user_type},
        },
        "issue": {
            "number": issue_number,
            "title": issue_title,
            "html_url": f"https://github.com/{repo}/issues/{issue_number}",
            "body": "## Acceptance Criteria\n\n- [ ] Build the widget\n",
            "state": "open",
            "labels": [{"name": name} for name in labels],
        },
    }


# ---------------------------------------------------------------------------
# HAPPY PATH — authorized OWNER on a blueprint-ready issue
# ---------------------------------------------------------------------------


class TestXtestActorHappyPath:
    def test_evaluate_returns_ok_authorization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D6: set-but-empty activates the documented default associations.
        monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        payload = _build_issue_comment_payload(login="ryosaeba1985", association="OWNER")

        outcome = evaluate_actor_authorization(payload)

        # Schema check — every documented field per §ActorAuthorization Schema.
        assert isinstance(outcome, ActorAuthorization)
        assert outcome.ok is True
        assert outcome.reason is None
        assert outcome.actor_login == "ryosaeba1985"  # canonical lowercase
        assert outcome.actor_association == "OWNER"
        assert outcome.actor_type == "User"
        assert outcome.actor_sender_login == "ryosaeba1985"
        assert outcome.sender_divergent is False
        assert outcome.matched_signal in ("allow_list", "association")

    def test_route_produces_assembly_ready_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        payload = _build_issue_comment_payload(login="ryosaeba1985", association="OWNER")

        routes = route_assembly_event("issue_comment", payload)

        # Route cardinality + shape per VOY-1817 §Writeback Result Schema +
        # VOY-1818 §Gate Corner Table (AC+ row).
        assert len(routes) == 1
        route = routes[0]
        assert route["agent"] == ASSEMBLY_AGENT_SLUG
        assert route["kind"] == "assembly_implementation"

        validation = route["validation"]
        assert validation["status"] == "assembly_ready"
        # D11 actor audit block visible on AL+ routes.
        actor_block = validation.get("actor")
        assert actor_block is not None
        assert actor_block["login"] == "ryosaeba1985"
        assert actor_block["association"] == "OWNER"
        assert actor_block["matched_signal"] in ("allow_list", "association")

        writeback = route["writeback"]
        assert writeback["dynamic"] == "assembly_implementation"
        assert writeback["refusal"] is None
        assert writeback["contract"] is not None
        assert writeback["contract"]["issue_number"] == 4242
        assert writeback["branch_name"] is not None


# ---------------------------------------------------------------------------
# DENY PATH — unauthorized CONTRIBUTOR on a blueprint-ready issue
# ---------------------------------------------------------------------------


class TestXtestActorDenyPath:
    def test_evaluate_returns_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        payload = _build_issue_comment_payload(login="random-passerby", association="CONTRIBUTOR")

        outcome = evaluate_actor_authorization(payload)

        assert outcome.ok is False
        assert outcome.reason == REFUSAL_UNAUTHORIZED_ACTOR
        assert outcome.actor_login == "random-passerby"
        assert outcome.actor_association == "CONTRIBUTOR"
        assert outcome.matched_signal is None

    def test_route_produces_unauthorized_actor_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
        monkeypatch.setenv(AUTHORIZED_ASSOCIATIONS_ENV, "")
        payload = _build_issue_comment_payload(login="random-passerby", association="CONTRIBUTOR")

        routes = route_assembly_event("issue_comment", payload)

        assert len(routes) == 1
        route = routes[0]
        assert route["validation"]["status"] == "assembly_refused"

        # §Refusal Payload Extension — when reason == "unauthorized_actor",
        # the refusal carries the two optional actor fields.
        refusal = route["writeback"]["refusal"]
        assert refusal["reason"] == REFUSAL_UNAUTHORIZED_ACTOR
        assert refusal["missing_labels"] == []
        assert refusal["outside_allow_list"] is False
        assert refusal["actor_login"] == "random-passerby"
        assert refusal["actor_association"] == "CONTRIBUTOR"

        # Refusal-shape route has no contract / branch.
        assert route["writeback"]["contract"] is None
        assert route["writeback"]["branch_name"] is None
