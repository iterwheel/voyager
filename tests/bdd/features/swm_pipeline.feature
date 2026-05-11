Feature: Clearance pipeline — webhook-driven SWM-1101 per-thread verdict orchestrator

  As the voyager clearance bot
  I want a webhook-triggered per-thread classify→judge→persist→sync pipeline
  So that PR review threads are deterministically resolved per SWM-1101

  Background:
    Given a temporary StateStore
    And a stub GitHubAppClient

  # ---------------------------------------------------------------------------
  # Status aggregation
  # ---------------------------------------------------------------------------

  Scenario: No review threads on the PR → automation status is "ready"
    Given the stub PR "iterwheel/sandbox" #49 has no review threads
    When compute_clearance_automation runs
    Then the automation enabled is true
    And the automation status is "ready"
    And the sync actions count is 0

  Scenario: All Codex threads RESOLVED on GitHub → ready, no Stage 1.5 sync
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread already isResolved
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And the sync actions count is 0
    And no resolveReviewThread mutation was invoked

  Scenario: RESOLVED verdict with GitHub isResolved=false under DRY_RUN plans but does not mutate
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "ready"
    And the sync actions count is 1
    And the planned sync action mutation is "resolveReviewThread"
    And no resolveReviewThread mutation was invoked

  Scenario: RESOLVED verdict with GitHub isResolved=false under DRY_RUN false invokes the mutation
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And the sync actions count is 1
    And exactly 1 resolveReviewThread mutation was invoked

  Scenario: State C thread with non-substantive reply produces OPEN → blocked
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with a short ack reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "blocked"
    And the automation reason mentions "still OPEN"
    And the sync actions count is 0

  Scenario: State B outdated with code change produces RESOLVED
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread that is outdated with no author reply
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "ready"
    And the sync actions count is 1

  # ---------------------------------------------------------------------------
  # State persistence
  # ---------------------------------------------------------------------------

  Scenario: Pipeline appends a PollRecord per webhook trigger
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the store has 1 poll for "iterwheel/sandbox" PR 49
    And the latest poll status is "ready"

  Scenario: Pipeline persists one ThreadSnapshot per Codex thread
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the store thread history for the Codex thread has 1 snapshot
    And the latest snapshot verdict is "RESOLVED"

  Scenario: Pipeline ignores non-Codex review threads
    Given the stub PR "iterwheel/sandbox" #49 has 1 human-authored review thread
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "ready"
    And the store has 1 poll for "iterwheel/sandbox" PR 49
    And the latest poll has 0 threads

  # ---------------------------------------------------------------------------
  # Error path
  # ---------------------------------------------------------------------------

  Scenario: Pull-request fetch failure surfaces as automation status error
    Given the stub GitHubAppClient fails on pull_request fetch
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "error"
    And the automation reason mentions "fetch failed"

  # ---------------------------------------------------------------------------
  # GraphQL mutation surface
  # ---------------------------------------------------------------------------

  Scenario: resolve_review_thread mutation returns the post-mutation thread
    Given a recording GitHubAppClient that returns a resolved thread payload
    When client.resolve_review_thread is awaited for "PRRT_abc123"
    Then the returned thread has isResolved true
    And the recorded GraphQL variables include threadId "PRRT_abc123"
