Feature: SWM state store — JSONL append-only poll and thread state

  As the voyager clearance bot
  I want a filesystem-backed state store for polls, threads, and ledger entries
  So that watchdog state is auditable and never mutated in place

  Background:
    Given a temporary StateStore

  # ---------------------------------------------------------------------------
  # polls
  # ---------------------------------------------------------------------------

  Scenario: Appended poll can be read back
    Given a PollRecord for repo "owner/repo" PR 49 with status "pending"
    When the poll is appended and all polls are read
    Then exactly 1 poll is returned
    And the poll repo is "owner/repo"

  Scenario: read_polls filters by repo
    Given polls for repos "owner/repo" and "other/repo"
    When polls are read filtered by repo "owner/repo"
    Then all returned polls belong to repo "owner/repo"

  Scenario: read_polls filters by PR number
    Given polls for PR 49 and PR 50 in "owner/repo"
    When polls are read filtered by PR 50
    Then all returned polls have PR number 50

  Scenario: latest_poll returns the most recently appended poll
    Given two polls for repo "owner/repo" PR 49 with different head_sha values
    When latest_poll is called for "owner/repo" PR 49
    Then the returned poll has the second head_sha

  Scenario: latest_poll returns None for unknown PR
    Given a PollRecord for repo "owner/repo" PR 49 with status "pending"
    When the poll is appended and latest_poll is called for PR 9999
    Then the latest poll is None

  Scenario: latest_per_pr indexes by PR number
    Given polls for PR 49 and PR 50 in "owner/repo"
    When latest_per_pr is called for "owner/repo"
    Then the result has keys 49 and 50

  Scenario: read_polls on missing directory returns empty
    Given no polls have been written
    When all polls are read without filters
    Then the poll list is empty

  Scenario: read_polls skips blank lines in JSONL
    Given two polls written with blank lines injected between them
    When all polls are read without filters
    Then exactly 2 polls are returned

  # ---------------------------------------------------------------------------
  # threads
  # ---------------------------------------------------------------------------

  Scenario: Written thread snapshot can be read back as latest
    Given a ThreadSnapshot for repo "owner/repo" PR 49 thread "PRRT_t1"
    When the thread is written and then read
    Then the read snapshot matches the written snapshot

  Scenario: read_thread returns None for unknown thread
    When read_thread is called for a non-existent thread
    Then the thread result is None

  Scenario: Multiple thread writes create an append-only history
    Given a ThreadSnapshot for repo "owner/repo" PR 49 thread "PRRT_t1"
    When three snapshots with different verdicts are written
    Then the thread history length is 3
    And the latest thread has the third verdict

  Scenario: Thread snapshots are isolated per PR number
    Given two snapshots for the same thread_id but PR 49 and PR 50
    When each is written and read back
    Then pr 49 snapshot has verdict "RESOLVED"
    And pr 50 snapshot has verdict "OPEN"

  Scenario: read_thread returns None when thread file is missing
    When read_thread is called for a non-existent thread
    Then the thread result is None

  # ---------------------------------------------------------------------------
  # ledger
  # ---------------------------------------------------------------------------

  Scenario: Ledger entry round-trips correctly
    Given two LedgerEntries for repo "owner/repo" PR 49
    When both ledger entries are appended
    Then the ledger has 2 entries for "owner/repo" PR 49

  Scenario: read_ledger returns empty for unknown repo
    When the ledger is read for an unknown repo
    Then the ledger list is empty

  Scenario: Ledger accepts extra legacy fields
    Given a raw ledger JSON line with extra fields for "owner/repo" PR 7
    When the ledger is read for "owner/repo" PR 7
    Then the ledger entry has the extra field preserved

  # ---------------------------------------------------------------------------
  # box misses
  # ---------------------------------------------------------------------------

  Scenario: Box miss round-trips correctly
    Given a BoxMiss for repo "owner/repo" PR 49
    When the box miss is appended and read back
    Then exactly 1 box miss is returned for "owner/repo"

  Scenario: read_box_misses filters by repo
    Given box misses for "owner/repo" and "other/repo"
    When box misses are read for "owner/repo"
    Then only box misses for "owner/repo" are returned

  Scenario: read_box_misses returns empty when directory is missing
    When box misses are read for "never/ran"
    Then the box miss list is empty

  # ---------------------------------------------------------------------------
  # path layout
  # ---------------------------------------------------------------------------

  Scenario: PR directory groups polls and threads together
    Given a PollRecord and ThreadSnapshot for "owner/repo" PR 49
    When both are written
    Then the PR directory contains polls.jsonl and a thread file

  # ---------------------------------------------------------------------------
  # crash / concurrency safety
  # ---------------------------------------------------------------------------

  Scenario: read_polls tolerates a half-line from a crashed appender
    Given polls.jsonl for "owner/repo" PR 49 contains one valid record followed by a truncated half-line
    When all polls are read for "owner/repo" PR 49
    Then exactly 1 poll is returned

  Scenario: read_ledger tolerates a malformed line
    Given ledger.jsonl for "owner/repo" PR 7 contains one valid entry and one corrupt line
    When the ledger is re-read after corruption for "owner/repo" PR 7
    Then exactly 1 ledger entry is returned

  Scenario: append_poll persists the line so a reopened reader sees the full record
    Given a PollRecord for repo "owner/repo" PR 49 with status "pending"
    When the poll is appended and the polls file is reopened independently
    Then the on-disk JSONL line round-trips back into a valid PollRecord

  Scenario: append_poll recovers when previous appender crashed without trailing newline
    Given polls.jsonl for "owner/repo" PR 49 ends in a corrupt half-line with no trailing newline
    And a PollRecord for repo "owner/repo" PR 49 with status "pending"
    When the poll is appended after the corrupt tail
    Then exactly 1 poll is returned
