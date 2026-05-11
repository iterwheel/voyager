"""Step definitions for SWM investigator BDD scenarios."""

from __future__ import annotations

import asyncio
import json

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/swm_investigator.feature")


def _make_input():
    from voyager.bots.clearance.investigator import ThreadInvestigationInput

    return ThreadInvestigationInput(
        repo="owner/repo",
        pr=7,
        pr_title="Fix bug",
        head_sha="abc123",
        path="app.py",
        line=12,
        classification="C",
        codex_comment_body="This leaks the token.",
        author_reply_body="Fixed in app.py by removing token logging.",
        diff_excerpt="diff --git a/app.py b/app.py\n- print(token)\n+ logger.info('ok')\n",
        heuristic_verdict="OPEN",
        heuristic_reason="author reply was too short",
    )


def _ok_response(verdict: str = "RESOLVED", confidence: float = 0.91) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "reason": "diff removes token logging",
            "evidence": ["print(token) removed"],
        }
    )


class _StubDeepSeekClient:
    """Minimal stub for DeepSeekClient — returns pre-set content or raises.

    Also captures the messages and thinking flag of the last call so tests
    can assert that ``thinking=True`` was forwarded and that the system
    prompt carries the output schema (GLM M3 review flag — the original
    stub silently accepted any ``**kwargs`` and was unobservable).
    """

    def __init__(
        self,
        *,
        response_text: str | None = None,
        reasoning_text: str | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._response_text = response_text
        self._reasoning_text = reasoning_text
        self._raise_exc = raise_exc
        self._model = "stub-model"
        self.last_messages: list | None = None
        self.last_thinking: bool | None = None

    async def complete(self, messages, *, thinking=True, **kwargs):
        from voyager.llm.deepseek import AssistantTurn

        self.last_messages = list(messages)
        self.last_thinking = thinking
        if self._raise_exc is not None:
            raise self._raise_exc
        return AssistantTurn(
            content=self._response_text,
            reasoning_content=self._reasoning_text,
        )


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------


@given(
    parsers.parse("raw text '{text}'"),
    target_fixture="raw_text",
)
def raw_text_plain(text: str) -> str:
    return text


@given(
    parsers.parse('raw text with a json fenced block containing verdict "{verdict}"'),
    target_fixture="raw_text",
)
def raw_text_fenced(verdict: str) -> str:
    return f'```json\n{{"verdict":"{verdict}","confidence":0.9,"reason":"ok","evidence":[]}}\n```'


@given(
    parsers.parse('raw text containing two JSON objects with verdicts "{first}" and "{second}"'),
    target_fixture="raw_text",
)
def raw_text_two_objects(first: str, second: str) -> str:
    return (
        f'{{"verdict":"{first}","confidence":0.9,"reason":"first","evidence":[]}}\n'
        f'{{"verdict":"{second}","confidence":0.4,"reason":"second","evidence":[]}}'
    )


@given(
    "raw text where reasoning includes an unrelated brace pair before the verdict JSON",
    target_fixture="raw_text",
)
def raw_text_with_brace_pair_preamble() -> str:
    return (
        "Considering the diff sample {a:1, b:2}, the fix addresses the concern.\n"
        '{"verdict":"RESOLVED","confidence":0.9,"reason":"diff addresses concern","evidence":[]}'
    )


@given(
    'raw text where reasoning quotes the author saying "I fixed it." before the verdict JSON',
    target_fixture="raw_text",
)
def raw_text_with_stray_quote_preamble() -> str:
    """Round-2 regression: a stray double-quote in preamble used to flip the
    walker into in_string=True forever, silently swallowing the real JSON.
    """
    return (
        'The author said "I fixed it." Here is the verdict.\n'
        '{"verdict":"RESOLVED","confidence":0.9,"reason":"author confirmed fix","evidence":[]}'
    )


@given(
    "raw text with no fenced block but evidence containing inline backticks",
    target_fixture="raw_text",
)
def raw_text_with_inline_backticks() -> str:
    """Round-2 regression (DeepSeek): the fence-strip step ran first and would
    mangle valid JSON whose evidence string contained literal backticks. The
    new order tries direct json.loads first, so this round-trips intact.
    """
    return (
        '{"verdict":"RESOLVED","confidence":0.9,"reason":"diff removes logging",'
        '"evidence":["`print(token)` removed from app.py"]}'
    )


@then(parsers.parse('the extracted dict has evidence containing "{token}"'))
def extracted_evidence_contains(extracted: dict, token: str) -> None:
    evidence = extracted.get("evidence") or []
    assert any(token in item for item in evidence), f"{token!r} not in evidence: {evidence!r}"


@when("_extract_json_object is called", target_fixture="extracted")
def call_extract_json_object(raw_text: str) -> dict:
    from voyager.bots.clearance.investigator import _extract_json_object

    return _extract_json_object(raw_text)


@then(parsers.parse('the extracted dict has verdict "{verdict}"'))
def extracted_has_verdict(extracted: dict, verdict: str) -> None:
    assert extracted["verdict"] == verdict


# ---------------------------------------------------------------------------
# _coerce_decision
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a raw decision dict with verdict "{verdict}" confidence {conf:f}'),
    target_fixture="raw_decision",
)
def raw_decision_dict(verdict: str, conf: float) -> dict:
    return {"verdict": verdict, "confidence": conf, "reason": "some reason", "evidence": ["e1"]}


