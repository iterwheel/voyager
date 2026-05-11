Feature: Stack bot — issue classification and routing

  As the Iterwheel pipeline's Stack stage
  I want to classify GitHub issues by type, area, size, and risk
  So that agents can filter and prioritise work accurately

  Background:
    Given the Stack agent slug is "iterwheel-stack"

  # ---------------------------------------------------------------------------
  # Event routing gate — should_run_stack
  # ---------------------------------------------------------------------------

  Scenario: Issues opened event triggers Stack classification
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack route targets the Stack agent

  Scenario: Issues edited event triggers Stack classification
    Given a webhook payload "stack_issues_edited_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack route targets the Stack agent

  Scenario: Issues reopened event triggers Stack classification
    Given a webhook payload "stack_issues_reopened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack route targets the Stack agent

  Scenario: Issues closed event is ignored
    Given a webhook payload "stack_issues_closed_ignored"
    When Stack receives the "issues" event
    Then no stack routes are produced

  Scenario: issue_comment with /stack command triggers Stack classification
    Given a webhook payload "stack_issue_comment_stack_command"
    When Stack receives the "issue_comment" event
    Then exactly one stack route is produced
    And the stack route targets the Stack agent

  Scenario: issue_comment with /STACK uppercase command also triggers
    Given a webhook payload "stack_issue_comment_stack_uppercase"
    When Stack receives the "issue_comment" event
    Then exactly one stack route is produced
    And the stack route targets the Stack agent

  Scenario: issue_comment without /stack command is ignored
    Given a webhook payload "stack_issue_comment_no_command"
    When Stack receives the "issue_comment" event
    Then no stack routes are produced

  Scenario: issue_comment edited action is ignored even with /stack in body
    Given a webhook payload "stack_issue_comment_edited_ignored"
    When Stack receives the "issue_comment" event
    Then no stack routes are produced

  Scenario: pull_request event is ignored entirely
    Given a webhook payload "stack_pull_request_opened"
    When Stack receives the "pull_request" event
    Then no stack routes are produced

  Scenario: Unknown event type produces no routes
    Given a webhook payload "stack_issues_unknown_event"
    When Stack receives the "label" event
    Then no stack routes are produced

  # ---------------------------------------------------------------------------
  # PR-linked issue guard
  # ---------------------------------------------------------------------------

  Scenario: Issue that is a pull_request is skipped
    Given a webhook payload "stack_issues_pr_linked"
    When Stack receives the "issues" event
    Then no stack routes are produced

  Scenario: /stack comment on a PR conversation is skipped (issue.pull_request set)
    Given a webhook payload "stack_issue_comment_on_pr"
    When Stack receives the "issue_comment" event
    Then no stack routes are produced

  # ---------------------------------------------------------------------------
  # Successful classification — stack_classified outcome
  # ---------------------------------------------------------------------------

  Scenario: Classified issue produces stack_classified status
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack validation status is "stack_classified"
    And the stack validation conclusion is "success"
    And the stack writeback adds reaction "rocket"
    And the stack writeback removes reaction "eyes"

  Scenario: Classified issue has exactly four axis labels added
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack writeback adds exactly four labels
    And the stack writeback removes "stack-needs-review"

  Scenario: Classified issue includes the issue number in the validation
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack validation includes the issue number

  # ---------------------------------------------------------------------------
  # Needs-review outcome — stack_needs_review
  # ---------------------------------------------------------------------------

  Scenario: Low-confidence issue produces stack_needs_review status
    Given a webhook payload "stack_issues_opened_needs_review"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack validation status is "stack_needs_review"
    And the stack validation conclusion is "neutral"
    And the stack writeback adds label "stack-needs-review"
    And the stack writeback adds reaction "eyes"
    And the stack writeback removes reaction "rocket"

  Scenario: Needs-review issue removes all axis labels
    Given a webhook payload "stack_issues_opened_needs_review"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack writeback removes "stack-type-task"
    And the stack writeback removes "stack-risk-low"

  Scenario: Needs-review comment includes suggested classification
    Given a webhook payload "stack_issues_opened_needs_review"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack writeback comment includes "Suggested classification:"

  Scenario: Needs-review comment includes review reasons
    Given a webhook payload "stack_issues_opened_needs_review"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack writeback comment includes "Review reasons:"

  # ---------------------------------------------------------------------------
  # Type classification sources
  # ---------------------------------------------------------------------------

  Scenario: Explicit Work Type field wins over title kind
    Given a webhook payload "stack_issues_explicit_work_type"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack classification type is "refactor"
    And the stack classification type source is "explicit_field"

  Scenario: Issue title kind maps to stack type when no explicit field
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack classification type is "feature"

  Scenario: issue_comment /stack command classifies the linked issue type
    Given a webhook payload "stack_issue_comment_stack_command"
    When Stack receives the "issue_comment" event
    Then exactly one stack route is produced
    And the stack classification type is "docs"

  # ---------------------------------------------------------------------------
  # Area classification sources
  # ---------------------------------------------------------------------------

  Scenario: Explicit Stack Area field overrides weighted signals
    Given a webhook payload "stack_issues_explicit_area_override"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack classification area is "infra"
    And the stack area source is "explicit_field"

  Scenario: Weighted signals select highest-scoring area
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack classification area is "github"

  Scenario: Automation signals score highest for orchestrator issue
    Given a webhook payload "stack_issues_explicit_work_type"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack classification area is "automation"
    And the stack area source is "weighted_signals"

  # ---------------------------------------------------------------------------
  # Risk classification
  # ---------------------------------------------------------------------------

  Scenario: High-risk keywords elevate risk to high
    Given a webhook payload "stack_issues_opened_high_risk"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack classification risk is "high"

  Scenario: Needs-review issue with placeholder body flags body reason
    Given a webhook payload "stack_issues_opened_needs_review"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack confidence needs_review is true

  # ---------------------------------------------------------------------------
  # Empty / null body handling
  # ---------------------------------------------------------------------------

  Scenario: Null body issue still routes and requires review
    Given a webhook payload "stack_issues_opened_empty_body"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack validation status is "stack_needs_review"

  # ---------------------------------------------------------------------------
  # Route shape
  # ---------------------------------------------------------------------------

  Scenario: Route kind is stack_classification
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack route kind is "stack_classification"

  Scenario: Route includes writeback comment marker
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack route includes the comment marker

  Scenario: Route includes writeback label and reaction dicts
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack writeback has label add and remove keys
    And the stack writeback has reaction add and remove keys

  Scenario: issue_comment route carries the original event name
    Given a webhook payload "stack_issue_comment_stack_command"
    When Stack receives the "issue_comment" event
    Then exactly one stack route is produced
    And the stack route event is "issue_comment"
    And the stack route action is "created"

  Scenario: Classified comment body includes status line
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack writeback comment includes "Status: stack-classified"

  Scenario: Classified comment body lists applied labels
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack writeback comment includes "Applied labels:"

  Scenario: Classifier version is recorded in the validation
    Given a webhook payload "stack_issues_opened_classified"
    When Stack receives the "issues" event
    Then exactly one stack route is produced
    And the stack validation classifier is "stack-v2"
