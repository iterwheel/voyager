Feature: Countdown resolve-loop safety contract
  Countdown resolves GitHub review threads only when the deterministic prefilter
  and fail-closed gate both say the action is safe.

  Scenario: Gate veto keeps the thread unresolved
    Given a Countdown candidate thread
    And the Countdown gate vetoes with reason "not addressed"
    When the Countdown resolve loop runs
    Then no Countdown resolve mutation occurs
    And Countdown records decision action "vetoed"

  Scenario: Resolve cap stops before another gate call
    Given three Countdown candidate threads
    And the Countdown gate approves all candidates
    When the Countdown resolve loop runs with max_resolves 2
    Then Countdown records 2 resolve mutations
    And Countdown records the run as capped
    And the Countdown gate was called 2 times

  Scenario: Dry-run records would_resolve without mutating
    Given a Countdown candidate thread
    And the Countdown gate approves all candidates
    When the Countdown resolve loop runs in dry-run mode
    Then no Countdown resolve mutation occurs
    And Countdown records decision action "would_resolve"

  Scenario: Outdated thread is still resolvable
    Given an outdated Countdown candidate thread
    And the Countdown gate approves all candidates
    When the Countdown resolve loop runs
    Then Countdown records decision action "resolved"
    And Countdown records 1 resolve mutation

  Scenario: Non-machine identity is refused before resolving
    Given a Countdown candidate thread
    And the Countdown gate approves all candidates
    And the Countdown resolver identity is "ryosaeba1985"
    When the Countdown resolve loop is attempted through the real resolver path
    Then Countdown refuses the run before resolving
    And the Countdown gate was called 0 times

  Scenario: Comment-count race skips stale candidate
    Given a Countdown candidate thread with 1 fetched comment
    And the Countdown live comment count is 2
    And the Countdown gate approves all candidates
    When the Countdown resolve loop runs
    Then no Countdown resolve mutation occurs
    And Countdown records decision action "skipped_stale"
