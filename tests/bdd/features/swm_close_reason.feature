Feature: SWM close_reason — review-thread conclusion comment rendering

  As the voyager clearance bot
  I want to render standardised GitHub comments for Codex review thread conclusions
  So that thread verdicts are consistently communicated to authors

  # ---------------------------------------------------------------------------
  # Marker helpers
  # ---------------------------------------------------------------------------

  Scenario: conclusion_marker encodes thread id and head sha prefix
    Given a thread with id "PRRT_abc123" and head sha "deadbeef1234567890"
    When conclusion_marker is called
    Then the marker starts with "clearance-thread-conclusion:PRRT_abc123:deadbeef1234"

  Scenario: close_reason_marker encodes thread id and head sha prefix
    Given a thread with id "PRRT_abc123" and head sha "deadbeef1234567890"
    When close_reason_marker is called
    Then the close reason marker starts with "clearance-close-reason:PRRT_abc123:deadbeef1234"

  Scenario: existing_conclusion_markers for RESOLVED thread returns close-reason marker
    Given a RESOLVED thread with id "PRRT_res" and head sha "abc1234def56"
    When existing_conclusion_markers is called
    Then the markers list contains the close-reason marker

  Scenario: existing_conclusion_markers for OPEN thread returns conclusion marker
    Given an OPEN thread with id "PRRT_open" and head sha "abc1234def56"
    When existing_conclusion_markers is called
    Then the markers list contains the conclusion marker

  # ---------------------------------------------------------------------------
  # has_llm_close_reason
  # ---------------------------------------------------------------------------

  Scenario: has_llm_close_reason is true when thread has llm_reason
    Given a thread with llm_reason "diff removes token logging"
    When has_llm_close_reason is called with no snapshot
    Then the llm close reason result is true

  Scenario: has_llm_close_reason is false when neither thread nor snapshot has llm_reason
    Given a thread with no llm_reason
    When has_llm_close_reason is called with no snapshot
    Then the llm close reason result is false

  # ---------------------------------------------------------------------------
  # build_thread_conclusion_comment — RESOLVED
  # ---------------------------------------------------------------------------

  Scenario: RESOLVED comment contains verdict and close-reason marker
    Given a RESOLVED thread with a verdict_reason "author reply substantive"
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "RESOLVED"
    And the comment contains "clearance-close-reason"
    And the comment contains "✅ **Clearance: resolved**"
    And the comment contains "✅ Action: conversation resolved"

  Scenario: OPEN comment contains conclusion marker and left-open message
    Given an OPEN thread with verdict_reason "non-substantive reply"
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "OPEN"
    And the comment contains "clearance-thread-conclusion"
    And the comment contains "👀 **Clearance: still open**"
    And the comment contains "⏳ Action: left open"

  Scenario: NEEDS_HUMAN_JUDGMENT comment contains compact human-judgment card
    Given a NEEDS_HUMAN_JUDGMENT thread with llm_reason "ambiguous evidence" and llm_confidence 0.63
    When build_thread_conclusion_comment is called with head_sha "abc1234def56" and model "deepseek-v4-flash"
    Then the comment contains "NEEDS_HUMAN_JUDGMENT"
    And the comment contains "clearance-thread-conclusion"
    And the comment contains "⚠️ **Clearance: needs human judgment**"
    And the comment contains "🧑 Action: left open for reviewer"

  Scenario: Comment with llm_reason and explicit model uses Clearance Investigator label
    Given a RESOLVED thread with llm_reason "diff clears the issue" and llm_confidence 0.92
    When build_thread_conclusion_comment is called with head_sha "abc1234def56" and model "deepseek-v4-pro"
    Then the comment contains "Clearance Investigator (`deepseek-v4-pro`)"
    And the comment contains "0.92"

  Scenario: Comment with llm_reason and no model still uses Clearance Investigator label
    Given a RESOLVED thread with llm_reason "diff clears the issue" and llm_confidence 0.92
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "Clearance Investigator"
    And the comment does not contain "deepseek-v4-pro"

  Scenario: Comment without llm_reason uses deterministic verifier label
    Given a RESOLVED thread with a verdict_reason "author reply substantive"
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "Clearance deterministic verifier"

  # ---------------------------------------------------------------------------
  # build_close_reason_comment — delegates to build_thread_conclusion_comment
  # ---------------------------------------------------------------------------

  Scenario: build_close_reason_comment produces same output as build_thread_conclusion_comment
    Given a RESOLVED thread with a verdict_reason "author reply substantive"
    When both close_reason and conclusion comments are built with the same inputs
    Then the close_reason comment equals the conclusion comment
