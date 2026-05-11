"""Step definitions for Stack BDD scenarios."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — those modules
# don't have implementations yet, so top-level imports would crash pytest
# collection. Import lazily INSIDE step functions instead.

scenarios("../features/stack.feature")


# ---------------------------------------------------------------------------
# Background / shared fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('the Stack agent slug is "{slug}"'), target_fixture="stack_agent_slug")
def stack_agent_slug(slug: str) -> str:
    return slug


@given(parsers.parse('a webhook payload "{name}"'), target_fixture="payload")
def webhook_payload(webhook_fixture, name: str) -> dict:
    return webhook_fixture(name)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('Stack receives the "{event}" event'), target_fixture="stack_routes")
def receive_stack_event(payload: dict, event: str) -> list:
    from voyager.bots.stack import route_stack_event  # lazy — module empty

    return route_stack_event(event, payload)


# ---------------------------------------------------------------------------
# Then — cardinality
# ---------------------------------------------------------------------------


@then("exactly one stack route is produced")
def one_stack_route(stack_routes: list) -> None:
    assert len(stack_routes) == 1


@then("no stack routes are produced")
def no_stack_routes(stack_routes: list) -> None:
    assert stack_routes == []


# ---------------------------------------------------------------------------
# Then — route identity
# ---------------------------------------------------------------------------


@then("the stack route targets the Stack agent")
def stack_route_targets_agent(stack_routes: list, stack_agent_slug: str) -> None:
    assert stack_routes[0]["agent"] == stack_agent_slug


@then('the stack route kind is "stack_classification"')
def stack_route_kind(stack_routes: list) -> None:
    assert stack_routes[0]["kind"] == "stack_classification"


@then(parsers.parse('the stack route event is "{event}"'))
def stack_route_event(stack_routes: list, event: str) -> None:
    assert stack_routes[0]["event"] == event


@then(parsers.parse('the stack route action is "{action}"'))
def stack_route_action(stack_routes: list, action: str) -> None:
    assert stack_routes[0]["action"] == action


# ---------------------------------------------------------------------------
# Then — validation fields
# ---------------------------------------------------------------------------


@then("the stack validation includes the issue number")
def stack_validation_has_issue_number(stack_routes: list) -> None:
    assert stack_routes[0]["validation"]["issue_number"] is not None


@then(parsers.parse('the stack validation status is "{status}"'))
def stack_validation_status(stack_routes: list, status: str) -> None:
    assert stack_routes[0]["validation"]["status"] == status


@then(parsers.parse('the stack validation conclusion is "{conclusion}"'))
def stack_validation_conclusion(stack_routes: list, conclusion: str) -> None:
    assert stack_routes[0]["validation"]["conclusion"] == conclusion


@then(parsers.parse('the stack validation classifier is "{version}"'))
def stack_validation_classifier(stack_routes: list, version: str) -> None:
    assert stack_routes[0]["validation"]["classifier"] == version


@then("the stack confidence needs_review is true")
def stack_confidence_needs_review(stack_routes: list) -> None:
    assert stack_routes[0]["validation"]["confidence"]["needs_review"] is True


# ---------------------------------------------------------------------------
# Then — classification dimensions
# ---------------------------------------------------------------------------


@then(parsers.parse('the stack classification type is "{stack_type}"'))
def stack_classification_type(stack_routes: list, stack_type: str) -> None:
    assert stack_routes[0]["validation"]["classification"]["type"] == stack_type


@then(parsers.parse('the stack classification type source is "{source}"'))
def stack_classification_type_source(stack_routes: list, source: str) -> None:
    assert stack_routes[0]["validation"]["confidence"]["type_source"] == source


@then(parsers.parse('the stack classification area is "{area}"'))
def stack_classification_area(stack_routes: list, area: str) -> None:
    assert stack_routes[0]["validation"]["classification"]["area"] == area


@then(parsers.parse('the stack area source is "{source}"'))
def stack_area_source(stack_routes: list, source: str) -> None:
    assert stack_routes[0]["validation"]["confidence"]["area_source"] == source


@then(parsers.parse('the stack classification risk is "{risk}"'))
def stack_classification_risk(stack_routes: list, risk: str) -> None:
    assert stack_routes[0]["validation"]["classification"]["risk"] == risk


# ---------------------------------------------------------------------------
# Then — writeback labels and reactions
# ---------------------------------------------------------------------------


@then(parsers.parse('the stack writeback adds label "{label}"'))
def stack_writeback_adds_label(stack_routes: list, label: str) -> None:
    assert label in stack_routes[0]["writeback"]["labels"]["add"]


@then(parsers.parse('the stack writeback removes "{label}"'))
def stack_writeback_removes_label(stack_routes: list, label: str) -> None:
    assert label in stack_routes[0]["writeback"]["labels"]["remove"]


@then("the stack writeback adds exactly four labels")
def stack_writeback_four_labels(stack_routes: list) -> None:
    assert len(stack_routes[0]["writeback"]["labels"]["add"]) == 4


@then(parsers.parse('the stack writeback adds reaction "{reaction}"'))
def stack_writeback_adds_reaction(stack_routes: list, reaction: str) -> None:
    assert reaction in stack_routes[0]["writeback"]["reactions"]["add"]


@then(parsers.parse('the stack writeback removes reaction "{reaction}"'))
def stack_writeback_removes_reaction(stack_routes: list, reaction: str) -> None:
    assert reaction in stack_routes[0]["writeback"]["reactions"]["remove"]


# ---------------------------------------------------------------------------
# Then — writeback comment content
# ---------------------------------------------------------------------------


@then(parsers.parse('the stack writeback comment includes "{text}"'))
def stack_writeback_comment_includes(stack_routes: list, text: str) -> None:
    assert text in stack_routes[0]["writeback"]["comment_body"]


# ---------------------------------------------------------------------------
# Then — writeback structure
# ---------------------------------------------------------------------------


@then("the stack route includes the comment marker")
def stack_route_has_comment_marker(stack_routes: list) -> None:
    assert stack_routes[0]["writeback"]["comment_marker"] != ""


@then("the stack writeback has label add and remove keys")
def stack_writeback_label_keys(stack_routes: list) -> None:
    labels = stack_routes[0]["writeback"]["labels"]
    assert "add" in labels
    assert "remove" in labels


@then("the stack writeback has reaction add and remove keys")
def stack_writeback_reaction_keys(stack_routes: list) -> None:
    reactions = stack_routes[0]["writeback"]["reactions"]
    assert "add" in reactions
    assert "remove" in reactions
