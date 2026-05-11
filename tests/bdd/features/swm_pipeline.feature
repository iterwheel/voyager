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
    And exactly 1 in-thread reply was posted under the Codex review comment
    And the in-thread reply body contains "RESOLVED"
    And the in-thread reply body contains "clearance-close-reason"
    And the in-thread reply body contains "head-sha-abc"

  Scenario: DRY_RUN true plans the resolve but posts no in-thread reply
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the sync actions count is 1
    And no in-thread reply was posted

  Scenario: resolveReviewThread mutation failure suppresses the in-thread reply (Codex PR #9 P2 fix)
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub GitHubAppClient fails on the resolveReviewThread mutation
    When compute_clearance_automation runs with DRY_RUN false
    Then the pipeline raised an exception
    And exactly 1 resolveReviewThread mutation was invoked
    And no in-thread reply was posted

  Scenario: State C thread with non-substantive reply produces OPEN → blocked
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with a short ack reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "blocked"
    And the automation reason mentions "still OPEN"
    And the sync actions count is 0

  Scenario: State B outdated defers to investigator wave under 7B-1 deterministic-only routing
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread that is outdated with no author reply
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "blocked"
    And the sync actions count is 0
    And the automation reason mentions "still OPEN"

  Scenario: Maintainer reply does NOT trigger Clearance auto-resolution (P1 author-filter)
    Given the stub PR "iterwheel/sandbox" #49 author is "ryosaeba1985"
    And the stub PR has 1 Codex thread with a substantive reply from "some-maintainer" and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "blocked"
    And the sync actions count is 0

  Scenario: Stale Codex follow-up does NOT override newer substantive author reply (P2 ordering)
    Given the stub PR "iterwheel/sandbox" #49 author is "ryosaeba1985"
    And the stub PR has 1 Codex thread where an older Codex followup precedes a newer substantive author reply
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "ready"
    And the sync actions count is 1

  Scenario: Fresh Codex follow-up overrides earlier substantive author reply (P2 ordering)
    Given the stub PR "iterwheel/sandbox" #49 author is "ryosaeba1985"
    And the stub PR has 1 Codex thread where a newer "still not addressed" Codex followup follows a substantive author reply
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "blocked"
    And the sync actions count is 0

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
