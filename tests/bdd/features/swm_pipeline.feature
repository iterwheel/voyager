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
  # Wave 7B-3: investigator path
  # ---------------------------------------------------------------------------

  Scenario: No investigator wired — State B thread verdicts OPEN with no llm_verdict (regression guard)
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread that is outdated with no author reply
    And no investigator is configured
    When compute_clearance_automation runs with investigator
    Then the automation status is "blocked"
    And the thread llm_verdict is None
    And the pipeline trigger is "webhook"

  Scenario: State B + investigator returns RESOLVED — thread verdict overridden to RESOLVED
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "Fix confirmed in diff"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the thread verdict is "RESOLVED"
    And the thread llm_verdict is "RESOLVED"
    And the thread llm_confidence is 0.95
    And the thread llm_reason contains "Fix confirmed in diff"
    And the pipeline trigger contains "webhook+investigator"

  Scenario: State B + investigator returns OPEN — thread verdict remains OPEN
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "OPEN" confidence 0.80 reason "Concern not addressed"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the thread verdict is "OPEN"
    And the thread llm_verdict is "OPEN"
    And the thread llm_reason contains "Concern not addressed"
    And the pipeline trigger contains "webhook+investigator"

  Scenario: State B + investigator returns NEEDS_HUMAN_JUDGMENT
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "NEEDS_HUMAN_JUDGMENT" confidence 0.60 reason "Ambiguous evidence"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the thread verdict is "NEEDS_HUMAN_JUDGMENT"
    And the thread llm_verdict is "NEEDS_HUMAN_JUDGMENT"
    And the pipeline trigger contains "webhook+investigator"

  Scenario: State A thread (fresh) + investigator configured — investigator NOT called
    Given the stub PR "iterwheel/sandbox" #49 has 1 fresh Codex thread (State A) at path "app.py"
    And a fake investigator returning verdict "RESOLVED" confidence 0.99 reason "Would fire if called"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the investigator was never called
    And the thread llm_verdict is None
    And the pipeline trigger is "webhook"

  Scenario: State C thread + investigator configured — investigator NOT called
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And a fake investigator returning verdict "RESOLVED" confidence 0.99 reason "Would fire if called"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the investigator was never called
    And the thread llm_verdict is None

  Scenario: State B + InvestigationError — falls back to deterministic OPEN with no llm_verdict
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator that raises InvestigationError "LLM quota exceeded"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the thread verdict is "OPEN"
    And the thread llm_verdict is None
    And the pipeline trigger is "webhook"

  Scenario: Lazy memoize — zero State B threads means pull_request_diff never called
    Given the stub PR "iterwheel/sandbox" #49 has 1 fresh Codex thread (State A) at path "app.py"
    And a fake investigator returning verdict "RESOLVED" confidence 0.99 reason "irrelevant"
    And the stub client records pull_request_diff calls
    When compute_clearance_automation runs with investigator
    Then pull_request_diff was called 0 times

  Scenario: Lazy memoize — 2 State B threads on same PR call pull_request_diff exactly once
    Given the stub PR "iterwheel/sandbox" #49 has 2 outdated Codex threads at path "app.py" line 10
    And a fake investigator returning verdict "OPEN" confidence 0.80 reason "Not fixed" for each thread
    And the stub client records pull_request_diff calls
    When compute_clearance_automation runs with investigator
    Then pull_request_diff was called 1 time
    And the investigator was called 2 times

  Scenario: Trigger composition — investigator fired + Stage 1.5 sync produces combined trigger
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "Fix confirmed"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator and DRY_RUN false
    Then the pipeline trigger is "webhook+investigator+stage1.5-sync"

  # ---------------------------------------------------------------------------
  # Wave 7B-3: new exception-path coverage (P1 httpx, P2 ValueError, Gemini gap)
  # ---------------------------------------------------------------------------

  Scenario: httpx.HTTPError on pull_request_diff — falls back to deterministic OPEN, automation not "error"
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "Would fire if diff fetched"
    And the stub client raises httpx.HTTPStatusError on pull_request_diff
    When compute_clearance_automation runs with investigator
    Then the thread verdict is "OPEN"
    And the thread llm_verdict is None
    And the automation status is "blocked"
    And the pipeline trigger is "webhook"

  Scenario: Investigator returns unknown verdict string — falls back to deterministic OPEN via ValueError
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning unknown verdict "MAYBE"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the thread verdict is "OPEN"
    And the thread llm_verdict is None
    And the automation status is "blocked"
    And the pipeline trigger is "webhook"

  Scenario: Two State B threads on different paths — diff fetched once, investigator called twice with different excerpts
    Given the stub PR "iterwheel/sandbox" #49 has 2 outdated Codex threads at different paths
    And a fake investigator returning verdict "OPEN" confidence 0.80 reason "Not fixed" for each thread
    And the stub client returns a sample diff covering both paths
    When compute_clearance_automation runs with investigator
    Then pull_request_diff was called 1 time
    And the investigator was called 2 times
    And the investigator received different diff excerpts for each thread

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

  # ---------------------------------------------------------------------------
  # Wave 7B-3 hardening #5: investigator failure-mode contract
  # ---------------------------------------------------------------------------

  Scenario: State B + InvestigationError → automation has investigator_error_* fields
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator that raises InvestigationError "LLM quota exceeded"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the automation status is "blocked"
    And the automation investigator_error_count is 1
    And the automation investigator_error_thread_ids contains "PRRT_codex_alpha"
    And the automation investigator_error_reason contains "LLM quota exceeded"
    And the snapshot evidence llm_error for thread "PRRT_codex_alpha" contains "LLM quota exceeded"

  Scenario: 2 State B threads both fail → investigator_error_count is 2
    Given the stub PR "iterwheel/sandbox" #49 has 2 outdated Codex threads at different paths
    And a fake investigator that raises InvestigationError for all threads "network error"
    And the stub client returns a sample diff covering both paths
    When compute_clearance_automation runs with investigator
    Then the automation investigator_error_count is 2
    And the automation investigator_error_thread_ids contains "PRRT_codex_alpha"
    And the automation investigator_error_thread_ids contains "PRRT_codex_beta"

  Scenario: Happy path (no failures) → investigator_error_* fields absent
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "Fix confirmed in diff"
    And the stub client returns a sample diff for "app.py"
    When compute_clearance_automation runs with investigator
    Then the automation has no investigator_error_fields

  Scenario: httpx fallback — Evidence.llm_error is set on the failed thread
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "Would fire if diff fetched"
    And the stub client raises httpx.HTTPStatusError on pull_request_diff
    When compute_clearance_automation runs with investigator
    Then the snapshot evidence llm_error for thread "PRRT_codex_alpha" contains "500"
