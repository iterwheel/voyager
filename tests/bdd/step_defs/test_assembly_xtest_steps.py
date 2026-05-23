"""Step definitions for Assembly cross-test BDD scenarios."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — import lazily
# inside step functions.

scenarios("../features/assembly_xtest.feature")


@pytest.fixture(autouse=True)
def _xtest_default_authorize_env(monkeypatch):
    """VOY-1818: xtest BDD scenarios use the same fixtures as the primary
    BDD suite, which now carry author_association="OWNER". Set the actor
    gate to set-but-empty (defaults) so the OWNER payload authorizes
    under the routing-time actor gate.
    """
    monkeypatch.delenv("BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS", raising=False)
    monkeypatch.setenv("BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS", "")


# ---------------------------------------------------------------------------
# Background / shared fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('the Assembly agent slug is "{slug}"'), target_fixture="agent_slug")
def assembly_agent_slug(slug: str) -> str:
    return slug


@given(parsers.parse('a webhook payload "{name}"'), target_fixture="payload")
def assembly_webhook_payload(webhook_fixture, name: str) -> dict:
    return webhook_fixture(name)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('Assembly receives the "{event}" event'), target_fixture="routes")
def assembly_receive_event(payload: dict, event: str) -> list:
    from voyager.bots.assembly import route_assembly_event

    return route_assembly_event(event, payload)


# ---------------------------------------------------------------------------
# Then — cardinality
# ---------------------------------------------------------------------------


@then("exactly one assembly route is produced")
def assembly_one_route(routes: list) -> None:
    assert len(routes) == 1, f"Expected 1 route, got {len(routes)}"


# ---------------------------------------------------------------------------
# Then — route identity
# ---------------------------------------------------------------------------


@then("the route targets the Assembly agent")
def assembly_route_targets_agent(routes: list, agent_slug: str) -> None:
    assert routes[0]["agent"] == agent_slug


@then(parsers.parse('the route kind is "{kind}"'))
def assembly_route_kind(routes: list, kind: str) -> None:
    assert routes[0]["kind"] == kind


# ---------------------------------------------------------------------------
# Then — validation fields
# ---------------------------------------------------------------------------


@then(parsers.parse('the route validation status is "{status}"'))
def assembly_route_validation_status(routes: list, status: str) -> None:
    assert routes[0]["validation"]["status"] == status


@then(parsers.parse('the route validation conclusion is "{conclusion}"'))
def assembly_route_validation_conclusion(routes: list, conclusion: str) -> None:
    assert routes[0]["validation"]["conclusion"] == conclusion


@then(parsers.parse('the route validation command is "{command}"'))
def assembly_route_validation_command(routes: list, command: str) -> None:
    assert routes[0]["validation"]["command"] == command


@then(parsers.parse('the route validation refusal reason is "{reason}"'))
def assembly_route_validation_refusal_reason(routes: list, reason: str) -> None:
    refusal = routes[0]["validation"].get("refusal") or {}
    assert refusal.get("reason") == reason


# ---------------------------------------------------------------------------
# Then — writeback fields
# ---------------------------------------------------------------------------


@then("the route writeback includes a contract dict")
def assembly_writeback_has_contract(routes: list) -> None:
    contract = routes[0]["writeback"].get("contract")
    assert contract is not None
    assert isinstance(contract, dict)
    assert "issue_number" in contract


@then(parsers.parse('the route writeback has dynamic "{value}"'))
def assembly_writeback_dynamic(routes: list, value: str) -> None:
    assert routes[0]["writeback"]["dynamic"] == value


@then(parsers.parse('the route writeback refusal has reason "{reason}"'))
def assembly_writeback_refusal_reason(routes: list, reason: str) -> None:
    refusal = routes[0]["writeback"].get("refusal") or {}
    assert refusal.get("reason") == reason
