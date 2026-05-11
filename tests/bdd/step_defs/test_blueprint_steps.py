"""Step definitions for Blueprint BDD scenarios."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — those modules
# don't have implementations yet, so top-level imports would crash pytest
# collection. Import lazily INSIDE step functions instead.

scenarios("../features/blueprint.feature")


# ---------------------------------------------------------------------------
# Background / shared fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('the Blueprint agent slug is "{slug}"'), target_fixture="agent_slug")
def agent_slug(slug: str) -> str:
    return slug


@given(parsers.parse('a webhook payload "{name}"'), target_fixture="payload")
def webhook_payload(webhook_fixture, name: str) -> dict:
    return webhook_fixture(name)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('Blueprint receives the "{event}" event'), target_fixture="routes")
def receive_event(payload: dict, event: str) -> list:
    from voyager.bots.blueprint import route_blueprint_event  # lazy import — module empty

    return route_blueprint_event(event, payload)


# ---------------------------------------------------------------------------
# Then — cardinality
# ---------------------------------------------------------------------------


@then("exactly one route is produced")
def one_route(routes: list) -> None:
    assert len(routes) == 1


@then("no routes are produced")
def no_routes(routes: list) -> None:
    assert routes == []


# ---------------------------------------------------------------------------
# Then — route identity
# ---------------------------------------------------------------------------


@then("the route targets the Blueprint agent")
def route_targets_blueprint(routes: list, agent_slug: str) -> None:
    assert routes[0]["agent"] == agent_slug


@then('the route kind is "issue_blueprint_validation"')
def route_kind(routes: list) -> None:
    assert routes[0]["kind"] == "issue_blueprint_validation"


@then(parsers.parse('the route event is "{event}"'))
def route_event(routes: list, event: str) -> None:
    assert routes[0]["event"] == event


@then(parsers.parse('the route action is "{action}"'))
def route_action(routes: list, action: str) -> None:
    assert routes[0]["action"] == action


# ---------------------------------------------------------------------------
# Then — validation fields
# ---------------------------------------------------------------------------


@then("the route validation includes the issue number")
def route_has_issue_number(routes: list) -> None:
    assert routes[0]["validation"]["issue_number"] is not None


@then(parsers.parse('the route validation status is "{status}"'))
def route_validation_status(routes: list, status: str) -> None:
    assert routes[0]["validation"]["status"] == status


@then(parsers.parse('the route validation conclusion is "{conclusion}"'))
def route_validation_conclusion(routes: list, conclusion: str) -> None:
    assert routes[0]["validation"]["conclusion"] == conclusion


@then("the route validation has no missing fields")
def route_no_missing(routes: list) -> None:
    assert routes[0]["validation"]["missing"] == []


@then("the route validation has no weak fields")
def route_no_weak(routes: list) -> None:
    assert routes[0]["validation"]["weak"] == []


@then(parsers.parse('the field "{field}" is in the route validation missing list'))
def field_in_missing(routes: list, field: str) -> None:
    assert field in routes[0]["validation"]["missing"]


@then(parsers.parse('the field "{field}" is in the route validation weak list'))
def field_in_weak(routes: list, field: str) -> None:
    assert field in routes[0]["validation"]["weak"]


@then(parsers.parse('the section "{section}" is present in sections found'))
def section_in_sections_found(routes: list, section: str) -> None:
    assert section in routes[0]["validation"]["sections_found"]


# ---------------------------------------------------------------------------
# Then — writeback labels and reactions
# ---------------------------------------------------------------------------


@then(parsers.parse('the route writeback adds label "{label}"'))
def writeback_adds_label(routes: list, label: str) -> None:
    assert label in routes[0]["writeback"]["labels"]["add"]


@then(parsers.parse('the route writeback removes label "{label}"'))
def writeback_removes_label(routes: list, label: str) -> None:
    assert label in routes[0]["writeback"]["labels"]["remove"]


@then(parsers.parse('the route writeback adds reaction "{reaction}"'))
def writeback_adds_reaction(routes: list, reaction: str) -> None:
    assert reaction in routes[0]["writeback"]["reactions"]["add"]


@then(parsers.parse('the route writeback removes reaction "{reaction}"'))
def writeback_removes_reaction(routes: list, reaction: str) -> None:
    assert reaction in routes[0]["writeback"]["reactions"]["remove"]


# ---------------------------------------------------------------------------
# Then — writeback comment content
# ---------------------------------------------------------------------------


@then("the route writeback comment includes title guidance")
def writeback_comment_title_guidance(routes: list) -> None:
    comment = routes[0]["writeback"]["comment_body"]
    assert "[Task]: Add Blueprint issue template" in comment


@then("the route writeback comment includes acceptance criteria guidance")
def writeback_comment_ac_guidance(routes: list) -> None:
    comment = routes[0]["writeback"]["comment_body"]
    assert "Acceptance Criteria should include" in comment


# ---------------------------------------------------------------------------
# Then — writeback structure
# ---------------------------------------------------------------------------


@then("the route includes a writeback comment marker")
def route_has_comment_marker(routes: list) -> None:
    assert routes[0]["writeback"]["comment_marker"] != ""


@then("the route includes writeback label changes")
def route_has_label_changes(routes: list) -> None:
    labels = routes[0]["writeback"]["labels"]
    assert "add" in labels
    assert "remove" in labels


@then("the route includes writeback reaction changes")
def route_has_reaction_changes(routes: list) -> None:
    reactions = routes[0]["writeback"]["reactions"]
    assert "add" in reactions
    assert "remove" in reactions
