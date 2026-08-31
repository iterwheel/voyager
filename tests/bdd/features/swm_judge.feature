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

  Scenario: State C with substantive reply alone defers to human judgment (issue #253)
    Given a state C thread with a substantive author reply
    When the thread is judged
    Then the verdict is "NEEDS_HUMAN_JUDGMENT"
    And the reason mentions "uncorroborated"
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

  # Issue #249: negation-window coverage — phrasings the fixed token list missed

  Scenario: "has not been addressed" is negative (issue #249)
    Given a Codex follow-up body "This has not been addressed in the follow-up commit."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: "remains unresolved" is negative (issue #249)
    Given a Codex follow-up body "The race condition remains unresolved."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: "unaddressed" is negative (issue #249)
    Given a Codex follow-up body "The migration path is unaddressed."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: "hasn't been fixed" is negative (issue #249)
    Given a Codex follow-up body "The leak hasn't been fixed yet."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: unnegated positive still classifies positive (issue #249)
    Given a Codex follow-up body "Addressed in commit abc1234, the guard is in place."
    When codex_followup_reaction is called
    Then the followup reaction is "positive"

  Scenario: Sentiment-free follow-up returns None (issue #249)
    Given a Codex follow-up body "I re-ran the analysis on the current head."
    When codex_followup_reaction is called
    Then the followup reaction is None

  # Codex P1 rounds on #334/#335: negation attachment refinements

  Scenario: Hard negator directly after a positive token negates it
    Given a Codex follow-up body "Looks fixed, not verified at HEAD."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Bare "no further action" after a positive stays positive
    Given a Codex follow-up body "The leak is addressed. No further action needed."
    When codex_followup_reaction is called
    Then the followup reaction is "positive"

  Scenario: Negative pronoun "none" negates a positive
    Given a Codex follow-up body "None of the findings were addressed by this patch."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Bare "fixed" alone is not approval (negation-only token)
    Given a Codex follow-up body "I believe this got fixed."
    When codex_followup_reaction is called
    Then the followup reaction is None

  Scenario: Negated "fixed" still classifies negative
    Given a Codex follow-up body "The leak hasn't been fixed yet."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Questioned token answered with No is negative (Codex round 5)
    Given a Codex follow-up body "Fixed? No—the race persists."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Distant "no new issues" before a positive stays positive
    Given a Codex follow-up body "no new issues introduced, looks good"
    When codex_followup_reaction is called
    Then the followup reaction is "positive"

  Scenario: Close "no" before a token negates it
    Given a Codex follow-up body "no issues were addressed here"
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Trailing rejection clause is negative (Codex round 6)
    Given a Codex follow-up body "No new issues were introduced, but the original race persists."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Still-occurs phrasing is negative (Codex round 6)
    Given a Codex follow-up body "The race still occurs at HEAD."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Plain no-new-issues approval stays positive
    Given a Codex follow-up body "No new issues. Nice work!"
    When codex_followup_reaction is called
    Then the followup reaction is "positive"

  Scenario: Partial-resolution qualifiers are negative (Codex round 9)
    Given a Codex follow-up body "The concern is only partially addressed by this patch"
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Partially-resolved phrasing is negative (Codex round 9)
    Given a Codex follow-up body "Partially resolved: the leak is gone but the race remains"
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Still-looks-good approval stays positive (Codex round 10)
    Given a Codex follow-up body "This still looks good after retesting."
    When codex_followup_reaction is called
    Then the followup reaction is "positive"

  Scenario: Negator in a completed sentence does not negate later positives
    Given a Codex follow-up body "This isn't a regression. The concern is resolved."
    When codex_followup_reaction is called
    Then the followup reaction is "positive"

  Scenario: Adversative clause after a token negates it (Codex round 11)
    Given a Codex follow-up body "The symptom is addressed, but not the root cause."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Terminal question-No reply is negative (Codex round 12)
    Given a Codex follow-up body "Resolved? No."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Adversative clause ending in remains is negative (Codex round 12)
    Given a Codex follow-up body "The symptom is addressed, but the original race remains."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Longer adversative clauses still negate (Codex round 13)
    Given a Codex follow-up body "The symptom is addressed, but the original concurrency race remains."
    When codex_followup_reaction is called
    Then the followup reaction is "negative"

  Scenario: Empty Codex follow-up returns None
    Given a None Codex follow-up body
    When codex_followup_reaction is called
    Then the followup reaction is None
