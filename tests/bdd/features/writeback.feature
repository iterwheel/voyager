Feature: apply_route_writeback — write GitHub labels/reactions/comments

  As the Voyager writeback engine
  I want to apply route writeback actions to GitHub issues
  So that routed events update labels, reactions, and comments in the target repo

  Scenario: DRY_RUN=true returns planned dict without API calls
    Given a writeback client with a recording transport
    And a route for "iterwheel-blueprint" on issue 42 with add label "backlog" and remove label "triage"
    And DRY_RUN is "true"
    When apply_route_writeback is called with repository "iterwheel/voyager-sandbox"
    Then the result has applied false
    And the result has dry_run true
    And the result planned add_labels contains "backlog"
    And the result planned remove_labels contains "triage"
    And no HTTP requests were made

  Scenario: DRY_RUN=false applies label changes in correct order
    Given a writeback client with a recording transport for label changes
    And a route for "iterwheel-blueprint" on issue 42 with add label "backlog" and remove label "triage"
    And DRY_RUN is "false"
    When apply_route_writeback is called with repository "iterwheel/voyager-sandbox"
    Then the result has applied true
    And the result has dry_run false
    And the first non-token request is a DELETE to remove the label
    And a subsequent request is a POST to add labels

  Scenario: DRY_RUN=false applies reactions in correct order
    Given a writeback client with a recording transport for reaction changes
    And a route for "iterwheel-blueprint" on issue 42 with remove reaction "eyes" and add reaction "rocket"
    And DRY_RUN is "false"
    When apply_route_writeback is called with repository "iterwheel/voyager-sandbox"
    Then the result has applied true
    And a DELETE request was made for the reaction
    And a POST request was made to add the reaction

  Scenario: comment_mode upsert invokes upsert_issue_comment path
    Given a writeback client with a recording transport for upsert comment
    And a route for "iterwheel-blueprint" on issue 42 with comment body "Hello" marker "<!-- bot -->" mode "upsert"
    And DRY_RUN is "false"
    When apply_route_writeback is called with repository "iterwheel/voyager-sandbox"
    Then the result has applied true
    And the result comment_url is set

  Scenario: comment_mode append invokes create_issue_comment path
    Given a writeback client with a recording transport for append comment
    And a route for "iterwheel-blueprint" on issue 42 with comment body "Hello" marker "" mode "append"
    And DRY_RUN is "false"
    When apply_route_writeback is called with repository "iterwheel/voyager-sandbox"
    Then the result has applied true
    And a POST request was made to create the comment directly

  Scenario: Route with no repository is skipped
    Given a writeback client with a recording transport
    And a route for "iterwheel-blueprint" on issue 42 with add label "backlog" and remove label "triage"
    And DRY_RUN is "false"
    When apply_route_writeback is called with repository None
    Then the result has applied false
    And the result reason mentions "repository"

  Scenario: Route with no issue_number is skipped
    Given a writeback client with a recording transport
    And a route for "iterwheel-blueprint" with no issue_number
    And DRY_RUN is "false"
    When apply_route_writeback is called with repository "iterwheel/voyager-sandbox"
    Then the result has applied false
    And the result reason mentions "issue number"

  # ---------------------------------------------------------------------------
  # dispatch_route_writeback — Codex round 1 P1 (PR #7):
  # Clearance routes carry only {"dynamic": "clearance_readiness"} in writeback;
  # they MUST be enriched first via enrich_clearance_route before apply.
  # ---------------------------------------------------------------------------

  Scenario: Dispatch with a clearance dynamic route and no repository is skipped
    Given a writeback client with a recording transport
    And a clearance dynamic route on PR 42
    And DRY_RUN is "false"
    When dispatch_route_writeback is called with repository None
    Then the result has applied false
    And the result reason mentions "Clearance enrichment"

  Scenario: Dispatch with a clearance dynamic route enriches before applying
    Given a writeback client with a recording transport for label changes
    And a clearance dynamic route on PR 42
    And enrich_clearance_route is stubbed to return a concrete writeback
    And DRY_RUN is "false"
    When dispatch_route_writeback is called with repository "iterwheel/voyager-sandbox"
    Then the result has applied true
    And the result planned add_labels contains "clearance-ready"
    And the result planned remove_labels contains "clearance-pending"
