Feature: Clearance bot — PR review readiness verification and routing

  As the Iterwheel pipeline's Clearance stage
  I want to evaluate GitHub pull request review state and route readiness signals
  So that only PRs with genuine approval and no blocking threads advance to Liftoff

  Background:
    Given the Clearance agent slug is "iterwheel-clearance"

  # ---------------------------------------------------------------------------
  # Event routing gate — should_run_clearance
  # ---------------------------------------------------------------------------

  Scenario: pull_request opened event triggers Clearance evaluation
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: pull_request synchronize event triggers Clearance evaluation
    Given a webhook payload "clearance_pull_request_synchronize"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: pull_request converted_to_draft event triggers Clearance evaluation
    Given a webhook payload "clearance_pull_request_converted_to_draft"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: pull_request_review submitted event triggers Clearance evaluation
    Given a webhook payload "clearance_pull_request_review_submitted"
    When Clearance receives the "pull_request_review" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: pull_request review_requested action does not trigger Clearance
    Given a webhook payload "clearance_pull_request_review_requested"
    When Clearance receives the "pull_request" event
    Then no clearance routes are produced

  Scenario: pull_request_review_comment created triggers Clearance (Codex PR #9 P1 fix)
    Given a webhook payload "clearance_pull_request_review_comment"
    When Clearance receives the "pull_request_review_comment" event
    Then exactly one clearance route is produced

  Scenario: check_run completed does not trigger Clearance
    Given a webhook payload "clearance_check_run_completed"
    When Clearance receives the "check_run" event
    Then no clearance routes are produced

  Scenario: Unknown event type produces no clearance routes
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "label" event
    Then no clearance routes are produced

  # ---------------------------------------------------------------------------
  # Clearance bot self-trigger guard
  # ---------------------------------------------------------------------------

  Scenario: pull_request_review submitted by Clearance bot does not self-trigger
    Given a webhook payload "clearance_review_from_clearance_bot"
    When Clearance receives the "pull_request_review" event
    Then no clearance routes are produced

  # ---------------------------------------------------------------------------
  # issue_comment routing — Codex review result + /clearance command
  # ---------------------------------------------------------------------------

  Scenario: Codex review result comment triggers Clearance evaluation
    Given a webhook payload "clearance_issue_comment_codex_review"
    When Clearance receives the "issue_comment" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: Codex bot with bracket suffix also triggers Clearance evaluation
    Given a webhook payload "clearance_issue_comment_codex_bracket_bot"
    When Clearance receives the "issue_comment" event
    Then exactly one clearance route is produced

  Scenario: Non-Codex comment without /clearance command is ignored
    Given a webhook payload "clearance_issue_comment_no_command"
    When Clearance receives the "issue_comment" event
    Then no clearance routes are produced

  Scenario: /clearance command in issue comment triggers evaluation
    Given a webhook payload "clearance_issue_comment_clearance_command"
    When Clearance receives the "issue_comment" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: issue_comment edited action is ignored even with Codex body
    Given a webhook payload "clearance_issue_comment_codex_edited"
    When Clearance receives the "issue_comment" event
    Then no clearance routes are produced

  Scenario: Codex comment on a plain issue (no pull_request link) is ignored
    Given a webhook payload "clearance_issue_comment_codex_plain_issue"
    When Clearance receives the "issue_comment" event
    Then no clearance routes are produced

  # ---------------------------------------------------------------------------
  # Reaction routing — Codex PR body reaction (👀 / 👍)
  # ---------------------------------------------------------------------------

  Scenario: Codex thumbs-up reaction on PR body triggers Clearance evaluation
    Given a webhook payload "clearance_reaction_codex_thumbs_up"
    When Clearance receives the "reaction" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: Codex eyes reaction on PR body triggers Clearance evaluation
    Given a webhook payload "clearance_reaction_codex_eyes"
    When Clearance receives the "reaction" event
    Then exactly one clearance route is produced

  Scenario: Non-Codex user reaction is ignored
    Given a webhook payload "clearance_reaction_non_codex"
    When Clearance receives the "reaction" event
    Then no clearance routes are produced

  Scenario: reaction deleted action by Codex still triggers Clearance
    Given a webhook payload "clearance_reaction_codex_deleted"
    When Clearance receives the "reaction" event
    Then exactly one clearance route is produced

  # ---------------------------------------------------------------------------
  # check_suite routing — CI completion
  # ---------------------------------------------------------------------------

  Scenario: check_suite completed with linked PR triggers Clearance evaluation
    Given a webhook payload "clearance_check_suite_completed_with_pr"
    When Clearance receives the "check_suite" event
    Then exactly one clearance route is produced
    And the clearance route targets the Clearance agent

  Scenario: check_suite completed with no linked PRs is ignored
    Given a webhook payload "clearance_check_suite_no_prs"
    When Clearance receives the "check_suite" event
    Then no clearance routes are produced

  Scenario: check_suite with multiple linked PRs produces one route per PR
    Given a webhook payload "clearance_check_suite_multi_pr"
    When Clearance receives the "check_suite" event
    Then exactly two clearance routes are produced

  # ---------------------------------------------------------------------------
  # Route shape
  # ---------------------------------------------------------------------------

  Scenario: Clearance route kind is clearance_readiness
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance route kind is "clearance_readiness"

  Scenario: Clearance route includes agent slug and agent id
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance route agent id is "github-clearance-agent"

  Scenario: Clearance route includes PR number in validation
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance validation includes the PR number

  Scenario: Clearance route includes base ref in validation
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance validation includes the base ref

  Scenario: Clearance route validation status is clearance_pending initially
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance validation status is "clearance_pending"

  Scenario: Clearance route validation conclusion is neutral initially
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance validation conclusion is "neutral"

  Scenario: Clearance route writeback is dynamic clearance_readiness
    Given a webhook payload "clearance_pull_request_opened"
    When Clearance receives the "pull_request" event
    Then exactly one clearance route is produced
    And the clearance route writeback is dynamic "clearance_readiness"

  Scenario: issue_comment route carries the original event name
    Given a webhook payload "clearance_issue_comment_codex_review"
    When Clearance receives the "issue_comment" event
    Then exactly one clearance route is produced
    And the clearance route event is "issue_comment"
    And the clearance route action is "created"

  # ---------------------------------------------------------------------------
  # evaluate_clearance_snapshot — clearance_ready outcome
  # ---------------------------------------------------------------------------

  Scenario: PR with current-head approval and no blocking threads is ready
    Given a clearance snapshot with an approved review on the current head
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_ready"
    And the evaluation conclusion is "success"
    And the evaluation reactions add "+1"
    And the evaluation reactions remove "eyes"
    And the evaluation reactions remove "rocket"
    And the evaluation labels add "clearance-4-ready-for-merge"
    And the evaluation labels remove "clearance-1-pending"
    And the evaluation labels remove "clearance-2-blocked"
    And the evaluation labels remove "clearance-3-ready-for-approval"
    And the evaluation labels remove "clearance-ready"
    And the evaluation labels remove "clearance-pending"
    And the evaluation labels remove "clearance-blocked"
    And the evaluation confidence has no reasons

  Scenario: Ready PR summary is "Clearance is ready for Countdown."
    Given a clearance snapshot with an approved review on the current head
    When the clearance snapshot is evaluated
    Then the evaluation summary is "Clearance is ready for Countdown."

  # ---------------------------------------------------------------------------
  # evaluate_clearance_snapshot — clearance_pending outcome
  # ---------------------------------------------------------------------------

  Scenario: Draft PR is clearance_pending
    Given a clearance snapshot for a draft PR with an approval
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_pending"
    And the evaluation conclusion is "neutral"
    And the evaluation reasons include "PR is still draft."

  Scenario: Closed PR is clearance_pending
    Given a clearance snapshot for a closed PR with an approval
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_pending"
    And the evaluation reasons include "PR is not open."

  Scenario: PR with no approvals at all is clearance_pending
    Given a clearance snapshot with no reviews
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_pending"
    And the evaluation reasons include "No approval on the current PR head."

  Scenario: PR with stale approval only is clearance_pending
    Given a clearance snapshot with only a stale approval
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_pending"
    And the evaluation review state stale approvals is not empty

  Scenario: Pending PR reactions use eyes, not thumbs-up
    Given a clearance snapshot with no reviews
    When the clearance snapshot is evaluated
    Then the evaluation reactions add "eyes"
    And the evaluation reactions remove "+1"

  # ---------------------------------------------------------------------------
  # evaluate_clearance_snapshot — clearance_blocked outcome
  # ---------------------------------------------------------------------------

  Scenario: Changes-requested review causes clearance_blocked
    Given a clearance snapshot with a changes-requested review
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_blocked"
    And the evaluation conclusion is "failure"
    And the evaluation review state has blocking reviewers
    And the evaluation labels add "clearance-2-blocked"

  Scenario: Unresolved review thread causes clearance_blocked
    Given a clearance snapshot with an unresolved review thread
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_blocked"
    And the evaluation reasons include "review thread(s) are unresolved"

  Scenario: Outdated unresolved review thread does NOT block clearance (Codex round 5 P2)
    Given a clearance snapshot with only an outdated unresolved review thread and a current approval
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_ready"
    And the evaluation reasons exclude "review thread(s) are unresolved"

  Scenario: Later approval supersedes earlier changes-requested from same author
    Given a clearance snapshot where a reviewer re-approved after requesting changes
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_ready"
    And the evaluation review state has no blocking reviewers

  Scenario: DISMISSED review is not counted as current approval
    Given a clearance snapshot with only a dismissed review
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_pending"
    And the evaluation review state has no blocking reviewers

  # ---------------------------------------------------------------------------
  # apply_swm_overlay — SWM automation status branches
  # ---------------------------------------------------------------------------

  Scenario: apply_swm_overlay with no automation dict returns evaluation unchanged
    Given a ready evaluation and no automation
    When the swm overlay is applied
    Then the overlaid evaluation is identical to the original

  Scenario: apply_swm_overlay with automation disabled returns evaluation unchanged
    Given a ready evaluation and automation with enabled false
    When the swm overlay is applied
    Then the overlaid evaluation is identical to the original

  Scenario: apply_swm_overlay with status ready clears thread-only blockers
    Given a blocked thread-only evaluation and automation with status "ready" and enabled true
    When the swm overlay is applied
    Then the overlaid evaluation status is "clearance_ready"
    And the overlaid evaluation conclusion is "success"
    And the overlaid evaluation labels add "clearance-4-ready-for-merge"
    And the overlaid evaluation reactions add "+1"

  Scenario: apply_swm_overlay with status ready preserves draft PR blockers
    Given a draft pending evaluation and automation with status "ready" and enabled true
    When the swm overlay is applied
    Then the overlaid evaluation is identical to the original

  Scenario: apply_swm_overlay with status pending downgrades to clearance_pending
    Given a ready evaluation and automation with status "pending" and enabled true
    When the swm overlay is applied
    Then the overlaid evaluation status is "clearance_pending"
    And the overlaid evaluation conclusion is "neutral"
    And the overlaid evaluation reactions add "eyes"
    And the overlaid evaluation reactions remove "+1"
    And the overlaid evaluation labels add "clearance-1-pending"
    And the overlaid evaluation confidence reasons include the automation engine reason

  Scenario: apply_swm_overlay with status blocked downgrades to clearance_blocked
    Given a ready evaluation and automation with status "blocked" and enabled true
    When the swm overlay is applied
    Then the overlaid evaluation status is "clearance_blocked"
    And the overlaid evaluation conclusion is "failure"
    And the overlaid evaluation labels add "clearance-2-blocked"

  Scenario: apply_swm_overlay with status error downgrades to clearance_blocked
    Given a ready evaluation and automation with status "error" and enabled true
    When the swm overlay is applied
    Then the overlaid evaluation status is "clearance_blocked"
    And the overlaid evaluation conclusion is "failure"

  Scenario: apply_swm_overlay uses automation reason field when present
    Given a ready evaluation and automation with status "pending" reason "awaiting SWM tick" and enabled true
    When the swm overlay is applied
    Then the overlaid evaluation confidence reasons include "awaiting SWM tick"

  Scenario: apply_swm_overlay uses automation error field when reason absent
    Given a ready evaluation and automation with status "error" error "timeout" and enabled true
    When the swm overlay is applied
    Then the overlaid evaluation confidence reasons include "timeout"

  # ---------------------------------------------------------------------------
  # Codex follow-up scheduling
  # ---------------------------------------------------------------------------

  Scenario: clearance_waiting_on_codex_pr_body_reaction is true when signal is reviewing
    Given a clearance route with codex_pr_body_signal "reviewing" and status pending
    When the codex reaction wait state is checked
    Then the route is waiting on codex pr body reaction

  Scenario: clearance_waiting_on_codex_pr_body_reaction is false for different agent
    Given a clearance route with wrong agent slug and codex_pr_body_signal "reviewing"
    When the codex reaction wait state is checked
    Then the route is not waiting on codex pr body reaction

  Scenario: clearance_waiting_on_codex_pr_body_reaction is false when signal is not reviewing
    Given a clearance route with codex_pr_body_signal "approved" and status pending
    When the codex reaction wait state is checked
    Then the route is not waiting on codex pr body reaction

  Scenario: should_schedule_codex_reaction_follow_up is true for check_suite event when waiting
    Given a clearance route with codex_pr_body_signal "reviewing" and status pending
    When the follow-up schedule decision is evaluated for event "check_suite"
    Then a codex reaction follow-up should be scheduled

  Scenario: should_schedule_codex_reaction_follow_up is false for non-check_suite event
    Given a clearance route with codex_pr_body_signal "reviewing" and status pending
    When the follow-up schedule decision is evaluated for event "pull_request"
    Then a codex reaction follow-up should not be scheduled

  Scenario: build_codex_reaction_follow_up_route sets follow-up event and action
    Given a clearance route with codex_pr_body_signal "reviewing" and status pending
    When the codex reaction follow-up route is built
    Then the follow-up route event is "clearance_follow_up"
    And the follow-up route action is "codex_pr_body_reaction"

  Scenario: build_codex_reaction_follow_up_route preserves validation pr_number
    Given a clearance route with codex_pr_body_signal "reviewing" and status pending
    When the codex reaction follow-up route is built
    Then the follow-up route preserves the PR number

  Scenario: build_codex_reaction_follow_up_route removes automation key
    Given a clearance route with codex_pr_body_signal "reviewing" and status pending
    When the codex reaction follow-up route is built
    Then the follow-up route has no automation key

  Scenario: build_codex_reaction_follow_up_route sets writeback dynamic clearance_readiness
    Given a clearance route with codex_pr_body_signal "reviewing" and status pending
    When the codex reaction follow-up route is built
    Then the follow-up route writeback is dynamic "clearance_readiness"

  # ---------------------------------------------------------------------------
  # clearance_swm_codex_pr_body_signal — signal extraction
  # ---------------------------------------------------------------------------

  Scenario: Signal read from automation.swm_clearance.codex_pr_body_signal
    Given a clearance route with direct codex_pr_body_signal "reviewing"
    When the codex pr body signal is extracted
    Then the extracted signal is "reviewing"

  Scenario: Signal read from automation.swm_clearance.poll.codex_pr_body_signal fallback
    Given a clearance route with poll codex_pr_body_signal "approved"
    When the codex pr body signal is extracted
    Then the extracted signal is "approved"

  Scenario: No signal returns None
    Given a clearance route with no codex_pr_body_signal
    When the codex pr body signal is extracted
    Then the extracted signal is None

  # ---------------------------------------------------------------------------
  # Issue #25: Numbered clearance labels — new label values
  # ---------------------------------------------------------------------------

  Scenario: PR with current-head approval and no configured approver is ready (numbered label)
    Given a clearance snapshot with an approved review on the current head
    And no configured review request users
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_ready"
    And the evaluation labels add "clearance-4-ready-for-merge"
    And the evaluation labels remove "clearance-1-pending"
    And the evaluation labels remove "clearance-2-blocked"
    And the evaluation labels remove "clearance-3-ready-for-approval"

  Scenario: PR with no reviews uses numbered pending label
    Given a clearance snapshot with no reviews
    And no configured review request users
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_pending"
    And the evaluation labels add "clearance-1-pending"
    And the evaluation labels remove "clearance-4-ready-for-merge"
    And the evaluation labels remove "clearance-2-blocked"

  Scenario: PR with changes-requested uses numbered blocked label
    Given a clearance snapshot with a changes-requested review
    And no configured review request users
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_blocked"
    And the evaluation labels add "clearance-2-blocked"

  Scenario: Legacy labels are migrated away on every writeback — ready case
    Given a clearance snapshot with an approved review on the current head
    And no configured review request users
    When the clearance snapshot is evaluated
    Then the evaluation labels remove "clearance-pending"
    And the evaluation labels remove "clearance-blocked"
    And the evaluation labels remove "clearance-ready"

  Scenario: Legacy labels are migrated away on every writeback — pending case
    Given a clearance snapshot with no reviews
    And no configured review request users
    When the clearance snapshot is evaluated
    Then the evaluation labels remove "clearance-pending"
    And the evaluation labels remove "clearance-blocked"
    And the evaluation labels remove "clearance-ready"

  Scenario: Legacy labels are migrated away on every writeback — blocked case
    Given a clearance snapshot with a changes-requested review
    And no configured review request users
    When the clearance snapshot is evaluated
    Then the evaluation labels remove "clearance-pending"
    And the evaluation labels remove "clearance-blocked"
    And the evaluation labels remove "clearance-ready"

  Scenario: PR with no configured approver on current head is ready_for_approval
    Given a clearance snapshot with an approved review on the current head
    And configured review request user "required-approver" who has not approved
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_ready_for_approval"
    And the evaluation labels add "clearance-3-ready-for-approval"
    And the evaluation labels remove "clearance-4-ready-for-merge"
    And the evaluation labels remove "clearance-1-pending"
    And the evaluation labels remove "clearance-2-blocked"
    And the evaluation labels remove "clearance-pending"
    And the evaluation labels remove "clearance-blocked"
    And the evaluation labels remove "clearance-ready"
    And the evaluation reactions add "eyes"
    And the evaluation summary contains "ready for human approval"

  Scenario: Configured approver has approved current head — clearance_ready
    Given a clearance snapshot where the configured approver has approved
    When the clearance snapshot is evaluated
    Then the evaluation status is "clearance_ready"
    And the evaluation labels add "clearance-4-ready-for-merge"
    And the evaluation summary is "Clearance is ready for Countdown."

  # ---------------------------------------------------------------------------
  # Issue #25 AC#9: literal "Review request: requested @<user>" line
  # ---------------------------------------------------------------------------

  Scenario: Ready-for-approval comment includes literal "Review request: requested @frankyxhl" line
    Given an env-configured reviewer "frankyxhl" that has not approved and an approval from a non-configured user
    When the clearance comment is built
    Then the comment body contains "Review request: requested @frankyxhl"
