Feature: Assembly bot — independent cross-test scenarios

  As the Iterwheel pipeline's Assembly stage
  I want to verify alternate command syntax, PR-disguised-as-issue refusal,
  and multi-line command matching
  So that the implementation is tested from a different angle than the
  primary BDD suite.

  Background:
    Given the Assembly agent slug is "iterwheel-assembly"

  # ---------------------------------------------------------------------------
  # Scenario 1: /implement (alternate command) on a ready issue, dry-run
  # ---------------------------------------------------------------------------

  Scenario: /implement command on a ready issue builds a dry-run contract
    Given a webhook payload "assembly_xtest_implement_command"
    When Assembly receives the "issue_comment" event
    Then exactly one assembly route is produced
    And the route targets the Assembly agent
    And the route kind is "assembly_implementation"
    And the route validation status is "assembly_ready"
    And the route validation conclusion is "success"
    And the route validation command is "/implement"
    And the route writeback includes a contract dict
    And the route writeback has dynamic "assembly_implementation"

  # ---------------------------------------------------------------------------
  # Scenario 2: Issue with PR payload field refused
  # ---------------------------------------------------------------------------

  Scenario: An issue with pull_request key set is refused as "pr_not_issue"
    Given a webhook payload "assembly_xtest_pr_disguised_as_issue"
    When Assembly receives the "issue_comment" event
    Then exactly one assembly route is produced
    And the route validation status is "assembly_refused"
    And the route validation conclusion is "neutral"
    And the route validation refusal reason is "pr_not_issue"
    And the route writeback refusal has reason "pr_not_issue"

  # ---------------------------------------------------------------------------
  # Scenario 3: Command on a non-first line matches
  # ---------------------------------------------------------------------------

  Scenario: /assembly on the second line of a comment body matches
    Given a webhook payload "assembly_xtest_command_on_second_line"
    When Assembly receives the "issue_comment" event
    Then exactly one assembly route is produced
    And the route validation status is "assembly_ready"
    And the route validation command is "/assembly"
