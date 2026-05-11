Feature: SWM judge — verdict assignment per SWM-1101 decision tree

  As the voyager clearance bot
  I want to assign verdicts to Codex review threads using the SWM-1101 decision tree
  So that thread resolution is deterministic and auditable

  # ---------------------------------------------------------------------------
  # State B — outdated thread
  # ---------------------------------------------------------------------------

  Scenario: State B with code change resolves the thread
    Given a state B thread with code_changed true
    When the thread is judged
    Then the verdict is "RESOLVED"
    And the reason mentions "outdated"

  Scenario: State B without matching code change stays OPEN per SWM-1101 step 3
    Given a state B thread with code_changed false
    When the thread is judged
    Then the verdict is "OPEN"
    And the reason mentions "outdated by unrelated edit"

  # ---------------------------------------------------------------------------
  # State C — author replied
  # ---------------------------------------------------------------------------

  Scenario: State C with substantive reply resolves the thread
    Given a state C thread with a substantive author reply
    When the thread is judged
    Then the verdict is "RESOLVED"
    And the decision substantive flag is true

  Scenario: State C with short ack reply leaves thread open
    Given a state C thread with a short ack reply "thanks!"
    When the thread is judged
    Then the verdict is "OPEN"
    And the decision substantive flag is false

  Scenario: State C with long reply but no concrete identifier leaves thread open
    Given a state C thread with a long vague reply
    When the thread is judged
    Then the verdict is "OPEN"

  # ---------------------------------------------------------------------------
  # State A — no response
  # ---------------------------------------------------------------------------

  Scenario: State A with no response leaves thread open
    Given a state A thread with no author reply and no code change
    When the thread is judged
    Then the verdict is "OPEN"

  # ---------------------------------------------------------------------------
  # Codex follow-up overrides
  # ---------------------------------------------------------------------------

  Scenario: Positive Codex follow-up overrides non-substantive reply to RESOLVED
    Given a state C thread with a short reply and a positive Codex follow-up
    When the thread is judged
    Then the verdict is "RESOLVED"

  Scenario: Negative Codex follow-up overrides substantive reply to OPEN
    Given a state C thread with a substantive reply and a negative Codex follow-up
    When the thread is judged
    Then the verdict is "OPEN"

  # ---------------------------------------------------------------------------
  # GitHub isResolved override
  # ---------------------------------------------------------------------------

  Scenario: github_isResolved true overrides everything to RESOLVED
    Given a state A thread with no response but github_isResolved true
    When the thread is judged
    Then the verdict is "RESOLVED"
    And the reason mentions "GitHub"

  Scenario: github_isResolved false does not change state A logic
    Given a state A thread with no response and github_isResolved false
    When the thread is judged
    Then the verdict is "OPEN"

  # ---------------------------------------------------------------------------
  # is_substantive_reply helper
  # ---------------------------------------------------------------------------

  Scenario: Long reply citing a commit SHA is substantive
    Given a reply body with commit SHA "c476c877" and sufficient length
    When is_substantive_reply is called
    Then the substantive result is true

  Scenario: Short reply is not substantive
    Given a short reply body "ok"
    When is_substantive_reply is called
    Then the substantive result is false

  Scenario: None reply is not substantive
    Given a None reply body
    When is_substantive_reply is called
    Then the substantive result is false

  # ---------------------------------------------------------------------------
  # codex_followup_reaction helper
  # ---------------------------------------------------------------------------

  Scenario: "Looks good" in Codex follow-up is positive
    Given a Codex follow-up body "Looks good, no new issues."
    When codex_followup_reaction is called
    Then the followup reaction is "positive"

  Scenario: "Concern remains" in Codex follow-up is negative
    Given a Codex follow-up body "Concern remains: migration path still missing."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: "not addressed" must outrank the positive substring "addressed"
    Given a Codex follow-up body "This concern is not addressed in the new diff."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: "still not resolved" must outrank the positive substring "resolved"
    Given a Codex follow-up body "The race condition is still not resolved at HEAD."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Empty Codex follow-up returns None
    Given a None Codex follow-up body
    When codex_followup_reaction is called
    Then the followup reaction is None