@given(
    parsers.parse(
        'a raw decision dict with verdict "{verdict}" confidence {conf:f} and empty reason'
    ),
    target_fixture="raw_decision",
)
def raw_decision_empty_reason(verdict: str, conf: float) -> dict:
    return {"verdict": verdict, "confidence": conf, "reason": "", "evidence": []}


@when(
    parsers.parse("_coerce_decision is called with min_confidence {min_conf:f}"),
    target_fixture="coerce_result",
)
def call_coerce_decision(raw_decision: dict, min_conf: float):
    from voyager.bots.clearance.investigator import InvestigationError, _coerce_decision

    try:
        return {"decision": _coerce_decision(raw_decision, min_confidence=min_conf, raw_text="{}")}
    except InvestigationError as e:
        return {"error": e}


@then(parsers.parse('the decision verdict is "{verdict}"'))
def decision_verdict(coerce_result, verdict: str) -> None:
    assert coerce_result["decision"].verdict == verdict


@then(parsers.parse("the decision confidence is {conf:f}"))
def decision_confidence(coerce_result, conf: float) -> None:
    assert abs(coerce_result["decision"].confidence - conf) < 1e-6


@then("a coerce InvestigationError is raised")
def coerce_investigation_error_raised(coerce_result) -> None:
    assert "error" in coerce_result, f"Expected error but got: {coerce_result}"


# ---------------------------------------------------------------------------
# DeepSeekInvestigator.investigate
# ---------------------------------------------------------------------------


@given(
    parsers.parse("a DeepSeekClient stub that returns a RESOLVED verdict with confidence {conf:f}"),
    target_fixture="investigator",
)
def stub_client_resolved(conf: float):
    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    stub = _StubDeepSeekClient(response_text=_ok_response("RESOLVED", conf))
    return DeepSeekInvestigator(client=stub, min_confidence=0.8)


@given("a DeepSeekClient stub that raises an exception", target_fixture="investigator")
def stub_client_raises():
    """Use httpx.HTTPError so the narrow except in investigate() catches it.

    The investigator now only wraps integration-layer exceptions
    (httpx.HTTPError, openai.APIError, ValueError) into InvestigationError;
    a generic RuntimeError would propagate to expose programming errors
    (DeepSeek M2 review flag).
    """
    import httpx

    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    stub = _StubDeepSeekClient(raise_exc=httpx.HTTPError("simulated network error"))
    return DeepSeekInvestigator(client=stub)


@given(
    "a DeepSeekClient stub that returns reasoning_content but no content",
    target_fixture="investigator",
)
def stub_client_reasoning_only():
    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    stub = _StubDeepSeekClient(
        response_text=None,
        reasoning_text="Considering several angles before deciding...",
    )
    return DeepSeekInvestigator(client=stub)


@given(
    parsers.parse(
        "a DeepSeekClient stub that returns a RESOLVED verdict with confidence "
        '{conf:f} and reasoning "{reasoning}"'
    ),
    target_fixture="investigator",
)
def stub_client_with_reasoning(conf: float, reasoning: str):
    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    stub = _StubDeepSeekClient(
        response_text=_ok_response("RESOLVED", conf),
        reasoning_text=reasoning,
    )
    return DeepSeekInvestigator(client=stub, min_confidence=0.8)


@given(
    "a recording DeepSeekClient stub that returns a RESOLVED verdict",
    target_fixture="investigator",
)
def recording_stub():
    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    stub = _StubDeepSeekClient(response_text=_ok_response("RESOLVED", 0.9))
    return DeepSeekInvestigator(client=stub, min_confidence=0.8)


@given("a DeepSeekClient stub that returns garbled non-JSON text", target_fixture="investigator")
def stub_client_bad_json():
    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    stub = _StubDeepSeekClient(response_text="this is not JSON at all")
    return DeepSeekInvestigator(client=stub)


@given(
    "a DeepSeekClient stub that returns a RESOLVED verdict with confidence 0.5",
    target_fixture="investigator",
)
def stub_client_low_confidence():
    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    stub = _StubDeepSeekClient(response_text=_ok_response("RESOLVED", 0.5))
    return DeepSeekInvestigator(client=stub, min_confidence=0.8)


@given("an investigation input for a state C thread", target_fixture="inv_input")
def investigation_input_state_c():
    return _make_input()


