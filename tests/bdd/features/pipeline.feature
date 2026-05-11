Feature: Rocket Factory Pipeline — orchestration state machine

  As the Iterwheel pipeline orchestrator
  I want to advance units of work through mission stages via typed signals
  So that each issue and PR progresses independently from Blueprint to Liftoff

  Background:
    Given the pipeline module is available

  # ---------------------------------------------------------------------------
  # Happy path — full forward progression
  # ---------------------------------------------------------------------------

  Scenario: Issue advances from BLUEPRINT_PENDING to BLUEPRINT_READY on blueprint-ready signal
    Given a pipeline state "state_blueprint_pending"
    And a pipeline signal "signal_blueprint_ready"
    When the signal is applied to the pipeline state
    Then the new stage is "blueprint_ready"
    And the transition is recorded in history

  Scenario: Issue advances from BLUEPRINT_READY to STACK_PENDING on stack-pending signal
    Given a pipeline state "state_blueprint_ready"
    And a pipeline signal "signal_stack_pending"
    When the signal is applied to the pipeline state
    Then the new stage is "stack_pending"
    And the transition is recorded in history

  Scenario: Full forward walk reaches LIFTOFF_DONE stage
    Given a fresh pipeline target "iterwheel/voyager#77"
    When the following signals are applied in order:
      | signal_kind         |
      | blueprint-ready     |
      | stack-classified    |
      | pr-opened           |
      | clearance-pending   |
      | clearance-ready     |
      | liftoff-done        |
    Then the final stage is "liftoff_done"
    And history contains 6 transitions

  # ---------------------------------------------------------------------------
  # Stage hold on revision — pipeline pauses, does not advance
  # ---------------------------------------------------------------------------

  Scenario: blueprint-revision signal from BLUEPRINT_PENDING holds the stage
    Given a pipeline state "state_blueprint_pending"
    And a pipeline signal "signal_blueprint_revision"
    When the signal is applied to the pipeline state
    Then the new stage is "blueprint_revision"
    And the transition is recorded in history

  Scenario: blueprint-ready signal after revision resumes progression
    Given a pipeline state "state_blueprint_pending"
    And a pipeline signal "signal_blueprint_revision"
    When the signal is applied to the pipeline state
    And the blueprint-ready signal is then applied
    Then the new stage is "blueprint_ready"

  # ---------------------------------------------------------------------------
  # Stage skip — no-blueprint-needed bypasses Blueprint stage
  # ---------------------------------------------------------------------------

  Scenario: no-blueprint-needed signal skips directly to STACK_PENDING
    Given a fresh pipeline target "iterwheel/voyager#50"
    And a pipeline signal "signal_no_blueprint_needed"
    When the signal is applied to the pipeline state
    Then the new stage is "stack_pending"
    And history contains 1 transitions

  # ---------------------------------------------------------------------------
  # Concurrent target isolation
  # ---------------------------------------------------------------------------

  Scenario: Two targets on the same repo maintain independent stages
    Given a pipeline state for target "iterwheel/voyager#10" at stage "blueprint_pending"
    And a pipeline state for target "iterwheel/voyager#11" at stage "stack_classified"
    When blueprint-ready signal is applied to target "iterwheel/voyager#10"
    Then target "iterwheel/voyager#10" stage is "blueprint_ready"
    And target "iterwheel/voyager#11" stage is still "stack_classified"

  # ---------------------------------------------------------------------------
  # Force restart — human-issued signal resets to a prior stage
  # ---------------------------------------------------------------------------

  Scenario: force-restart signal from STACK_CLASSIFIED resets to BLUEPRINT_PENDING
    Given a pipeline state "state_stack_classified"
    And a pipeline signal "signal_force_restart"
    When the signal is applied to the pipeline state
    Then the new stage is "blueprint_pending"
    And the transition is recorded in history

  # ---------------------------------------------------------------------------
  # Stale signal — signal for a stage already passed is a no-op
  # ---------------------------------------------------------------------------

  Scenario: blueprint-ready signal arriving after STACK_CLASSIFIED is a no-op
    Given a pipeline state "state_stack_classified"
    And a pipeline signal "signal_blueprint_ready"
    When the signal is applied to the pipeline state
    Then the stage is unchanged at "stack_classified"
    And the stale signal is rejected

  # ---------------------------------------------------------------------------
  # Unknown target — signal for unseen target_id initialises state
  # ---------------------------------------------------------------------------

  Scenario: Signal for an unknown target initialises a new pipeline state
    Given no existing pipeline state for target "iterwheel/voyager#999"
    And a pipeline signal "signal_blueprint_ready"
    When the signal is applied to the unknown target
    Then a new pipeline state is created for "iterwheel/voyager#999"
    And the new stage is "blueprint_ready"

  # ---------------------------------------------------------------------------
  # History capture
  # ---------------------------------------------------------------------------

  Scenario: Each accepted transition appends one history entry
    Given a pipeline state "state_blueprint_pending"
    And a pipeline signal "signal_blueprint_ready"
    When the signal is applied to the pipeline state
    Then history contains 1 transitions
    And the first history entry stage is "blueprint_pending"
    And the first history entry signal is "blueprint-ready"

  # ---------------------------------------------------------------------------
  # Idempotency — same signal applied twice does not double-advance
  # ---------------------------------------------------------------------------

  Scenario: Applying the same blueprint-ready signal twice is idempotent
    Given a pipeline state "state_blueprint_pending"
    And a pipeline signal "signal_blueprint_ready"
    When the signal is applied to the pipeline state
    And the same signal is applied again
    Then the new stage is "blueprint_ready"
    And history contains 1 transitions

  # ---------------------------------------------------------------------------
  # Rollback — clearance-blocked after CLEARANCE_READY rolls back
  # ---------------------------------------------------------------------------

  Scenario: clearance-blocked signal from CLEARANCE_READY rolls back to CLEARANCE_BLOCKED
    Given a pipeline state "state_clearance_ready"
    And a pipeline signal "signal_clearance_blocked"
    When the signal is applied to the pipeline state
    Then the new stage is "clearance_blocked"
    And the transition is recorded in history

  # ---------------------------------------------------------------------------
  # Cross-target signal contamination guard (Codex P1)
  # ---------------------------------------------------------------------------

  Scenario: Signal targeting a different target_id is ignored (no cross-target contamination)
    Given a fresh pipeline target "iterwheel/voyager#42"
    When a "blueprint-ready" signal for target "iterwheel/voyager#99" is applied
    Then the stage is unchanged at "blueprint_pending"
    And history contains 0 transitions

  # ---------------------------------------------------------------------------
  # force-restart input validation (Codex P2)
  # ---------------------------------------------------------------------------

  Scenario: force-restart with an invalid restart_to raises ValueError
    Given a pipeline state "state_stack_classified"
    And a malformed force-restart signal with restart_to "garbage_stage"
    When the signal is applied to the pipeline state and may raise
    Then a ValueError is raised mentioning "invalid restart_to"

  # ---------------------------------------------------------------------------
  # Clearance first-evaluation block from PENDING (Codex round 2)
  # ---------------------------------------------------------------------------

  Scenario: clearance-blocked signal from CLEARANCE_PENDING transitions directly to CLEARANCE_BLOCKED
    Given a fresh pipeline target "iterwheel/voyager#88"
    When the following signals are applied in order:
      | signal_kind         |
      | blueprint-ready     |
      | stack-classified    |
      | pr-opened           |
      | clearance-pending   |
      | clearance-blocked   |
    Then the final stage is "clearance_blocked"
    And history contains 5 transitions

  # ---------------------------------------------------------------------------
  # Clearance recovery from blocked (Codex round 4 P1)
  # ---------------------------------------------------------------------------

  Scenario: clearance-ready signal recovers a CLEARANCE_BLOCKED PR (first-eval block recovery)
    Given a fresh pipeline target "iterwheel/voyager#89"
    When the following signals are applied in order:
      | signal_kind         |
      | blueprint-ready     |
      | stack-classified    |
      | pr-opened           |
      | clearance-pending   |
      | clearance-blocked   |
      | clearance-ready     |
    Then the final stage is "clearance_ready"
    And history contains 6 transitions
