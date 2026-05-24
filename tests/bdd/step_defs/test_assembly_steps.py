"""Step definitions for Assembly BDD scenarios (VOY-1817 Surface 22, VOY-1818 Surface 11)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# Lazy imports inside steps so a bad voyager.bots.assembly state surfaces
# at the failing step rather than at module import.

scenarios("../features/assembly.feature")


@pytest.fixture(autouse=True)
def _default_assembly_authorize_env(monkeypatch):
    """VOY-1818: every BDD scenario starts with the actor gate set to
    set-but-empty (defaults) so the pre-existing five scenarios' fixtures
    (now all carrying author_association="OWNER") authorize. Scenarios
    that need a different gate state override with their own Given step.
    """
    monkeypatch.delenv("BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS", raising=False)
    monkeypatch.setenv("BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS", "")


# ---------------------------------------------------------------------------
# Background / fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('the Assembly agent slug is "{slug}"'), target_fixture="agent_slug")
def agent_slug(slug: str) -> str:
    return slug


@given(parsers.parse('a webhook payload "{name}"'), target_fixture="payload")
def webhook_payload(webhook_fixture, name: str) -> dict:
    return webhook_fixture(name)


@given(parsers.parse('DRY_RUN is "{value}"'))
def set_dry_run(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DRY_RUN", value)


@given(parsers.parse('ASSEMBLY_EXECUTION_BACKEND is "{value}"'))
def set_backend(monkeypatch, value: str) -> None:
    monkeypatch.setenv("ASSEMBLY_EXECUTION_BACKEND", value)


@given("the fake subprocess backend is allowed")
def fake_subprocess_allowed(monkeypatch) -> None:
    monkeypatch.setenv("ASSEMBLY_FAKE_SUBPROCESS_ALLOW", "1")


@given(parsers.parse('the fake subprocess backend will return executed with commit SHA "{sha}"'))
def fake_subprocess_returns_commit(monkeypatch, sha: str) -> None:
    monkeypatch.setenv(
        "ASSEMBLY_FAKE_SUBPROCESS_OUTPUT",
        json.dumps(
            {
                "status": "executed",
                "commit_shas": [sha],
                "summary": "BDD fake subprocess commit",
            }
        ),
    )


@given("the repository allow-list is empty")
def empty_allow_list(monkeypatch) -> None:
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    # Force the deny path: allow-list empty + dry-run false = denied.
    monkeypatch.setenv("DRY_RUN", "false")


# ---------------------------------------------------------------------------
# VOY-1818 Surface 11 — actor-gate env-var Given steps
# ---------------------------------------------------------------------------


@given("the BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS env is set-but-empty")
def assoc_env_set_but_empty(monkeypatch) -> None:
    """D6: set-but-empty -> default trusted associations activate."""
    monkeypatch.setenv("BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS", "")


@given(parsers.parse('the BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS env contains "{logins}"'))
def actor_env_contains(monkeypatch, logins: str) -> None:
    monkeypatch.setenv("BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS", logins)


@given(parsers.parse('the BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS env contains "{associations}"'))
def assoc_env_contains(monkeypatch, associations: str) -> None:
    monkeypatch.setenv("BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS", associations)


@given(parsers.parse('the webhook comes from "{login}" with association "{assoc}"'))
def webhook_actor_override(payload: dict, login: str, assoc: str) -> None:
    """Mutate the loaded webhook fixture to set comment.user.login + assoc."""
    comment = payload.setdefault("comment", {})
    user = comment.setdefault("user", {})
    user["login"] = login
    user.setdefault("type", "User")
    comment["author_association"] = assoc
    payload.setdefault("sender", {})["login"] = login


# ---------------------------------------------------------------------------
# When — routing
# ---------------------------------------------------------------------------


@when(
    parsers.parse('Assembly receives the "{event}" event'),
    target_fixture="routes",
)
def receive_event(payload: dict, event: str) -> list:
    from voyager.bots.assembly import route_assembly_event

    return route_assembly_event(event, payload)


@when(
    "the bridge filters routes by repository",
    target_fixture="filter_outcome",
)
def filter_routes(payload: dict) -> dict:
    from voyager.bots.assembly import route_assembly_event
    from voyager.server import _filter_routes_by_repository

    candidate = route_assembly_event("issue_comment", payload)
    repo = (payload.get("repository") or {}).get("full_name")
    allowed, denied = _filter_routes_by_repository(candidate, repo)
    return {"allowed": allowed, "denied": denied}


@when(
    "Assembly dispatches the route with a mock GitHub client",
    target_fixture="dispatch_outcome",
)
def dispatch_route(routes: list, payload: dict) -> dict:
    from voyager.bots.assembly.writeback import dispatch_assembly_writeback

    assert routes, "no route to dispatch"
    route = routes[0]
    client = AsyncMock()
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.create_branch_ref = AsyncMock(return_value={"object": {"sha": "newsha"}})
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(
        return_value={
            "number": 999,
            "html_url": "https://example/pr/999",
            "head": {"repo": {"full_name": "iterwheel/voyager"}},
            "base": {"repo": {"full_name": "iterwheel/voyager"}},
        }
    )
    client.update_pull_request = AsyncMock(return_value={})
    client.create_issue_comment = AsyncMock(return_value={"id": 1})
    client.upsert_issue_comment = AsyncMock(return_value={"id": 2})
    # Real OMP adapter scenarios should fail at context validation in BDD,
    # not spawn local git/omp subprocesses.
    client.installation_token = AsyncMock(return_value="")

    repo = (payload.get("repository") or {}).get("full_name")
    result = asyncio.run(dispatch_assembly_writeback(client, route, repository=repo))
    return {"client": client, "result": result}


# ---------------------------------------------------------------------------
# Then — routing cardinality
# ---------------------------------------------------------------------------


@then("exactly one route is produced")
def one_route(routes: list) -> None:
    assert len(routes) == 1


@then("the route targets the Assembly agent")
def route_targets_assembly(routes: list, agent_slug: str) -> None:
    assert routes[0]["agent"] == agent_slug


@then(parsers.parse('the route writeback is dynamic "{dynamic}"'))
def route_dynamic(routes: list, dynamic: str) -> None:
    assert routes[0]["writeback"]["dynamic"] == dynamic


@then(parsers.parse("the route writeback contract has issue number {number:d}"))
def contract_issue_number(routes: list, number: int) -> None:
    assert routes[0]["writeback"]["contract"]["issue_number"] == number


@then(parsers.parse('the route writeback branch name is "{name}"'))
def branch_name(routes: list, name: str) -> None:
    assert routes[0]["writeback"]["branch_name"] == name


@then(parsers.parse('the route writeback contract forbidden_operations includes "{value}"'))
def contract_forbidden_includes(routes: list, value: str) -> None:
    forbidden = routes[0]["writeback"]["contract"]["forbidden_operations"]
    assert value in forbidden


@then(parsers.parse('the route validation status is "{status}"'))
def validation_status(routes: list, status: str) -> None:
    assert routes[0]["validation"]["status"] == status


@then("the route writeback contract is present")
def contract_present(routes: list) -> None:
    assert routes[0]["writeback"]["contract"] is not None


@then(parsers.parse('the route writeback refusal reason is "{reason}"'))
def refusal_reason(routes: list, reason: str) -> None:
    refusal = routes[0]["writeback"]["refusal"]
    assert refusal is not None
    assert refusal["reason"] == reason


# ---------------------------------------------------------------------------
# Then — dispatcher outcomes
# ---------------------------------------------------------------------------


@then(parsers.parse('the dispatcher result has dry_run "{value}"'))
def dispatcher_dry_run(dispatch_outcome: dict, value: str) -> None:
    assert dispatch_outcome["result"]["dry_run"] is (value.lower() == "true")


@then(parsers.parse('the dispatcher result adapter_result status is "{status}"'))
def dispatcher_adapter_status(dispatch_outcome: dict, status: str) -> None:
    assert dispatch_outcome["result"]["adapter_result"]["status"] == status


@then("the dispatcher made no GitHub mutations")
def dispatcher_no_writes(dispatch_outcome: dict) -> None:
    client = dispatch_outcome["client"]
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0
    assert client.create_issue_comment.await_count == 0
    assert client.upsert_issue_comment.await_count == 0


@then("the dispatcher upserted exactly one refusal comment")
def dispatcher_refusal_comment(dispatch_outcome: dict) -> None:
    client = dispatch_outcome["client"]
    assert client.upsert_issue_comment.await_count == 1


@then("the dispatcher made no branch or pull-request writes")
def dispatcher_no_branch_pr(dispatch_outcome: dict) -> None:
    client = dispatch_outcome["client"]
    assert client.create_branch_ref.await_count == 0
    assert client.create_pull_request.await_count == 0


@then("the dispatcher upserted at least one progress comment")
def dispatcher_progress_comment(dispatch_outcome: dict) -> None:
    client = dispatch_outcome["client"]
    assert client.upsert_issue_comment.await_count >= 1


@then("the dispatcher created a branch and opened a pull request")
def dispatcher_branch_and_pr(dispatch_outcome: dict) -> None:
    result = dispatch_outcome["result"]
    client = dispatch_outcome["client"]
    assert result["branch"]["created"] is True
    assert result["pull_request"]["action"] == "opened"
    assert result["pull_request"]["number"] == 999
    assert client.create_branch_ref.await_count == 1
    assert client.create_pull_request.await_count == 1


@then("the dispatcher posted a Codex review trigger")
def dispatcher_codex_trigger(dispatch_outcome: dict) -> None:
    client = dispatch_outcome["client"]
    assert client.create_issue_comment.await_count == 1
    assert client.create_issue_comment.await_args.kwargs["body"] == "@codex review"


@then("the dispatcher upserted progress comments on the issue and pull request")
def dispatcher_issue_and_pr_progress_comments(dispatch_outcome: dict) -> None:
    client = dispatch_outcome["client"]
    assert client.upsert_issue_comment.await_count == 2


@then(parsers.parse('the dispatcher result writeback_failures includes "{op}"'))
def dispatcher_failures_include(dispatch_outcome: dict, op: str) -> None:
    failures = dispatch_outcome["result"]["writeback_failures"]
    assert any(f["operation"] == op for f in failures), f"expected {op} in failures, got {failures}"


@then("the dispatcher result writeback_failures is empty")
def dispatcher_failures_empty(dispatch_outcome: dict) -> None:
    assert dispatch_outcome["result"]["writeback_failures"] == []


@then("the Assembly route is denied")
def route_denied(filter_outcome: dict) -> None:
    assert filter_outcome["denied"], "expected denied list to be non-empty"
    assert not filter_outcome["allowed"], "expected allowed list to be empty"


@then("the dispatcher is never called")
def dispatcher_not_called(filter_outcome: dict) -> None:
    # No dispatcher invocation in this scenario — the assertion is the
    # absence of a dispatch_outcome fixture. We confirm the precondition.
    assert filter_outcome["allowed"] == []
