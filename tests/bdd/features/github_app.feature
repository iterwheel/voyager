Feature: GitHub App authentication — JWT and installation token machinery

  As the Iterwheel bot platform
  I want to authenticate to GitHub as a GitHub App installation
  So that bots can make authenticated API calls on behalf of each installation

  Background:
    Given a test GitHub App with slug "test-bot" and app_id "12345"
    And the app has a valid RSA private key
    And the app has installation_id "99887766"

  # ---------------------------------------------------------------------------
  # JWT generation
  # ---------------------------------------------------------------------------

  Scenario: JWT is signed with RS256 algorithm
    When a JWT is generated for the app
    Then the JWT header algorithm is "RS256"

  Scenario: JWT claims include iss set to the app_id
    When a JWT is generated for the app
    Then the JWT claim "iss" equals "12345"

  Scenario: JWT iat is 60 seconds before now
    When a JWT is generated for the app
    Then the JWT iat is approximately 60 seconds before now

  Scenario: JWT exp is approximately 9 minutes after now
    When a JWT is generated for the app
    Then the JWT exp is within 10 minutes from now

  Scenario: JWT generation fails when private key file does not exist
    Given the private key file does not exist
    When a JWT generation is attempted for the app
    Then a RuntimeError is raised mentioning "private key not found"

  # ---------------------------------------------------------------------------
  # Installation token request
  # ---------------------------------------------------------------------------

  Scenario: installation_token POSTs to the correct GitHub endpoint
    Given GitHub returns a valid installation token response
    When an installation token is requested
    Then the HTTP call was POST to "/app/installations/99887766/access_tokens"

  Scenario: installation_token request carries Bearer JWT in Authorization header
    Given GitHub returns a valid installation token response
    When an installation token is requested
    Then the request Authorization header starts with "Bearer "

  Scenario: installation_token request carries correct Accept header
    Given GitHub returns a valid installation token response
    When an installation token is requested
    Then the request Accept header is "application/vnd.github+json"

  Scenario: installation_token request carries X-GitHub-Api-Version header
    Given GitHub returns a valid installation token response
    When an installation token is requested
    Then the request X-GitHub-Api-Version header is "2022-11-28"

  Scenario: installation_token returns the token string from the response payload
    Given GitHub returns a valid installation token response
    When an installation token is requested
    Then the returned token is "ghs_test_installation_token_abc123"

  # ---------------------------------------------------------------------------
  # Token caching
  # ---------------------------------------------------------------------------

  Scenario: A second installation_token call within validity window returns the cached token
    Given GitHub returns a valid installation token response
    And an installation token has already been fetched
    When an installation token is requested again
    Then only one HTTP call was made in total
    And the returned token is "ghs_test_installation_token_abc123"

  Scenario: Token with expires_at within 5 minutes triggers a fresh request
    Given GitHub returns a fresh installation token response after an expiring one
    And an installation token with near-expiry has been fetched
    When an installation token is requested again
    Then two HTTP calls were made in total

  # ---------------------------------------------------------------------------
  # Installation ID resolution — per-repo config vs fallback vs discovery
  # ---------------------------------------------------------------------------

  Scenario: App with per-repository installation_id uses it without discovery
    Given the app has repository "test-org/my-repo" mapped to installation_id "55544433"
    And GitHub returns a valid installation token response
    When an installation token is requested for repository "test-org/my-repo"
    Then the HTTP call was POST to "/app/installations/55544433/access_tokens"

  Scenario: App with per-owner installation_id uses it without discovery
    Given the app has owner "test-org" mapped to installation_id "77766655"
    And GitHub returns a valid installation token response
    When an installation token is requested for repository "test-org/other-repo"
    Then the HTTP call was POST to "/app/installations/77766655/access_tokens"

  Scenario: Missing installation_id with no repository raises RuntimeError
    Given the app has no default installation_id
    When an installation token without a repository is attempted
    Then a RuntimeError is raised mentioning "installation_id is not configured"

  # ---------------------------------------------------------------------------
  # Installation discovery via GET /repos/:owner/:name/installation
  # ---------------------------------------------------------------------------

  Scenario: App discovers installation_id from GitHub when not configured
    Given the app has no installation mappings
    And GitHub discovery returns installation_id "99887766" for "test-org/new-repo"
    And GitHub returns a valid installation token response
    When an installation token is requested for repository "test-org/new-repo"
    Then the GET discovery call was made to "/repos/test-org/new-repo/installation"
    And the HTTP call was POST to "/app/installations/99887766/access_tokens"

  Scenario: Discovery returns 404 and installation_id is treated as absent
    Given the app has no installation mappings
    And GitHub discovery returns 404 for "test-org/unknown-repo"
    When an installation token without configured id is attempted for "test-org/unknown-repo"
    Then a RuntimeError is raised mentioning "installation_id is not configured"

  Scenario: Discovered installation_id is cached for subsequent requests
    Given the app has no installation mappings
    And GitHub discovery returns installation_id "99887766" for "test-org/new-repo"
    And GitHub returns a valid installation token response for two calls
    And an installation token has been fetched for repository "test-org/new-repo"
    When an installation token is requested again for repository "test-org/new-repo"
    Then only one discovery GET call was made in total

  # ---------------------------------------------------------------------------
  # Multi-app routing
  # ---------------------------------------------------------------------------

  Scenario: A second app slug uses its own app_id in the JWT iss claim
    Given a second GitHub App with slug "other-bot" and app_id "67890"
    And the second app has a valid RSA private key
    And the second app has installation_id "11223344"
    And GitHub returns a valid installation token response
    When an installation token is requested for app "other-bot"
    Then the JWT iss claim used was "67890"

  # ---------------------------------------------------------------------------
  # Error paths
  # ---------------------------------------------------------------------------

  Scenario: GitHub returns 401 on installation token request and HTTPStatusError is raised
    Given GitHub returns a 401 response on the installation token endpoint
    When an installation token is requested
    Then an httpx.HTTPStatusError is raised

  Scenario: GitHub returns malformed JSON missing the "token" key and KeyError is raised
    Given GitHub returns a malformed JSON response missing the token field
    When an installation token is requested
    Then a KeyError is raised

  # ---------------------------------------------------------------------------
  # Generic request() helper
  # ---------------------------------------------------------------------------

  Scenario: request() acquires an installation token then sends the API call
    Given GitHub returns a valid installation token response
    And GitHub returns a generic 200 JSON response
    When a GET request is made to path "/repos/test-org/my-repo/pulls/1"
    Then the request Authorization header starts with "Bearer ghs_test_installation_token"

  Scenario: request() returns None for HTTP 204 responses
    Given GitHub returns a valid installation token response
    And GitHub returns a 204 No Content response
    When a DELETE request is made to path "/repos/test-org/my-repo/issues/1/labels/foo"
    Then the result is None

  # ---------------------------------------------------------------------------
  # GraphQL helper
  # ---------------------------------------------------------------------------

  Scenario: graphql() raises RuntimeError when response contains errors
    Given GitHub returns a valid installation token response
    And GitHub returns a GraphQL response with errors
    When a GraphQL query is executed
    Then a RuntimeError is raised mentioning "GitHub GraphQL errors"

  # ---------------------------------------------------------------------------
  # PR reviews pagination (Codex round 3)
  # ---------------------------------------------------------------------------

  Scenario: pull_request_reviews fetches all pages when GitHub returns >100 reviews
    Given the app has repository "test-org/my-repo" mapped to installation_id "55544433"
    And GitHub returns a valid installation token response
    And GitHub returns 2 pages of PR reviews with 100 then 50 items
    When pull_request_reviews is called for "test-org/my-repo" PR 42
    Then pull_request_reviews returned 150 items
    And the reviews endpoint was called 2 times

  Scenario: issue_comments fetches all pages when GitHub returns >100 comments
    Given the app has repository "test-org/my-repo" mapped to installation_id "55544433"
    And GitHub returns a valid installation token response
    And GitHub returns 2 pages of issue comments with 100 then 30 items
    When issue_comments is called for "test-org/my-repo" issue 42
    Then issue_comments returned 130 items
    And the comments endpoint was called 2 times

  # ---------------------------------------------------------------------------
  # pull_request_diff (Wave 7B-3)
  # ---------------------------------------------------------------------------

  Scenario: pull_request_diff returns the raw unified diff from the v3.diff endpoint
    Given a test GitHub App with slug "iterwheel-clearance" and app_id "9999"
    And the app has a valid RSA private key
    And the app has installation_id "55544433"
    And the GitHub API returns a token then a 200 diff response with a sample PR diff
    When pull_request_diff is called for "iterwheel/voyager-sandbox" PR 7
    Then the returned diff contains "diff --git a/app.py b/app.py"
    And the returned diff contains "@@ -1,3 +1,4 @@"
    And the captured request URL ends with "/repos/iterwheel/voyager-sandbox/pulls/7"
    And the request Accept header is "application/vnd.github.v3.diff"

  Scenario: pull_request_diff raises HTTPStatusError when the PR is missing
    Given a test GitHub App with slug "iterwheel-clearance" and app_id "9999"
    And the app has a valid RSA private key
    And the app has installation_id "55544433"
    And the GitHub API returns a token then a 404 not-found response
    When pull_request_diff is awaited and may raise
    Then an httpx.HTTPStatusError is raised
