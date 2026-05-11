Feature: Blueprint bot — issue intake validation and routing

  As the Iterwheel pipeline's Blueprint stage
  I want to validate GitHub issue structure and route it for design review
  So that only well-formed issues enter the agent work queue

  Background:
    Given the Blueprint agent slug is "iterwheel-blueprint"

  # ---------------------------------------------------------------------------
  # Event routing gate — should_run_blueprint
  # ---------------------------------------------------------------------------

  Scenario: Issues opened event triggers Blueprint validation
    Given a webhook payload "issues_opened_valid"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route targets the Blueprint agent

  Scenario: Issues edited event triggers Blueprint validation
    Given a webhook payload "issues_edited_valid"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route targets the Blueprint agent

  Scenario: Issues reopened event triggers Blueprint validation
    Given a webhook payload "issues_reopened_valid"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route targets the Blueprint agent

  Scenario: Issues closed event is ignored
    Given a webhook payload "issues_closed_ignored"
    When Blueprint receives the "issues" event
    Then no routes are produced

  Scenario: issue_comment with /blueprint command triggers Blueprint validation
    Given a webhook payload "issue_comment_blueprint_command"
    When Blueprint receives the "issue_comment" event
    Then exactly one route is produced
    And the route targets the Blueprint agent

  Scenario: issue_comment with /BLUEPRINT uppercase command also triggers
    Given a webhook payload "issue_comment_blueprint_uppercase"
    When Blueprint receives the "issue_comment" event
    Then exactly one route is produced
    And the route targets the Blueprint agent

  Scenario: issue_comment without /blueprint command is ignored
    Given a webhook payload "issue_comment_no_command"
    When Blueprint receives the "issue_comment" event
    Then no routes are produced

  Scenario: issue_comment edited action is ignored even with /blueprint in body
    Given a webhook payload "issue_comment_edited_ignored"
    When Blueprint receives the "issue_comment" event
    Then no routes are produced

  Scenario: pull_request event is ignored entirely
    Given a webhook payload "pull_request_opened"
    When Blueprint receives the "pull_request" event
    Then no routes are produced

  Scenario: Unknown event type produces no routes
    Given a webhook payload "check_suite_completed"
    When Blueprint receives the "check_suite" event
    Then no routes are produced

  # ---------------------------------------------------------------------------
  # PR-linked issue guard
  # ---------------------------------------------------------------------------

  Scenario: Issue that is a pull_request is skipped
    Given a webhook payload "issues_pr_linked"
    When Blueprint receives the "issues" event
    Then no routes are produced

  Scenario: /blueprint comment on a PR conversation is skipped (issue.pull_request set)
    Given a webhook payload "issue_comment_blueprint_command_on_pr"
    When Blueprint receives the "issue_comment" event
    Then no routes are produced

  # ---------------------------------------------------------------------------
  # Valid issue — blueprint_ready outcome
  # ---------------------------------------------------------------------------

  Scenario: Fully valid issue produces blueprint_ready status
    Given a webhook payload "issues_opened_valid"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_ready"
    And the route validation conclusion is "success"
    And the route validation has no missing fields
    And the route validation has no weak fields
    And the route writeback adds label "blueprint-ready"
    And the route writeback removes label "blueprint-needed"
    And the route writeback removes label "blueprint-requests-revision"
    And the route writeback adds reaction "rocket"

  Scenario: Valid issue includes the issue number in the route
    Given a webhook payload "issues_opened_valid"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation includes the issue number

  # ---------------------------------------------------------------------------
  # Missing / weak sections — blueprint_requests_revision outcome
  # ---------------------------------------------------------------------------

  Scenario: Issue with missing sections produces blueprint_requests_revision
    Given a webhook payload "issues_opened_missing_sections"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_requests_revision"
    And the route validation conclusion is "failure"
    And the route writeback adds label "blueprint-requests-revision"
    And the route writeback removes label "blueprint-needed"
    And the route writeback removes label "blueprint-ready"
    And the route writeback removes reaction "rocket"

  Scenario: Issue body is null produces revision with all sections missing
    Given a webhook payload "issues_opened_empty_body"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_requests_revision"

  # ---------------------------------------------------------------------------
  # Title validation
  # ---------------------------------------------------------------------------

  Scenario: Issue title without Blueprint kind prefix is flagged as weak
    Given a webhook payload "issues_opened_bad_title_format"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_requests_revision"
    And the field "Title format" is in the route validation weak list
    And the route writeback comment includes title guidance

  Scenario: Issue with empty title is flagged as missing
    Given a webhook payload "issues_opened_empty_title"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_requests_revision"
    And the field "Title" is in the route validation missing list

  # ---------------------------------------------------------------------------
  # Acceptance Criteria special validation
  # ---------------------------------------------------------------------------

  Scenario: Missing Acceptance Criteria section triggers AC guidance in comment
    Given a webhook payload "issues_opened_missing_ac"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_requests_revision"
    And the field "Acceptance Criteria" is in the route validation missing list
    And the route writeback comment includes acceptance criteria guidance

  Scenario: Acceptance Criteria present as prose only (no list items) is flagged as weak
    Given a webhook payload "issues_opened_ac_no_list_items"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_requests_revision"
    And the field "Acceptance Criteria" is in the route validation weak list
    And the route writeback comment includes acceptance criteria guidance

  # ---------------------------------------------------------------------------
  # Nested subheadings
  # ---------------------------------------------------------------------------

  Scenario: Nested subheadings inside a section are captured within that section
    Given a webhook payload "issues_opened_nested_subheadings"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route validation status is "blueprint_ready"
    And the section "Problem / Goal" is present in sections found

  # ---------------------------------------------------------------------------
  # Route shape
  # ---------------------------------------------------------------------------

  Scenario: Route includes event metadata and writeback fields
    Given a webhook payload "issues_opened_valid"
    When Blueprint receives the "issues" event
    Then exactly one route is produced
    And the route kind is "issue_blueprint_validation"
    And the route includes a writeback comment marker
    And the route includes writeback label changes
    And the route includes writeback reaction changes

  Scenario: issue_comment route carries the original event name
    Given a webhook payload "issue_comment_blueprint_command"
    When Blueprint receives the "issue_comment" event
    Then exactly one route is produced
    And the route event is "issue_comment"
    And the route action is "created"
