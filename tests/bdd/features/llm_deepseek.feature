Feature: DeepSeek LLM adapter — reasoning, tool calls, and error handling

  As the Iterwheel bot platform
  I want a DeepSeek adapter that wraps the OpenAI-compatible DeepSeek API
  So that bots can reason with thinking mode, call tools, and handle multi-turn conversations reliably

  Background:
    Given a DeepSeekClient with api_key "sk-test-key" and model "deepseek-v4-pro"

  # ---------------------------------------------------------------------------
  # Auth header
  # ---------------------------------------------------------------------------

  Scenario: Bearer token is sent in every request
    Given the DeepSeek API returns a thinking-enabled response
    When complete is called with a user message "What is the capital of France?"
    Then the request Authorization header is "Bearer sk-test-key"

  # ---------------------------------------------------------------------------
  # Client configuration
  # ---------------------------------------------------------------------------

  Scenario: base_url is sent to the DeepSeek endpoint
    Given the DeepSeek API returns a thinking-enabled response
    When complete is called with a user message "ping"
    Then the request was sent to a URL containing "api.deepseek.com"

  Scenario: model name is included in the request body
    Given the DeepSeek API returns a thinking-enabled response
    When complete is called with a user message "ping"
    Then the request body model is "deepseek-v4-pro"

  # ---------------------------------------------------------------------------
  # Thinking enabled — response shape
  # ---------------------------------------------------------------------------

  Scenario: Thinking enabled returns reasoning_content alongside content
    Given the DeepSeek API returns a thinking-enabled response
    When complete is called with thinking enabled
    Then the AssistantTurn has a non-empty reasoning_content
    And the AssistantTurn has a non-empty content
    And the AssistantTurn has no tool_calls

  Scenario: Thinking enabled sends correct extra_body thinking flag
    Given the DeepSeek API returns a thinking-enabled response
    When complete is called with thinking enabled
    Then the request extra_body thinking type is "enabled"

  # ---------------------------------------------------------------------------
  # Thinking disabled — response shape
  # ---------------------------------------------------------------------------

  Scenario: Thinking disabled returns content only, no reasoning_content
    Given the DeepSeek API returns a thinking-disabled response
    When complete is called with thinking disabled
    Then the AssistantTurn has a non-empty content
    And the AssistantTurn reasoning_content is None

  Scenario: Thinking disabled sends correct extra_body thinking flag
    Given the DeepSeek API returns a thinking-disabled response
    When complete is called with thinking disabled
    Then the request extra_body thinking type is "disabled"

  # ---------------------------------------------------------------------------
  # reasoning_effort variants
  # ---------------------------------------------------------------------------

  Scenario: reasoning_effort high is forwarded in extra_body
    Given the DeepSeek API returns a reasoning_effort_high response
    When complete is called with thinking enabled and reasoning_effort "high"
    Then the request extra_body reasoning_effort is "high"
    And the AssistantTurn has a non-empty reasoning_content

  Scenario: reasoning_effort max is forwarded in extra_body
    Given the DeepSeek API returns a reasoning_effort_max response
    When complete is called with thinking enabled and reasoning_effort "max"
    Then the request extra_body reasoning_effort is "max"
    And the AssistantTurn has a non-empty reasoning_content

  # ---------------------------------------------------------------------------
  # Multi-turn — reasoning_content must be included (V4 rule)
  # ---------------------------------------------------------------------------

  Scenario: Multi-turn with prior reasoning_content succeeds
    Given the DeepSeek API returns a multi-turn response
    When complete is called with a prior assistant turn carrying reasoning_content
    Then the request messages include an assistant message with reasoning_content
    And the AssistantTurn has a non-empty content

  Scenario: Multi-turn without prior reasoning_content triggers a 400 error
    Given the DeepSeek API returns a 400 error for missing reasoning_content
    When complete is called with a prior assistant turn missing reasoning_content
    Then an error is raised mentioning "400"

  # ---------------------------------------------------------------------------
  # Tool calls with thinking
  # ---------------------------------------------------------------------------

  Scenario: Tool call response with thinking returns reasoning_content and tool_calls
    Given the DeepSeek API returns a tool-call response with thinking
    When complete is called with thinking enabled and a tool definition
    Then the AssistantTurn has a non-empty reasoning_content
    And the AssistantTurn has tool_calls
    And the first tool_call name is "post_pr_comment"
    And the first tool_call id is "call_abc123"
    And the first tool_call arguments are parsed as a dict

  Scenario: Tool call extra_body is sent with thinking enabled when tools are provided
    Given the DeepSeek API returns a tool-call response with thinking
    When complete is called with thinking enabled and a tool definition
    Then the request extra_body thinking type is "enabled"
    And the request body includes tools

  # ---------------------------------------------------------------------------
  # Tool result message in subsequent turn
  # ---------------------------------------------------------------------------

  Scenario: Tool result role message is accepted in next complete call
    Given the DeepSeek API returns a tool-result follow-up response
    When complete is called with a tool result message in the history
    Then the request messages include a tool role message with tool_call_id "call_abc123"
    And the AssistantTurn has a non-empty content

  # ---------------------------------------------------------------------------
  # Error handling
  # ---------------------------------------------------------------------------

  Scenario: 401 Unauthorized surfaces an error
    Given the DeepSeek API returns a 401 response
    When complete is called with a user message "ping"
    Then an error is raised mentioning "401"

  Scenario: 429 Rate limit surfaces an error
    Given the DeepSeek API returns a 429 response
    When complete is called with a user message "ping"
    Then an error is raised mentioning "429"

  Scenario: 500 Server error surfaces an error
    Given the DeepSeek API returns a 500 response
    When complete is called with a user message "ping"
    Then an error is raised mentioning "500"

  Scenario: Request timeout surfaces an error
    Given the DeepSeek API raises a timeout
    When complete is called with a user message "ping"
    Then a timeout error is raised

  Scenario: Malformed JSON in response surfaces an error
    Given the DeepSeek API returns malformed JSON
    When complete is called with a user message "ping"
    Then a JSON decode error is raised
