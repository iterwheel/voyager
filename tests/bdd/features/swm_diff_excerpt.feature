Feature: SWM diff_excerpt — extract_anchor_excerpt hunk extraction

  As the voyager clearance bot
  I want to extract the relevant diff hunk(s) for a (path, line) anchor
  So that the LLM investigator receives precise context instead of a full diff

  # ---------------------------------------------------------------------------
  # Edge cases: empty / missing
  # ---------------------------------------------------------------------------

  Scenario: Empty diff returns empty string
    Given an empty diff text
    When extract_anchor_excerpt is called with path "app.py" and line 1
    Then the excerpt is ""

  Scenario: Path not in diff returns empty string
    Given a single-file diff for "other.py"
    When extract_anchor_excerpt is called with path "app.py" and line 1
    Then the excerpt is ""

  # ---------------------------------------------------------------------------
  # Single-hunk diff — basic line matching and line=None
  # ---------------------------------------------------------------------------

  Scenario: Single hunk containing the line is returned
    Given a single-hunk diff for "app.py" covering new lines 1 to 4
    When extract_anchor_excerpt is called with path "app.py" and line 2
    Then the excerpt contains "@@ -1,3 +1,4 @@"
    And the excerpt contains "+added line"

  Scenario: line=None returns all hunks for the path
    Given a single-hunk diff for "app.py" covering new lines 1 to 4
    When extract_anchor_excerpt is called with path "app.py" and line None
    Then the excerpt contains "@@ -1,3 +1,4 @@"
    And the excerpt contains "+added line"

  # ---------------------------------------------------------------------------
  # Multi-hunk diff — line in first hunk only
  # ---------------------------------------------------------------------------

  Scenario: Multi-hunk diff returns only the hunk containing the line
    Given a multi-hunk diff for "app.py" with hunk1 at new lines 3 to 7 and hunk2 at new lines 15 to 18
    When extract_anchor_excerpt is called with path "app.py" and line 5
    Then the excerpt contains "@@ -3,4 +3,4 @@"
    And the excerpt does not contain "@@ -15,3 +15,3 @@"

  # ---------------------------------------------------------------------------
  # Line outside every hunk — fallback to all hunks
  # ---------------------------------------------------------------------------

  Scenario: Line not in any hunk triggers fallback to all hunks
    Given a multi-hunk diff for "app.py" with hunk1 at new lines 3 to 7 and hunk2 at new lines 15 to 18
    When extract_anchor_excerpt is called with path "app.py" and line 99
    Then the excerpt contains "@@ -3,4 +3,4 @@"
    And the excerpt contains "@@ -15,3 +15,3 @@"

  # ---------------------------------------------------------------------------
  # Multi-file diff — only the matched file is returned
  # ---------------------------------------------------------------------------

  Scenario: Multi-file diff returns only the matched file's hunks
    Given a three-file diff containing "alpha.py", "beta.py", and "gamma.py"
    When extract_anchor_excerpt is called with path "beta.py" and line 2
    Then the excerpt contains "beta.py"
    And the excerpt does not contain "alpha.py"
    And the excerpt does not contain "gamma.py"

  # ---------------------------------------------------------------------------
  # Renamed file — match on b/ side
  # ---------------------------------------------------------------------------

  Scenario: Renamed file is matched by new (b/) path
    Given a renamed-file diff from "old.py" to "new.py"
    When extract_anchor_excerpt is called with path "new.py" and line 2
    Then the excerpt contains "@@ -1,3 +1,3 @@"
    And the excerpt contains "+new impl"

  # ---------------------------------------------------------------------------
  # Truncation
  # ---------------------------------------------------------------------------

  Scenario: Truncation keeps the line-matching hunk when total exceeds max_chars
    Given a diff for "app.py" with a small matching hunk at line 2 and a huge distant hunk
    When extract_anchor_excerpt is called with path "app.py" and line 2 and max_chars 200
    Then the excerpt contains "@@ -1,3 +1,4 @@"
    And the excerpt does not contain "...[truncated]..."

  Scenario: Single hunk larger than max_chars is truncated with marker
    Given a diff for "app.py" with a single huge hunk
    When extract_anchor_excerpt is called with path "app.py" and line 2 and max_chars 200
    Then the excerpt contains "...[truncated]..."
