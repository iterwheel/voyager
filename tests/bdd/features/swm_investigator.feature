Feature: SWM investigator — DeepSeek-backed thread verdict investigation

  As the voyager clearance bot
  I want to investigate Codex review thread verdicts using DeepSeek
  So that ambiguous verdicts get LLM semantic verification instead of subprocess calls

  # ---------------------------------------------------------------------------
  # _extract_json_object helper
  # ---------------------------------------------------------------------------

  Scenario: Extract plain JSON object from text
    Given raw text '{"verdict":"OPEN","confidence":0.8,"reason":"not fixed","evidence":[]}'
    When _extract_json_object is called
    Then the extracted dict has verdict "OPEN"

  Scenario: Extract JSON from fenced code block
    Given raw text with a json fenced block containing verdict "RESOLVED"
    When _extract_json_object is called
    Then the extracted dict has verdict "RESOLVED"

  Scenario: Extract first JSON object when model emits two
    Given raw text containing two JSON objects with verdicts "RESOLVED" and "OPEN"
    When _extract_json_object is called
    Then the extracted dict has verdict "RESOLVED"

  Scenario: Extract JSON from reasoning preamble with brace pair
    Given raw text where reasoning includes an unrelated brace pair before the verdict JSON
    When _extract_json_object is called
    Then the extracted dict has verdict "RESOLVED"

  Scenario: Extract JSON from reasoning preamble that contains a stray double-quote
    Given raw text where reasoning quotes the author saying "I fixed it." before the verdict JSON
    When _extract_json_object is called
    Then the extracted dict has verdict "RESOLVED"

  Scenario: Extract JSON whose evidence string contains literal backticks
    Given raw text with no fenced block but evidence containing inline backticks
    When _extract_json_object is called
    Then the extracted dict has verdict "RESOLVED"
    And the extracted dict has evidence containing "`print(token)`"

  # ---------------------------------------------------------------------------
  # _coerce_decision helper
  # ---------------------------------------------------------------------------

  Scenario: coerce_decision accepts valid RESOLVED with high confidence
    Given a raw decision dict with verdict "RESOLVED" confidence 0.91
    When _coerce_decision is called with min_confidence 0.8
    Then the decision verdict is "RESOLVED"
    And the decision confidence is 0.91

  Scenario: coerce_decision demotes low-confidence RESOLVED to NEEDS_HUMAN_JUDGMENT
    Given a raw decision dict with verdict "RESOLVED" confidence 0.5
    When _coerce_decision is called with min_confidence 0.8
    Then the decision verdict is "NEEDS_HUMAN_JUDGMENT"
    And the decision confidence is 0.5

  Scenario: coerce_decision raises InvestigationError for invalid verdict
    Given a raw decision dict with verdict "UNKNOWN" confidence 0.9
    When _coerce_decision is called with min_confidence 0.8
    Then a coerce InvestigationError is raised

  Scenario: coerce_decision raises InvestigationError for empty reason
    Given a raw decision dict with verdict "OPEN" confidence 0.9 and empty reason
    When _coerce_decision is called with min_confidence 0.8
    Then a coerce InvestigationError is raised

  # ---------------------------------------------------------------------------
  # DeepSeekInvestigator.investigate — happy path
  # ---------------------------------------------------------------------------

  Scenario: DeepSeekInvestigator returns RESOLVED decision from successful LLM response
    Given a DeepSeekClient stub that returns a RESOLVED verdict with confidence 0.91
    And an investigation input for a state C thread
    When DeepSeekInvestigator.investigate is awaited
    Then the investigation verdict is "RESOLVED"
    And the investigation confidence is 0.91

  Scenario: DeepSeekInvestigator wraps LLM errors as InvestigationError
    Given a DeepSeekClient stub that raises an exception
    And an investigation input for a state C thread
    When DeepSeekInvestigator.investigate is awaited
    Then an InvestigationError is raised from investigate

  Scenario: DeepSeekInvestigator raises InvestigationError when JSON is unparseable
    Given a DeepSeekClient stub that returns garbled non-JSON text
    And an investigation input for a state C thread
    When DeepSeekInvestigator.investigate is awaited
    Then an InvestigationError is raised from investigate

  Scenario: DeepSeekInvestigator demotes RESOLVED below min_confidence
    Given a DeepSeekClient stub that returns a RESOLVED verdict with confidence 0.5
    And an investigation input for a state C thread
    When DeepSeekInvestigator.investigate is awaited
    Then the investigation verdict is "NEEDS_HUMAN_JUDGMENT"

  Scenario: DeepSeekInvestigator raises diagnostic when only reasoning is returned
    Given a DeepSeekClient stub that returns reasoning_content but no content
    And an investigation input for a state C thread
    When DeepSeekInvestigator.investigate is awaited
    Then an InvestigationError mentioning "reasoning-only response" is raised from investigate

  Scenario: DeepSeekInvestigator records reasoning in audit raw_text
    Given a DeepSeekClient stub that returns a RESOLVED verdict with confidence 0.91 and reasoning "the diff removes token logging"
    And an investigation input for a state C thread
    When DeepSeekInvestigator.investigate is awaited
    Then the decision raw_text contains "the diff removes token logging"

  Scenario: DeepSeekInvestigator invokes the client with thinking enabled
    Given a recording DeepSeekClient stub that returns a RESOLVED verdict
    And an investigation input for a state C thread
    When DeepSeekInvestigator.investigate is awaited
    Then the client was called with thinking enabled
    And the system message contains the output schema

  # ---------------------------------------------------------------------------
  # build_investigator_from_env
  # ---------------------------------------------------------------------------

  Scenario: build_investigator_from_env returns None when disabled
    Given VOYAGER_INVESTIGATOR_ENABLED is not set
    When build_investigator_from_env is called
    Then the result is None

  Scenario: build_investigator_from_env returns DeepSeekInvestigator when enabled
    Given VOYAGER_INVESTIGATOR_ENABLED is "1" and VOYAGER_DEEPSEEK_API_KEY is "test-key"
    When build_investigator_from_env is called
    Then the result is a DeepSeekInvestigator

  Scenario: build_investigator_from_env uses VOYAGER_INVESTIGATOR_MODEL env var
    Given VOYAGER_INVESTIGATOR_ENABLED is "1" and VOYAGER_INVESTIGATOR_MODEL is "deepseek-v4-flash" and VOYAGER_DEEPSEEK_API_KEY is "test-key"
    When build_investigator_from_env is called
    Then the investigator model is "deepseek-v4-flash"

  Scenario: build_investigator_from_env defaults model to deepseek-v4-pro
    Given VOYAGER_INVESTIGATOR_ENABLED is "1" and VOYAGER_DEEPSEEK_API_KEY is "test-key"
    When build_investigator_from_env is called
    Then the investigator model is "deepseek-v4-pro"

  Scenario: build_investigator_from_env raises when enabled but API key missing
    Given VOYAGER_INVESTIGATOR_ENABLED is "1" and VOYAGER_DEEPSEEK_API_KEY is missing
    When build_investigator_from_env is called
    Then a factory InvestigationError is raised
