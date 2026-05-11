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
    Then the marker starts with "swm-thread-conclusion:PRRT_abc123:deadbeef1234"

  Scenario: close_reason_marker encodes thread id and head sha prefix
    Given a thread with id "PRRT_abc123" and head sha "deadbeef1234567890"
    When close_reason_marker is called
    Then the close reason marker starts with "swm-close-reason:PRRT_abc123:deadbeef1234"

  Scenario: existing_conclusion_markers for RESOLVED thread returns close-reason marker
    Given a RESOLVED thread with id "PRRT_res" and head sha "abc1234def56"
    When existing_conclusion_markers is called
    Then the markers list contains the close-reason marker

  Scenario: existing_conclusion_markers for OPEN thread returns conclusion marker
    Given an OPEN thread with id "PRRT_open" and head sha "abc1234def56"
    When existing_conclusion_markers is called
    Then the markers list contains the conclusion marker

  # ---------------------------------------------------------------------------
  # has_flash_close_reason
  # ---------------------------------------------------------------------------

  Scenario: has_flash_close_reason is true when thread has llm_reason
    Given a thread with llm_reason "diff removes token logging"
    When has_flash_close_reason is called with no snapshot
    Then the flash close reason result is true

  Scenario: has_flash_close_reason is false when neither thread nor snapshot has llm_reason
    Given a thread with no llm_reason
    When has_flash_close_reason is called with no snapshot
    Then the flash close reason result is false

  # ---------------------------------------------------------------------------
  # build_thread_conclusion_comment — RESOLVED
  # ---------------------------------------------------------------------------

  Scenario: RESOLVED comment contains verdict and close-reason marker
    Given a RESOLVED thread with a verdict_reason "author reply substantive"
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "RESOLVED"
    And the comment contains "swm-close-reason"
    And the comment contains "The conversation can be resolved now."

  Scenario: OPEN comment contains conclusion marker and left-open message
    Given an OPEN thread with verdict_reason "non-substantive reply"
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "OPEN"
    And the comment contains "swm-thread-conclusion"
    And the comment contains "left open"

  Scenario: Comment with llm_reason uses flash verifier label
    Given a RESOLVED thread with llm_reason "diff clears the issue" and llm_confidence 0.92
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "OpenClaw Flash"
    And the comment contains "0.92"

  Scenario: Comment without llm_reason uses deterministic verifier label
    Given a RESOLVED thread with a verdict_reason "author reply substantive"
    When build_thread_conclusion_comment is called with head_sha "abc1234def56"
    Then the comment contains "SWM deterministic verifier"

  # ---------------------------------------------------------------------------
  # build_close_reason_comment — delegates to build_thread_conclusion_comment
  # ---------------------------------------------------------------------------

  Scenario: build_close_reason_comment produces same output as build_thread_conclusion_comment
    Given a RESOLVED thread with a verdict_reason "author reply substantive"
    When both close_reason and conclusion comments are built with the same inputs
    Then the close_reason comment equals the conclusion comment
