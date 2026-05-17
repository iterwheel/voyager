# SOP-1815: Clearance DeepSeek Profile Policy

**Applies to:** Voyager Clearance investigator profile configuration
**Last updated:** 2026-05-18
**Last reviewed:** 2026-05-18
**Status:** Active
**Related:** VOY-1807 (GitHub App Registry), VOY-1811 (Multi-Agent Loop Configuration), issue #46

---

## What Is It?

The operator policy for selecting DeepSeek-backed Clearance investigator
profiles. The investigator is advisory code inside Voyager: it examines review
thread evidence and can contribute to review-thread resolution decisions, but
it does not write to GitHub directly.

## Why

Voyager's current canary uses a Flash-tier DeepSeek profile. That is useful for
latency and cost, but the original confidence thresholds were tuned against
Pro-tier reasoning. Without an explicit policy, operators can accidentally use
Flash output for approval-adjacent review-thread auto-resolve beyond the canary
scope.

This SOP keeps the current canary behavior available while making production
promotion explicit: Pro-tier profiles are the recommended path for automatic
review-thread resolution, and Flash-tier profiles require a higher threshold
and should remain canary/advisory until an operator changes the default profile.

## When to Use

- Choosing or changing `[voyager].default_profile` in `config.toml`.
- Reviewing warnings about Flash-tier or unknown investigator models.
- Preparing to expand Clearance beyond the current Voyager canary.
- Auditing why a profile used a specific `min_confidence` value.

## When NOT to Use

- Defining GitHub App permissions or webhook installation scope.
- Choosing non-DeepSeek LLM providers.
- Deciding whether a specific PR should merge after a human review dispute.
- Changing production profile defaults without a repo-specific rollout issue.

## Steps

### 1. Use the Supported Profiles

| Profile | Model | Thinking | `min_confidence` | Intended use |
|---------|-------|----------|------------------|--------------|
| `flash` | `deepseek-v4-flash` | on | `0.90` | Canary/advisory investigation when lower latency matters. |
| `flash_no_thinking` | `deepseek-v4-flash` | off | `0.90` | Current Wukong canary behavior; do not broaden without explicit operator approval. |
| `pro` | `deepseek-v4-pro` | on, medium effort | `0.78` | Recommended production review-thread auto-resolve profile. |
| `pro_max` | `deepseek-v4-pro` | on, max effort | `0.85` | Higher-assurance profile for risky or noisy diffs. |

Equivalent locally named profiles are allowed if they keep the same model tier,
thinking mode, and threshold policy.

### 2. Choose the Default Profile

Preserve the current canary until the operator explicitly changes it:

```toml
[voyager]
default_profile = "flash_no_thinking"
```

Promote to the recommended production auto-resolve path only through an
intentional config change:

```toml
[voyager]
default_profile = "pro"
```

### 3. Apply the Auto-Resolve Policy

- Production review-thread auto-resolve should use a Pro-tier profile.
- Flash-tier profiles may remain enabled for the current canary/advisory path
  with `min_confidence >= 0.90`.
- Unknown models must not be treated as production-ready until their tier and
  threshold are documented.
- Moving public aliases such as `deepseek-chat` must be treated as unknown
  until the rollout document pins them to a Voyager policy tier.
- Lowering a Pro threshold below `0.78` or a Flash threshold below `0.90`
  requires a written exception in the rollout issue or deployment handoff.

### 4. Interpret Startup Warnings

Voyager logs an actionable startup warning when the selected investigator
profile is Flash-tier, unknown, or below the recommended Pro threshold. Treat
that warning as a config review prompt, not as a crash condition. The bridge
continues running so the current canary does not change behavior silently.

Operator actions:

- Flash selected intentionally: keep it in canary scope and confirm
  `min_confidence >= 0.90`.
- Production auto-resolve desired: set `[voyager].default_profile = "pro"`.
- Unknown model selected: pin to `deepseek-v4-pro` or document the model tier
  and threshold before enabling auto-resolve.
- Public alias selected: prefer an explicit Voyager profile model such as
  `deepseek-v4-pro` or `deepseek-v4-flash`, or document why the alias is safe.

### 5. Verify Profile Selection

After editing `config.toml`, run a local config load and targeted tests:

```bash
uv run pytest -q tests/bdd/step_defs/test_config_steps.py \
  tests/bdd/step_defs/test_swm_investigator_steps.py \
  tests/unit/test_investigator_profile_policy.py
```

Then restart the bridge and inspect startup logs for the policy warning.

## Pitfalls

- Do not treat Flash and Pro confidence values as interchangeable. They are
  different model tiers.
- Do not lower thresholds to suppress `NEEDS_HUMAN_JUDGMENT`; that moves risk
  into automation.
- Do not assume `thinking=false` plus `reasoning_effort` is valid. Voyager
  rejects that config because DeepSeek nullifies the effort setting when
  thinking is disabled.
- Do not promote the default profile as part of a broad repository expansion
  without recording the decision in the rollout issue.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-18 | Initial DeepSeek profile policy for issue #46. | Codex |
