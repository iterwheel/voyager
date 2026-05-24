Feature: Assembly bot — code implementation routing and writeback

  As the Iterwheel pipeline's Assembly stage
  I want to handle /assembly slash commands on blueprint-ready issues
  So that approved issues become feature branches and pull requests
  Without ever merging, approving, or applying gating labels

  Background:
    Given the Assembly agent slug is "iterwheel-assembly"

  # ---------------------------------------------------------------------------
  # Scenario 1 — Successful sandbox flow (allow-listed, ready issue, dry-run)
  # ---------------------------------------------------------------------------

  Scenario: /assembly on a ready, allow-listed issue runs dry-run plan
    Given a webhook payload "assembly_command_ready"
    And DRY_RUN is "true"
    And ASSEMBLY_EXECUTION_BACKEND is "dry-run"
    When Assembly receives the "issue_comment" event
    Then exactly one route is produced
    And the route targets the Assembly agent
    And the route writeback is dynamic "assembly_implementation"
    And the route writeback contract has issue number 69
    And the route writeback branch name is "69-implement-assembly-bot-mvp"
    And the route writeback contract forbidden_operations includes "Merge pull requests"
    When Assembly dispatches the route with a mock GitHub client
    Then the dispatcher result has dry_run "true"
    And the dispatcher result adapter_result status is "dry_run"
    And the dispatcher made no GitHub mutations

  # ---------------------------------------------------------------------------
  # Scenario 2 — Refusal on non-blueprint-ready issue posts refusal comment
  # ---------------------------------------------------------------------------

  Scenario: /assembly on non-blueprint-ready issue refuses with a comment
    Given a webhook payload "assembly_command_not_ready"
    And DRY_RUN is "false"
    When Assembly receives the "issue_comment" event
    Then exactly one route is produced
    And the route writeback refusal reason is "missing_blueprint_ready_label"
    When Assembly dispatches the route with a mock GitHub client
    Then the dispatcher upserted exactly one refusal comment
    And the dispatcher made no branch or pull-request writes

  # ---------------------------------------------------------------------------
  # Scenario 3 — Non-allow-listed repository (server-level filter) — modelled
  # here by an empty repository: the bridge's allow-list filter runs before
  # the dispatcher, so the dispatcher itself receives no route.
  # ---------------------------------------------------------------------------

  Scenario: /assembly on a non-allow-listed repository produces no GitHub writes
    Given a webhook payload "assembly_command_ready"
    And the repository allow-list is empty
    When the bridge filters routes by repository
    Then the Assembly route is denied
    And the dispatcher is never called

  # ---------------------------------------------------------------------------
  # Scenario 4 — /assembly --allow-missing-stack on blueprint-ready issue
  # without stack-type-* still builds the contract.
  # ---------------------------------------------------------------------------

  Scenario: /assembly --allow-missing-stack on blueprint-ready issue still builds contract
    Given a webhook payload "assembly_command_missing_stack"
    When Assembly receives the "issue_comment" event
    Then exactly one route is produced
    And the route validation status is "assembly_ready"
    And the route writeback contract is present
    And the route writeback contract has issue number 71

  # ---------------------------------------------------------------------------
  # Scenario 5 — BE=pi corner: adapter returns a failed result; progress
  # comment upserts the failure; no branch / PR / codex writes.
  # ---------------------------------------------------------------------------

  Scenario: BE=pi adapter failure upserts progress comment without branch or PR writes
    Given a webhook payload "assembly_command_ready"
    And DRY_RUN is "false"
    And ASSEMBLY_EXECUTION_BACKEND is "pi-oh-my-pi-deepseek"
    When Assembly receives the "issue_comment" event
    And Assembly dispatches the route with a mock GitHub client
    Then the dispatcher result adapter_result status is "failed"
    And the dispatcher upserted at least one progress comment
    And the dispatcher made no branch or pull-request writes
    And the dispatcher result writeback_failures is empty

  # ---------------------------------------------------------------------------
  # Scenario 5b (VOY-1821 RED) — BE=fake-subprocess exercises the real
  # dispatcher branch -> PR -> Codex -> progress-comment flow.
  # ---------------------------------------------------------------------------

  Scenario: BE=fake-subprocess opens a PR and records Assembly progress
    Given a webhook payload "assembly_command_ready"
    And DRY_RUN is "false"
    And ASSEMBLY_EXECUTION_BACKEND is "fake-subprocess"
    And the fake subprocess backend is allowed
    And the fake subprocess backend will return executed with commit SHA "0123456789abcdef0123456789abcdef01234567"
    When Assembly receives the "issue_comment" event
    And Assembly dispatches the route with a mock GitHub client
    Then the dispatcher result adapter_result status is "executed"
    And the dispatcher created a branch and opened a pull request
    And the dispatcher posted a Codex review trigger
    And the dispatcher upserted progress comments on the issue and pull request

  Scenario: /assembly --resume reports resume fallback when no prior PR exists
    Given a webhook payload "assembly_command_ready"
    And the Assembly command body is "/assembly --resume"
    And DRY_RUN is "false"
    And ASSEMBLY_EXECUTION_BACKEND is "fake-subprocess"
    And the fake subprocess backend is allowed
    And the fake subprocess backend will return no_changes
    When Assembly receives the "issue_comment" event
    And Assembly dispatches the route with a mock GitHub client
    Then the dispatcher result session mode is "resume_fallback"
    And the dispatcher upserted at least one progress comment
    And the latest Assembly progress comment includes "Session: `resume_fallback`"

  # ---------------------------------------------------------------------------
  # Scenario 6 (VOY-1818) — /assembly from an authorized maintainer (OWNER)
  # on a ready, allow-listed issue runs the dry-run plan.
  # ---------------------------------------------------------------------------

  Scenario: /assembly from authorized maintainer runs dry-run plan
    Given a webhook payload "assembly_command_authorized_member"
    And the BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS env is set-but-empty
    And DRY_RUN is "true"
    And ASSEMBLY_EXECUTION_BACKEND is "dry-run"
    When Assembly receives the "issue_comment" event
    Then exactly one route is produced
    And the route validation status is "assembly_ready"
    And the route writeback contract is present
    When Assembly dispatches the route with a mock GitHub client
    Then the dispatcher result has dry_run "true"
    And the dispatcher made no GitHub mutations

  # ---------------------------------------------------------------------------
  # Scenario 7 (VOY-1818) — /assembly from an unauthorized CONTRIBUTOR is
  # refused at the actor gate; refusal comment carries unauthorized_actor.
  # ---------------------------------------------------------------------------

  Scenario: /assembly from unauthorized CONTRIBUTOR is refused
    Given a webhook payload "assembly_command_unauthorized_contributor"
    And the BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS env is set-but-empty
    And DRY_RUN is "false"
    When Assembly receives the "issue_comment" event
    Then exactly one route is produced
    And the route writeback refusal reason is "unauthorized_actor"
    When Assembly dispatches the route with a mock GitHub client
    Then the dispatcher upserted exactly one refusal comment
    And the dispatcher made no branch or pull-request writes

  # ---------------------------------------------------------------------------
  # Scenario 8 (VOY-1818) — /assembly from an allow-list login with
  # author_association: NONE → route runs (allow-list overrides association).
  # ---------------------------------------------------------------------------

  Scenario: /assembly from allow-list login with NONE association runs
    Given a webhook payload "assembly_command_allowlist_only"
    And the BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS env contains "external-collab"
    When Assembly receives the "issue_comment" event
    Then exactly one route is produced
    And the route validation status is "assembly_ready"
    And the route writeback contract is present
