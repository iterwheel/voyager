# CHG-1830: Remove Countdown Dedicated PAT and User-Token Canary Machinery

**Applies to:** VOY project
**Last updated:** 2026-06-28
**Last reviewed:** 2026-06-28
**Status:** Proposed
**Date:** 2026-06-28
**Requested by:** Frank Xu
**Priority:** Medium
**Change Type:** Normal
**Supersedes:** VOY-1827 (REF), VOY-1828 (SOP), VOY-1829 (CHG)
**Retracts:** VOY-1831 (CHG, never merged)

---

## What

Remove the Countdown **dedicated-PAT** review-thread resolver and the **user OAuth
refresh-token** canary in full, now that PR #222 (`vyg countdown
resolve-conversation`) provides the sanctioned resolve mechanism: resolve review
conversations as the fixed machine account via `gh auth token --user
iterwheel-countdown-user`, with a hard viewer-login identity gate and a
resolve-only operation allowlist.

Removed surfaces:

| Layer | Removed |
|-------|---------|
| CLI (`voyager/cli.py`) | `countdown review-thread-diagnostic`, `countdown user-review-thread-diagnostic`, `countdown user-device-code`, `countdown user-refresh-check`, and their PAT/refresh-token helpers + timeout constants |
| Core (`voyager/core/countdown_diagnostic.py`) | whole module — `DEDICATED_PAT_FALLBACK_*` constants, `GitHubTokenReviewThreadClient`, `query_review_thread_capabilities`, `run_review_thread_resolve_canary`, and `COUNTDOWN_AGENT_SLUG` (its sole consumer was the removed PAT branch, so it was deleted, not relocated) |
| Core (`voyager/core/github_app_user_auth.py`) | whole module — the user OAuth Device Flow / refresh-token backing of the canary CLI: `request_device_code`, `exchange_device_code`, `refresh_user_access_token`, `query_viewer_login`, `GitHubUserAccessClient` (incl. its `resolve_review_thread`). Orphaned once the canary CLI commands were removed |
| Config (`voyager/core/config.py`) | `CountdownDedicatedPatFallbackConfig`, `CountdownConfig`, the `[countdown.dedicated_pat_fallback]` parser, and the `countdown` field on the top-level config (read only by PAT code) |
| Clearance pipeline (`voyager/bots/clearance/pipeline.py`) | the `dedicated_pat_fallback` resolution tier and its helpers (`_countdown_dedicated_pat_fallback_for_repository`, `_read_countdown_dedicated_pat`, `_countdown_app_baseline_allows_dedicated_pat_fallback`), the `dedicated_pat_token_reader` / `dedicated_pat_client_factory` parameters of `_maybe_sync_stage_15` |
| Tests | `tests/unit/test_countdown_diagnostic.py` (whole) + the dedicated-PAT / refresh-token / canary cases in `test_cli.py`, `test_config_example_apps.py`, `test_bridge_assembly_config.py`, `test_pipeline_thread_verdict_writeback.py`, `test_docs_voy_1807.py` |
| Secrets / scripts | local age vault (`~/.config/voyager/age/keys.txt`, `countdown-pat.age`, `voyager.env.age`) and `scripts/countdown-resolve-threads` |
| Governance | VOY-1827/1828/1829 marked **Superseded**; VOY-1831 retracted; VOY-1807 registry updated; index regenerated |

**Out of scope / preserved:** the non-PAT clearance resolution path (GitHub-App
primary + App-based delegated-resolver fallback via `_authorized_resolver_app_for_pr_author`),
the `countdown_app_baseline` capability checks (GitHub-App capability data, not PAT
config), and the new `resolve-conversation` tool.

## Why

The dedicated-PAT approach was retired in favor of gh-login as the machine account
(see #222). Keeping the PAT machinery alive is dead weight and a standing risk:

- It is a second, divergent resolve path with its own identity model (PAT actor
  vs. the gh-login viewer-gate), increasing the surface where a resolve could run
  as the wrong identity — the exact failure class #222's P1 fix hardened against.
- The clearance pipeline's dedicated-PAT fallback ships `enabled=False` (dark) and
  has never been activated in production; it carries config, secrets handling, and
  test surface for a capability the project no longer intends to use.
- The user OAuth refresh-token canary (`user-device-code` / `user-refresh-check` /
  `user-review-thread-diagnostic`) is a third, also-superseded mechanism.

## Impact Analysis

- **Systems affected:** clearance bot Stage-1.5 thread resolution (loses the dark
  PAT fallback tier; the App-based path is unchanged), the `vyg countdown` CLI
  group (only `resolve-conversation` remains), config schema (`[countdown]` section
  removed — it was PAT-only), governance docs.
- **Behavior change:** none observable in production — the removed pipeline tier was
  `enabled=False` by default and unset everywhere; the diagnostics/canaries were
  operator-run tools. No example/app config references `[countdown]`.
- **Migration:** review-thread resolution is now `vyg countdown
  resolve-conversation` (machine-account, identity-gated). The clearance pipeline
  re-acquiring a machine-account fallback is tracked separately (rebuild on the
  #222 resolver) and is **not** part of this change.
- **Rollback plan:** revert this PR's commit. The removed code is recoverable from
  git history and the closed `feat/countdown-resolve-loop-core` branch; superseded
  governance docs remain as historical record.

## Implementation Plan

1. Branch `chore/remove-dedicated-pat` off `origin/main`.
2. Excise the CLI commands + helpers; delete `countdown_diagnostic.py` (whole
   module, including `COUNTDOWN_AGENT_SLUG` whose only consumer was the PAT tier);
   remove the config classes/parser; unwind the `dedicated_pat_fallback` tier from
   `pipeline.py` leaving the App path intact.
3. Delete / trim the associated tests; delete local age artifacts + the script.
4. Mark VOY-1827/1828/1829 Superseded, retract VOY-1831, update VOY-1807, `af index`.
5. Verify: `pytest`, `ruff`, `ruff format`, `mypy --strict`, `bandit`, `af validate`.
6. Front-load Trinity adversarial review → PR → codex gate to zero findings.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-28 | Updated VOY-1827 superseded reference after HYP was reclassified as REF for validation compatibility. | Codex |
| 2026-06-28 | Initial version | Claude Code |
