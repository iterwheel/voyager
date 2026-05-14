Feature: SWM classify — Codex thread state classification

  As the voyager clearance bot
  I want to classify Codex review threads into states A/B/C
  So that downstream judge logic can apply the correct verdict rules

  Background:
    Given the classify module is available

  # ---------------------------------------------------------------------------
  # classify_thread — dominant state
  # ---------------------------------------------------------------------------

  Scenario: Fresh thread with no reply is state A
    Given a thread with no author reply and not outdated
    When the thread is classified
    Then the thread state is "A"

  Scenario: Outdated thread is state B regardless of replies
    Given a thread that is outdated with an author reply
    When the thread is classified
    Then the thread state is "B"

  Scenario: Thread with author reply and not outdated is state C
    Given a thread with an author reply and not outdated
    When the thread is classified
    Then the thread state is "C"

  # ---------------------------------------------------------------------------
  # is_codex_thread
  # ---------------------------------------------------------------------------

  Scenario: Thread whose first comment is by Codex bot is a Codex thread
    Given a thread whose first comment author is "chatgpt-codex-connector"
    When is_codex_thread is called
    Then the result is true

  Scenario: Thread whose first comment is by a human is not a Codex thread
    Given a thread whose first comment author is "ryosaeba1985"
    When is_codex_thread is called
    Then the result is false

  Scenario: Thread with no comments is not a Codex thread
    Given a thread with no comments
    When is_codex_thread is called
    Then the result is false

  # ---------------------------------------------------------------------------
  # codex_comment_id
  # ---------------------------------------------------------------------------

  Scenario: codex_comment_id returns the first comment's databaseId
    Given a thread whose first comment has databaseId 42
    When codex_comment_id is called
    Then the comment id is 42

  # ---------------------------------------------------------------------------
  # latest_author_reply
  # ---------------------------------------------------------------------------

  Scenario: latest_author_reply skips Codex follow-up comments
    Given a thread with a human reply followed by a Codex follow-up
    When latest_author_reply is called
    Then the latest reply databaseId is 2

  Scenario: latest_author_reply skips legacy SWM marker comments
    Given a thread with a SWM marker comment followed by a human reply
    When latest_author_reply is called
    Then the latest reply databaseId is 10

  Scenario: latest_author_reply skips Clearance marker comments
    Given a thread with a Clearance marker comment followed by a human reply
    When latest_author_reply is called
    Then the latest reply databaseId is 10

  Scenario: latest_author_reply returns None when only legacy SWM markers are present
    Given a thread with only a SWM marker comment and no human reply
    When latest_author_reply is called
    Then the latest author reply is None

  Scenario: latest_author_reply returns None when only Clearance markers are present
    Given a thread with only a Clearance marker comment and no human reply
    When latest_author_reply is called
    Then the latest author reply is None

  # ---------------------------------------------------------------------------
  # latest_codex_followup
  # ---------------------------------------------------------------------------

  Scenario: latest_codex_followup returns the most recent Codex reply
    Given a thread with two Codex follow-up comments with ids 3 and 4
    When latest_codex_followup is called
    Then the followup databaseId is 4

  # ---------------------------------------------------------------------------
  # codex_pr_body_signal
  # ---------------------------------------------------------------------------

  Scenario: THUMBS_UP reaction by Codex bot means approved
    Given PR body reactions with THUMBS_UP from "chatgpt-codex-connector[bot]"
    When codex_pr_body_signal is called
    Then the signal is "approved"

  Scenario: EYES reaction by Codex bot means reviewing
    Given PR body reactions with EYES from "chatgpt-codex-connector[bot]"
    When codex_pr_body_signal is called
    Then the signal is "reviewing"

  Scenario: THUMBS_UP wins over EYES during transition window
    Given PR body reactions with both EYES and THUMBS_UP from "chatgpt-codex-connector[bot]"
    When codex_pr_body_signal is called
    Then the signal is "approved"

  Scenario: Reactions from non-Codex users are ignored
    Given PR body reactions with THUMBS_UP from "ryosaeba1985"
    When codex_pr_body_signal is called
    Then the signal is None

  Scenario: Empty reactions list returns None signal
    Given an empty PR body reactions list
    When codex_pr_body_signal is called
    Then the signal is None

  Scenario: GraphQL login form (without [bot] suffix) is accepted
    Given PR body reactions with THUMBS_UP from "chatgpt-codex-connector"
    When codex_pr_body_signal is called
    Then the signal is "approved"

  # ---------------------------------------------------------------------------
  # VOYAGER_TEST_BOT_LOGINS bypass (sandbox e2e harness only)
  # ---------------------------------------------------------------------------

  Scenario: Thread whose first comment is by a TEST_BOT_LOGIN is a Codex thread
    Given VOYAGER_TEST_BOT_LOGINS env is set to "voyager-e2e-bot"
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is true

  Scenario: TEST_BOT_LOGINS unset means non-Codex first commenters are not Codex
    Given VOYAGER_TEST_BOT_LOGINS env is not set
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is false

  Scenario: TEST_BOT_LOGINS supports multiple comma-separated logins
    Given VOYAGER_TEST_BOT_LOGINS env is set to "bot-a,bot-b, bot-c"
    And a thread whose first comment author is "bot-b"
    When is_codex_thread is called
    Then the result is true

  Scenario: TEST_BOT_LOGINS reaction THUMBS_UP signals approved
    Given VOYAGER_TEST_BOT_LOGINS env is set to "voyager-e2e-bot"
    And PR body reactions with THUMBS_UP from "voyager-e2e-bot"
    When codex_pr_body_signal is called
    Then the signal is "approved"

  Scenario: TEST_BOT_LOGINS follow-up comment is recognized as Codex follow-up
    Given VOYAGER_TEST_BOT_LOGINS env is set to "voyager-e2e-bot"
    And a thread with a test-bot follow-up comment id 7
    When latest_codex_followup is called
    Then the followup databaseId is 7

  Scenario: TEST_BOT_LOGINS reply is excluded from latest_author_reply
    Given VOYAGER_TEST_BOT_LOGINS env is set to "voyager-e2e-bot"
    And a thread with a test-bot reply followed by a human reply id 5
    When latest_author_reply is called
    Then the latest reply databaseId is 5

  # ---------------------------------------------------------------------------
  # VOYAGER_TEST_BOT_LOGINS env-parse edge cases (round-1 P2: 3/4 reviewers)
  # ---------------------------------------------------------------------------

  Scenario: Empty VOYAGER_TEST_BOT_LOGINS string yields no extras
    Given VOYAGER_TEST_BOT_LOGINS env is set to ""
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is false

  Scenario: Whitespace-only VOYAGER_TEST_BOT_LOGINS yields no extras
    Given VOYAGER_TEST_BOT_LOGINS env is set to "   "
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is false

  Scenario: Comma-only VOYAGER_TEST_BOT_LOGINS yields no extras
    Given VOYAGER_TEST_BOT_LOGINS env is set to ",,,"
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is false

  Scenario: Trailing comma in VOYAGER_TEST_BOT_LOGINS is ignored
    Given VOYAGER_TEST_BOT_LOGINS env is set to "voyager-e2e-bot,"
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is true

  Scenario: Empty parts between commas in VOYAGER_TEST_BOT_LOGINS are dropped
    Given VOYAGER_TEST_BOT_LOGINS env is set to "a,,voyager-e2e-bot,,b"
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is true

  Scenario: Duplicate logins in VOYAGER_TEST_BOT_LOGINS are accepted (frozenset dedup)
    Given VOYAGER_TEST_BOT_LOGINS env is set to "voyager-e2e-bot,voyager-e2e-bot"
    And a thread whose first comment author is "voyager-e2e-bot"
    When is_codex_thread is called
    Then the result is true

  Scenario: VOYAGER_TEST_BOT_LOGINS match is case-sensitive (GitHub logins are case-sensitive)
    Given VOYAGER_TEST_BOT_LOGINS env is set to "voyager-e2e-bot"
    And a thread whose first comment author is "Voyager-E2E-Bot"
    When is_codex_thread is called
    Then the result is false
