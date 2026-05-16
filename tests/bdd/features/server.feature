Feature: Webhook server — HTTP entry point for GitHub events

  As the Iterwheel bot platform
  I want to receive GitHub webhook events over HTTP, verify their signatures, and dispatch them to bots
  So that only authenticated events reach the agent work queue

  Background:
    Given the webhook secret "test-secret-abc" is configured for slug "test-app"
    And the webhook endpoint is "POST /github/webhook"

  # ---------------------------------------------------------------------------
  # Health check endpoints
  # ---------------------------------------------------------------------------

  Scenario: Root endpoint returns ok with service name
    When a GET request is made to "/"
    Then the response status is 200
    And the response body contains key "ok" with value true
    And the response body contains key "service" with value "iterwheel-github-bridge"

  Scenario: Healthz endpoint returns ok with service name
    When a GET request is made to "/healthz"
    Then the response status is 200
    And the response body contains key "ok" with value true
    And the response body contains key "service" with value "iterwheel-github-bridge"

  Scenario: Healthz response includes time field
    When a GET request is made to "/healthz"
    Then the response status is 200
    And the response body has a "time" field

  Scenario: Healthz response includes dry_run field
    When a GET request is made to "/healthz"
    Then the response status is 200
    And the response body has a "dry_run" field

  # ---------------------------------------------------------------------------
  # Signature verification — match_signature unit tests
  # ---------------------------------------------------------------------------

  Scenario: match_signature returns the slug for a correctly signed body
    Given body b"hello" signed with secret "s3cr3t" under slug "my-app"
    When match_signature is called with that body, signature, and secrets
    Then the returned slug is "my-app"

  Scenario: match_signature returns None when signature is wrong
    Given body b"hello" signed with secret "s3cr3t" under slug "my-app"
    When match_signature is called with a tampered signature
    Then the returned slug is None

  Scenario: match_signature returns None when signature is missing
    Given any body and secrets dict with one entry
    When match_signature is called with signature None
    Then the returned slug is None

  Scenario: match_signature uses constant-time comparison
    Given body b"data" signed with secret "abc" under slug "app-a"
    When match_signature is called with that body, signature, and secrets
    Then the returned slug is "app-a"

  Scenario: match_signature signature format is sha256= prefix plus hex digest
    Given body b"msg" signed with secret "key" under slug "app-b"
    When github_signature is computed for that body and secret
    Then the signature starts with "sha256="
    And the part after "sha256=" is 64 hex characters

  # ---------------------------------------------------------------------------
  # POST /github/webhook — signature acceptance / rejection
  # ---------------------------------------------------------------------------

  Scenario: Valid signed payload is accepted and returns ok
    Given a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-001"
    When the webhook is POSTed
    Then the response status is 200
    And the response body contains key "ok" with value true

  Scenario: Valid signed payload returns the matched app slug
    Given a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-002"
    When the webhook is POSTed
    Then the response status is 200
    And the response body contains key "app" with value "test-app"

  Scenario: Valid signed payload echoes the event name
    Given a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-003"
    When the webhook is POSTed
    Then the response status is 200
    And the response body contains key "event" with value "issues"

  Scenario: Valid signed payload echoes the delivery ID
    Given a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-004"
    When the webhook is POSTed
    Then the response status is 200
    And the response body contains key "delivery_id" with value "abc-004"

  Scenario: Invalid signature is rejected with 401
    Given a webhook payload "server_issues_opened" with a wrong signature for event "issues" with delivery "abc-010"
    When the webhook is POSTed
    Then the response status is 401

  Scenario: Missing signature header is rejected with 401
    Given a webhook payload "server_issues_opened" with no signature header for event "issues" with delivery "abc-011"
    When the webhook is POSTed
    Then the response status is 401

  Scenario: No webhook secrets configured returns 503
    Given no webhook secrets are configured
    And a raw signed payload for event "issues" with delivery "abc-012"
    When the webhook is POSTed
    Then the response status is 503

  # ---------------------------------------------------------------------------
  # POST /github/webhook — request shape validation
  # ---------------------------------------------------------------------------

  Scenario: Missing X-GitHub-Delivery header is rejected with 400
    Given a signed webhook payload "server_issues_opened" for event "issues" with no delivery header
    When the webhook is POSTed
    Then the response status is 400

  Scenario: Malformed JSON body is rejected with 400
    Given a signed non-JSON body for event "issues" with delivery "abc-020"
    When the webhook is POSTed
    Then the response status is 400

  Scenario: Missing X-GitHub-Event header is rejected with 400
    Given a signed webhook payload "server_issues_opened" with no event header and delivery "abc-021"
    When the webhook is POSTed
    Then the response status is 400

  # ---------------------------------------------------------------------------
  # Dispatch routing — response shape
  # ---------------------------------------------------------------------------

  Scenario: Issues event produces routes and sets queued to true
    Given a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-030"
    When the webhook is POSTed
    Then the response status is 200
    And the response body "queued" field is true
    And the response body "routes" field is a list

  Scenario: Unknown event type is accepted with 200 and queued is false
    Given a signed webhook payload "server_unknown_event" for event "release" with delivery "abc-040"
    When the webhook is POSTed
    Then the response status is 200
    And the response body "queued" field is false
    And the response body "routes" field is an empty list

  Scenario: Accepted webhook with no routes reports zero scheduled writebacks
    Given a signed webhook payload "server_unknown_event" for event "release" with delivery "abc-041"
    When the webhook is POSTed
    Then the response status is 200
    And the response body "writebacks" field has "status" equal to string "queued"
    And the response body "writebacks" field has "scheduled" equal to integer 0

  Scenario: Accepted webhook with routes reports queued writebacks with scheduled count
    Given a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-042"
    When the webhook is POSTed
    Then the response status is 200
    And the response body "writebacks" field has "status" equal to string "queued"
    And the response body "writebacks" field has "scheduled" greater than 0

  Scenario: Production webhook filters routes outside the repository allow-list
    Given DRY_RUN is "false"
    And bridge allowed repositories is "iterwheel/voyager-sandbox"
    And a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-043"
    When the webhook is POSTed
    Then the response status is 200
    And the response body "queued" field is false
    And the response body "routes" field is an empty list
    And the response body "writebacks" field has "scheduled" equal to integer 0
    And the response body "filtered" field has "status" equal to string "repository_not_allowed"
    And the response body "filtered" field has "count" greater than 0

  Scenario: Production webhook allows routes for an explicitly allow-listed repository
    Given DRY_RUN is "false"
    And bridge allowed repositories is "test-org/test-repo"
    And a signed webhook payload "server_issues_opened" for event "issues" with delivery "abc-044"
    When the webhook is POSTed
    Then the response status is 200
    And the response body "queued" field is true
    And the response body "writebacks" field has "scheduled" greater than 0
    And the response body "filtered" field has "count" equal to integer 0

  # ---------------------------------------------------------------------------
  # Dispatch routing — event-to-bot mapping (issues event)
  # ---------------------------------------------------------------------------

  Scenario: issues event routes include a Blueprint agent route
    Given a signed webhook payload "issues_opened_valid" for event "issues" with delivery "abc-050"
    When the webhook is POSTed
    Then the response status is 200
    And at least one route targets agent "iterwheel-blueprint"

  Scenario: issues event routes include a Stack agent route
    Given a signed webhook payload "issues_opened_valid" for event "issues" with delivery "abc-051"
    When the webhook is POSTed
    Then the response status is 200
    And at least one route targets agent "iterwheel-stack"

  # ---------------------------------------------------------------------------
  # Dispatch routing — event-to-bot mapping (issue_comment event)
  # ---------------------------------------------------------------------------

  Scenario: issue_comment with blueprint command routes to Blueprint agent
    Given a signed webhook payload "issue_comment_blueprint_command" for event "issue_comment" with delivery "abc-060"
    When the webhook is POSTed
    Then the response status is 200
    And at least one route targets agent "iterwheel-blueprint"

  Scenario: issue_comment with stack command routes to Stack agent
    Given a signed webhook payload "stack_issue_comment_stack_command" for event "issue_comment" with delivery "abc-061"
    When the webhook is POSTed
    Then the response status is 200
    And at least one route targets agent "iterwheel-stack"

  Scenario: issue_comment without a recognized command produces no routes
    Given a signed webhook payload "issue_comment_no_command" for event "issue_comment" with delivery "abc-062"
    When the webhook is POSTed
    Then the response status is 200
    And the response body "queued" field is false
