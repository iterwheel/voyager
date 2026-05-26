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

  Scenario: resolveReviewThread mutation failure captures structured metadata and suppresses the in-thread reply (CHG-1813)
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub GitHubAppClient fails on the resolveReviewThread mutation with an HTTP error
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "error"
    And the latest poll status is "error"
    And exactly 1 resolveReviewThread mutation was invoked
    And no in-thread reply was posted
    And the automation has writeback failure metadata
    And the automation writeback failure count is 1
    And the automation writeback failure reason starts with "1 writeback operation failed"
    And the Stage 1.5 action result has applied false with operation "resolveReviewThread"
    And the thread GitHub state was not mutated

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

  # ---------------------------------------------------------------------------
  # Wave 7C-3: severity evaluator wiring
  # ---------------------------------------------------------------------------

  Scenario: S1 — P1 badge + required_check_coupling cue + unprotected base branch demotes to P2
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with P1 badge and required_check_coupling body
    And the base branch is "main"
    And the stub branch_protected returns False
    When compute_clearance_automation runs with DRY_RUN true
    Then the thread codex_severity is "P1"
    And the thread effective_severity is "P2"
    And the thread demotion_reason contains "main has no branch protection"
    And a severity_demoted log was emitted

  Scenario: S2 — P1 badge + required_check_coupling cue + protected base branch does NOT demote
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with P1 badge and required_check_coupling body
    And the base branch is "main"
    And the stub branch_protected returns True
    When compute_clearance_automation runs with DRY_RUN true
    Then the thread codex_severity is "P1"
    And the thread effective_severity is "P1"
    And the thread demotion_reason is None

  Scenario: S3 — No severity badge → codex_severity P3, effective P3, no demotion
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with no severity badge
    And the base branch is "main"
    And the stub branch_protected returns False
    When compute_clearance_automation runs with DRY_RUN true
    Then the thread codex_severity is "P3"
    And the thread effective_severity is "P3"
    And the thread demotion_reason is None

  Scenario: S4 — P2 badge but no required_check_coupling cue → P2 effective, no demotion
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with P2 badge and no coupling cue
    And the base branch is "main"
    And the stub branch_protected returns False
    When compute_clearance_automation runs with DRY_RUN true
    Then the thread codex_severity is "P2"
    And the thread effective_severity is "P2"
    And the thread demotion_reason is None

  Scenario: S5 — P1 → P2 (single-step demotion) when unprotected + required_check_coupling, ThreadSnapshot also populated
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with P1 badge and required_check_coupling body
    And the base branch is "main"
    And the stub branch_protected returns False
    When compute_clearance_automation runs with DRY_RUN true
    Then the thread codex_severity is "P1"
    And the thread effective_severity is "P2"
    And the thread demotion_reason contains "main has no branch protection"

  Scenario: S6 — branch_protected REST fails → fail-safe protected=True → no demotion
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with P1 badge and required_check_coupling body
    And the base branch is "main"
    And the stub branch_protected raises a transport error
    When compute_clearance_automation runs with DRY_RUN true
    Then the thread codex_severity is "P1"
    And the thread effective_severity is "P1"
    And the thread demotion_reason is None

  # ---------------------------------------------------------------------------
  # Wave 7C commit 5: head_sha in automation dict
  # ---------------------------------------------------------------------------

  Scenario: Happy path — automation dict contains head_sha with the PR's head SHA value
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN true
    Then the automation status is "ready"
    And the automation head_sha is "head-sha-abc1234"

  Scenario: No Codex threads — automation dict still contains head_sha
    Given the stub PR "iterwheel/sandbox" #49 has no review threads
    When compute_clearance_automation runs
    Then the automation status is "ready"
    And the automation head_sha is "head-sha-abc1234"

  Scenario: No Codex threads (DRY_RUN false) — automation head_sha always emitted
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread already isResolved
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And the automation head_sha is "head-sha-abc1234"

  # ---------------------------------------------------------------------------
  # Wave 7C commit 6: stale-verdict guard in dispatch_route_writeback
  # ---------------------------------------------------------------------------

  Scenario: G1 fresh — automation.head_sha matches current PR head, writeback proceeds
    Given a stub automation with head_sha "sha-abc" and status "ready"
    And the current PR head sha is "sha-abc"
    When dispatch_route_writeback runs with DRY_RUN false for PR 49 on "iterwheel/sandbox"
    Then the writeback was not skipped
    And no stale_verdict_skip log was emitted

  Scenario: G2 stale — automation.head_sha differs from current PR head, writeback is skipped
    Given a stub automation with head_sha "sha-old" and status "ready"
    And the current PR head sha is "sha-new"
    When dispatch_route_writeback runs with DRY_RUN false for PR 49 on "iterwheel/sandbox"
    Then the dispatch result is skipped with reason "stale_verdict"
    And the dispatch automation status is "stale_verdict_skip"
    And a stale_verdict_skip log was emitted with expected_sha "sha-old" and actual_sha "sha-new"

  Scenario: G3 fail-open — client.pull_request raises httpx error, writeback proceeds
    Given a stub automation with head_sha "sha-abc" and status "ready"
    And the stub client fails on pull_request with an httpx error
    When dispatch_route_writeback runs with DRY_RUN false for PR 49 on "iterwheel/sandbox"
    Then the writeback was not skipped
    And a stale_guard_failed_fail_open log was emitted

  Scenario: G4 legacy payload — automation has no head_sha key, guard short-circuits
    Given a stub automation with no head_sha and status "ready"
    And the current PR head sha is "sha-new"
    When dispatch_route_writeback runs with DRY_RUN false for PR 49 on "iterwheel/sandbox"
    Then the writeback was not skipped
    And no stale_verdict_skip log was emitted

  Scenario: G5 dry-run skip — guard short-circuits when DRY_RUN true, no pull_request fetch
    Given a stub automation with head_sha "sha-old" and status "ready"
    And the current PR head sha is "sha-new"
    When dispatch_route_writeback runs with DRY_RUN true for PR 49 on "iterwheel/sandbox"
    Then the writeback was not skipped
    And pull_request was never called

  Scenario: G6 pipeline-stale-skip terminal — automation.status already stale_verdict_skip from pipeline, dispatch skips before enrichment even when head_sha would match
    Given a stub automation with head_sha "sha-current" and status "stale_verdict_skip"
    And the current PR head sha is "sha-current"
    When dispatch_route_writeback runs with DRY_RUN false for PR 49 on "iterwheel/sandbox"
    Then the dispatch result is skipped with reason "stale_verdict"
    And the dispatch automation status is "stale_verdict_skip"
    And a writeback_skipped_stale_verdict log was emitted

  # ---------------------------------------------------------------------------
  # Fix 2 (Codex P2): pre-mutation stale guard inside compute_clearance_automation
  # ---------------------------------------------------------------------------

  Scenario: P1 pre-mutation stale — expected_sha differs from fresh PR head, Stage 1.5 NOT invoked
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the webhook expected_sha is "sha-webhook-old"
    And the stub PR current head sha advanced to "sha-fresh-new"
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "stale_verdict_skip"
    And no resolveReviewThread mutation was invoked
    And no in-thread reply was posted
    And a pipeline_stale_verdict_skip log was emitted with expected_sha "sha-webhook-old" and actual_sha "sha-fresh-new"

  Scenario: P2 pre-mutation fresh — expected_sha matches fresh PR head, Stage 1.5 runs normally
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the webhook expected_sha is "head-sha-abc1234"
    And the stub PR current head sha is "head-sha-abc1234"
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And exactly 1 resolveReviewThread mutation was invoked

  Scenario: P3 no expected_sha — pre-mutation guard short-circuits, Stage 1.5 runs normally
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And exactly 1 resolveReviewThread mutation was invoked

  # ---------------------------------------------------------------------------
  # R5-P2: second pre-Stage-1.5 stale guard (race window fix)
  # ---------------------------------------------------------------------------

  Scenario: R5-P2 race — PR head advances between initial fetch and Stage 1.5; second guard fires
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the webhook expected_sha is "sha-webhook"
    And the stub PR initial head sha is "sha-webhook"
    And the stub PR head advances on the second pull_request call to "sha-advanced"
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "stale_verdict_skip"
    And no resolveReviewThread mutation was invoked
    And a pipeline_stale_verdict_skip log was emitted with expected_sha "sha-webhook" and actual_sha "sha-advanced"

  Scenario: R5-P2 no race — PR head is stable on both fetches, Stage 1.5 runs normally
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the webhook expected_sha is "sha-webhook"
    And the stub PR head is stable at "sha-webhook" on all fetches
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And exactly 1 resolveReviewThread mutation was invoked

  # ---------------------------------------------------------------------------
  # R7-P2: second pre-Stage-1.5 guard applies even when expected_sha is None
  # (check_suite events / /clearance issue comments)
  # ---------------------------------------------------------------------------

  Scenario: R7-P2-A no expected_sha race — head advances between initial fetch and Stage 1.5, second guard fires using initial head_sha as baseline
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub PR initial head sha is "sha-initial"
    And the stub PR head advances on the second pull_request call to "sha-advanced-no-webhook"
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "stale_verdict_skip"
    And no resolveReviewThread mutation was invoked
    And a pipeline_stale_verdict_skip log was emitted with expected_sha "sha-initial" and actual_sha "sha-advanced-no-webhook"

  Scenario: R7-P2-B no expected_sha no race — head is stable on both fetches, Stage 1.5 runs normally
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub PR head is stable at "sha-initial-stable" on all fetches
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And exactly 1 resolveReviewThread mutation was invoked

  # ---------------------------------------------------------------------------
  # CHG-1813: writeback failure capture from Stage 1.5
  # ---------------------------------------------------------------------------

  Scenario: CHG-1813 HTTPStatusError on resolveReviewThread — structured failure metadata captured
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub GitHubAppClient fails on the resolveReviewThread mutation with HTTPStatusError 403
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "error"
    And the latest poll status is "error"
    And exactly 1 resolveReviewThread mutation was invoked
    And no in-thread reply was posted
    And the automation has writeback failure metadata
    And the automation writeback failure count is 1
    And the Stage 1.5 action result has applied false with operation "resolveReviewThread"
    And the thread GitHub state was not mutated

  Scenario: CHG-1813 GraphQL permission error on resolveReviewThread — structured failure metadata captured
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub GitHubAppClient fails on the resolveReviewThread mutation with a GraphQL error
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "error"
    And exactly 1 resolveReviewThread mutation was invoked
    And no in-thread reply was posted
    And the automation has writeback failure metadata
    And the Stage 1.5 action result error_class is "GraphQLError"

  Scenario: CHG-1813 TimeoutError on resolveReviewThread — structured failure metadata captured
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub GitHubAppClient fails on the resolveReviewThread mutation with a TimeoutError
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "error"
    And the automation has writeback failure metadata
    And the Stage 1.5 action result error_class is "TimeoutError"

  Scenario: CHG-1813 Successful resolve produces no writeback failure keys
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And the automation has no writeback failure metadata
    And exactly 1 in-thread reply was posted under the Codex review comment

  # ---------------------------------------------------------------------------
  # Issue #63: State A investigator eligibility (codex_review_stale)
  # ---------------------------------------------------------------------------

  Scenario: Issue #63 State A stale — PR pushed after Codex review, investigator invoked
    Given the stub PR "iterwheel/sandbox" #49 has 1 fresh Codex thread (State A) at path "app.py"
    And a fake investigator returning verdict "RESOLVED" confidence 0.92 reason "diff confirms the null-guard was added" for each thread
    And the PR was pushed after the Codex review
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And the investigator was called 1 times
    And the thread llm_verdict is "RESOLVED"
    And exactly 1 resolveReviewThread mutation was invoked

  Scenario: Issue #63 State A fresh — PR not pushed after Codex review, no investigator
    Given the stub PR "iterwheel/sandbox" #49 has 1 fresh Codex thread (State A) at path "app.py"
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "would resolve" for each thread
    And the PR was not pushed after the Codex review
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "blocked"
    And the investigator was never called
    And the thread llm_verdict is None
    And no resolveReviewThread mutation was invoked

  # ---------------------------------------------------------------------------
  # Issue #62: fork PR head-repo accessibility (UnsupportedContext)
  # ---------------------------------------------------------------------------

  Scenario: Issue #62 fork PR with inaccessible head repo — resolveReviewThread skipped with UnsupportedContext
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub PR is from fork "ryosaeba1985/voyager"
    And the fork head repo is not accessible
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "error"
    And exactly 0 resolveReviewThread mutations were invoked
    And the Stage 1.5 action result error_class is "UnsupportedContext"
    And the Stage 1.5 action suggested_action mentions the fork repo

  Scenario: Issue #62 fork PR with accessible head repo — resolveReviewThread runs normally
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the stub PR is from fork "ryosaeba1985/voyager"
    And the fork head repo is accessible
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And exactly 1 resolveReviewThread mutation was invoked

  Scenario: Issue #62 non-fork PR — no head-repo check, resolveReviewThread runs normally
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And exactly 1 resolveReviewThread mutation was invoked

  # ---------------------------------------------------------------------------
  # Issues #100/#106: viewerCanResolve=false — same-repo unsupported auto-resolve
  # ---------------------------------------------------------------------------

  Scenario: Issue #106 same-repo viewerCanResolve=false — resolveReviewThread skipped, reaches ready
    Given the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with substantive author reply and isResolved false
    And the thread viewerCanResolve is false
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And exactly 0 resolveReviewThread mutations were invoked
    And the Stage 1.5 action has a skipped action
    And the Stage 1.5 skipped action reason is "viewerCanResolve is false"

  # ---------------------------------------------------------------------------
  # Issue #118: visual-unresolved review threads
  # ---------------------------------------------------------------------------

  Scenario: Issue #118 outdated resolved thread cannot sync GitHub UI but is non-blocking
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "Fix confirmed in diff"
    And the stub client returns a sample diff for "app.py"
    And the thread viewerCanResolve is false
    When compute_clearance_automation runs with investigator and DRY_RUN false
    Then the automation status is "ready"
    And the sync actions count is 1
    And the thread verdict is "RESOLVED"
    And the automation semantic blocker count is 0
    And the automation visual-unresolved skipped thread count is 1
    And exactly 0 resolveReviewThread mutations were invoked
    And the Stage 1.5 action has a skipped action
    And the Stage 1.5 skipped action reason is "viewerCanResolve is false"
    And no in-thread reply was posted
    And the thread GitHub state was not mutated

  Scenario: Issue #118 current actionable thread still blocks when Clearance cannot resolve it
    Given the stub PR "iterwheel/sandbox" #49 has 1 fresh Codex thread (State A) at path "app.py"
    And the thread viewerCanResolve is false
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "blocked"
    And the automation reason mentions "still OPEN"
    And the automation semantic blocker count is 1
    And the automation visual-unresolved skipped thread count is 0
    And the sync actions count is 0
    And exactly 0 resolveReviewThread mutations were invoked
    And no in-thread reply was posted

  Scenario: Issue #118 outdated resolved thread syncs when Clearance can resolve it
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated Codex thread at path "app.py" line 10
    And a fake investigator returning verdict "RESOLVED" confidence 0.95 reason "Fix confirmed in diff"
    And the stub client returns a sample diff for "app.py"
    And the thread viewerCanResolve is true
    When compute_clearance_automation runs with DRY_RUN false
    Then the automation status is "ready"
    And the sync actions count is 1
    And the thread verdict is "RESOLVED"
    And exactly 1 resolveReviewThread mutation was invoked
    And exactly 1 in-thread reply was posted under the Codex review comment
    And the in-thread reply body contains "RESOLVED"

  # ---------------------------------------------------------------------------
  # Issue #124: outdated visual-unresolved thread after clean follow-up review
  # ---------------------------------------------------------------------------

  Scenario: Issue #124 clean follow-up Codex review makes outdated visual-unresolved thread non-blocking
    Given the stub PR "iterwheel/sandbox" #49 has 1 outdated P3 Codex thread at path "CHANGELOG.md" line 166
    And a fake investigator returning verdict "OPEN" confidence 0.95 reason "Stale investigator text claims the obsolete `|-` prefix remains"
    And the stub client returns a sample diff for "CHANGELOG.md"
    And the stub PR has a clean Codex review on the current head after the thread
    And the thread viewerCanResolve is false
    When compute_clearance_automation runs with investigator and DRY_RUN false
    Then the automation status is "ready"
    And the automation reason mentions "outdated visual-unresolved"
    And the automation reason does not mention "low-priority"
    And the sync actions count is 1
    And the thread verdict is "RESOLVED"
    And the thread llm_verdict is None
    And the latest poll status is "ready"
    And the latest snapshot verdict is "RESOLVED"
    And the automation semantic blocker count is 0
    And the automation visual-unresolved thread count is 1
    And the automation visual-unresolved skipped thread count is 1
    And exactly 0 resolveReviewThread mutations were invoked
    And the Stage 1.5 action has a skipped action
    And the Stage 1.5 skipped action reason is "viewerCanResolve is false"
    And no in-thread reply was posted
    And the thread GitHub state was not mutated