@when("DeepSeekInvestigator.investigate is awaited", target_fixture="inv_result")
def call_investigate(investigator, inv_input):
    """Drive the async investigator from a sync pytest-bdd step.

    Use ``asyncio.run`` rather than ``asyncio.get_event_loop().run_until_complete``
    — the latter has been deprecated since Python 3.10 and is being removed
    in 3.14 (project target). ``asyncio.run`` creates and tears down a fresh
    event loop per call, which is exactly what we want for an isolated test
    step. GLM-5.1 H4 review flag.
    """
    from voyager.bots.clearance.investigator import InvestigationError

    try:
        result = asyncio.run(investigator.investigate(inv_input))
        return {"decision": result}
    except InvestigationError as e:
        return {"error": e}


@then(parsers.parse('the investigation verdict is "{verdict}"'))
def investigation_verdict(inv_result, verdict: str) -> None:
    assert inv_result["decision"].verdict == verdict


@then(parsers.parse("the investigation confidence is {conf:f}"))
def investigation_confidence(inv_result, conf: float) -> None:
    assert abs(inv_result["decision"].confidence - conf) < 1e-6


@then("an InvestigationError is raised from investigate")
def investigation_error_from_investigate(inv_result) -> None:
    assert "error" in inv_result, f"Expected InvestigationError but got: {inv_result}"


@then(parsers.parse('an InvestigationError mentioning "{text}" is raised from investigate'))
def investigation_error_mentions(inv_result, text: str) -> None:
    assert "error" in inv_result, f"Expected InvestigationError but got: {inv_result}"
    assert text in str(inv_result["error"]), (
        f"Expected {text!r} in error message: {inv_result['error']}"
    )


@then(parsers.parse('the decision raw_text contains "{text}"'))
def decision_raw_text_contains(inv_result, text: str) -> None:
    assert "decision" in inv_result, f"Expected decision but got: {inv_result}"
    raw_text = inv_result["decision"].raw_text or ""
    assert text in raw_text, f"Expected {text!r} in raw_text: {raw_text!r}"


@then("the client was called with thinking enabled")
def client_called_with_thinking(investigator) -> None:
    assert investigator._client.last_thinking is True, (
        f"thinking flag was {investigator._client.last_thinking!r}, expected True"
    )


@then("the system message contains the output schema")
def system_message_has_schema(investigator) -> None:
    messages = investigator._client.last_messages or []
    system = next((m for m in messages if m.role == "system"), None)
    assert system is not None, "no system message captured"
    assert "RESOLVED|OPEN|NEEDS_HUMAN_JUDGMENT" in (system.content or ""), (
        f"system prompt missing output schema: {system.content!r}"
    )


# ---------------------------------------------------------------------------
# build_investigator_from_env
# ---------------------------------------------------------------------------


@given("VOYAGER_INVESTIGATOR_ENABLED is not set", target_fixture="env_overrides")
def env_disabled(monkeypatch) -> dict:
    monkeypatch.delenv("VOYAGER_INVESTIGATOR_ENABLED", raising=False)
    return {}


@given(
    parsers.parse('VOYAGER_INVESTIGATOR_ENABLED is "1" and VOYAGER_DEEPSEEK_API_KEY is "{key}"'),
    target_fixture="env_overrides",
)
def env_enabled_with_key(monkeypatch, key: str) -> dict:
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_ENABLED", "1")
    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", key)
    monkeypatch.delenv("VOYAGER_INVESTIGATOR_MODEL", raising=False)
    return {"key": key}


@given(
    parsers.parse(
        'VOYAGER_INVESTIGATOR_ENABLED is "1" and VOYAGER_INVESTIGATOR_MODEL is "{model}" and VOYAGER_DEEPSEEK_API_KEY is "{key}"'
    ),
    target_fixture="env_overrides",
)
def env_enabled_with_model(monkeypatch, model: str, key: str) -> dict:
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_ENABLED", "1")
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_MODEL", model)
    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", key)
    return {"model": model, "key": key}


@given(
    'VOYAGER_INVESTIGATOR_ENABLED is "1" and VOYAGER_DEEPSEEK_API_KEY is missing',
    target_fixture="env_overrides",
)
def env_enabled_no_key(monkeypatch) -> dict:
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_ENABLED", "1")
    monkeypatch.delenv("VOYAGER_DEEPSEEK_API_KEY", raising=False)
    return {}


@when("build_investigator_from_env is called", target_fixture="factory_result")
def call_build_from_env():
    from voyager.bots.clearance.investigator import InvestigationError, build_investigator_from_env

    try:
        return {"investigator": build_investigator_from_env()}
    except InvestigationError as e:
        return {"error": e}


@then("the result is None")
def factory_result_none(factory_result) -> None:
    assert factory_result["investigator"] is None


@then("the result is a DeepSeekInvestigator")
def factory_result_is_investigator(factory_result) -> None:
    from voyager.bots.clearance.investigator import DeepSeekInvestigator

    assert isinstance(factory_result["investigator"], DeepSeekInvestigator)


@then(parsers.parse('the investigator model is "{model}"'))
def investigator_model_is(factory_result, model: str) -> None:
    investigator = factory_result["investigator"]
    assert investigator._client._model == model


@then("a factory InvestigationError is raised")
def factory_investigation_error(factory_result) -> None:
    assert "error" in factory_result, f"Expected InvestigationError but got: {factory_result}"
