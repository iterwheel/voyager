# Voyager E2E Test Harness

End-to-end test infrastructure for voyager's 7C clearance pipeline against
`iterwheel/voyager-sandbox`. Builds real PRs, posts review threads using a
dedicated test GitHub App identity (treated as Codex-equivalent via
`VOYAGER_TEST_BOT_LOGINS`), and streams pass/fail to a real-time dashboard.

## Layout

```
scripts/e2e/
├── matrix.yaml              # declarative test scenarios (Phase A: 5 cases)
├── run_matrix.py            # orchestrator + PR factory
├── dashboard.py             # FastAPI + SSE real-time dashboard
├── templates/index.html     # browser UI
└── README.md                # this file
```

## One-time operator setup

### 1. Register the test GitHub App

In the GitHub UI:

1. Go to https://github.com/organizations/iterwheel/settings/apps/new
2. **App name**: `voyager-e2e-bot` (the bot-slug becomes the login voyager sees)
3. **Webhook**: deactivate (this App posts; it does not receive webhooks)
4. **Permissions**:
   - Pull requests: **Read & Write**
   - Issues: **Read**
   - Metadata: **Read** (mandatory in the GitHub UI)
   - Reactions are not exposed as a separate GitHub App permission in the
     current UI; the reaction endpoints used by the harness are covered by
     the issue / pull request resources above.
5. **Where can this App be installed**: Only on this account
6. After creation: generate a private key (.pem), download it
7. Install the App on `iterwheel/voyager-sandbox`

Current `iterwheel` sandbox registration:

- App slug: `voyager-e2e-bot`
- App ID: `3723890`
- Webhook: inactive
- Install target: `iterwheel` only; install on `iterwheel/voyager-sandbox`
- Private key location: `~/.voyager/secrets/voyager-e2e-bot.pem` (never commit
  the PEM)
- Installation ID: capture from the GitHub installation URL after installing
  the App, then export it as `VOYAGER_E2E_TEST_BOT_INSTALLATION_ID`

### 2. Wire it into voyager's config

```bash
# Place the PEM under ~/.voyager/secrets/
chmod 600 ~/.voyager/secrets/voyager-e2e-bot.pem
```

Add the App's bot login to `~/.voyager/config.toml`:

```toml
# Append the e2e harness's test-bot login so voyager treats it as Codex.
# NOTE: this is the env var voyager reads at startup, not a TOML field —
# export it in the same shell that runs voyager.
```

In the shell that launches voyager:

```bash
export VOYAGER_TEST_BOT_LOGINS="voyager-e2e-bot[bot]"   # or whatever slug you chose
```

(`is_codex_login` auto-expands to also match the bare GraphQL form, so
listing just one of the two forms is sufficient.)

### 3. Set the runner env vars

```bash
export VOYAGER_E2E_TEST_BOT_APP_ID="<app id from GH UI>"
export VOYAGER_E2E_TEST_BOT_INSTALLATION_ID="<installation id from GH UI>"
export VOYAGER_E2E_TEST_BOT_PEM="$HOME/.voyager/secrets/voyager-e2e-bot.pem"
export VOYAGER_E2E_TEST_BOT_LOGIN="voyager-e2e-bot[bot]"
```

## Running

**Terminal 1 — start the dashboard:**

```bash
cd /Users/frank/Projects/voyager
uv run uvicorn scripts.e2e.dashboard:app --host 127.0.0.1 --port 9099
```

Open http://127.0.0.1:9099 in a browser.

**Terminal 2 — start voyager** (with the test-bot login and the debug endpoint enabled):

```bash
cd /Users/frank/Projects/voyager
export VOYAGER_TEST_BOT_LOGINS="voyager-e2e-bot[bot]"
export DRY_RUN=true              # voyager won't write labels/merges during testing
export VOYAGER_E2E_DEBUG=1       # enables /e2e/recent_writebacks (the runner polls it)
# Optional defense-in-depth: pair with X-Voyager-E2E-Token header
# export VOYAGER_E2E_TOKEN="<random-secret>"  # if set, runner reads same env var
uv run uvicorn voyager.server:app --host 127.0.0.1 --port 8000
```

(Make sure cloudflared tunnel points to `:8000`. See top-level docs for the
sandbox-only swap procedure per Q1(c).)

> Without `VOYAGER_E2E_DEBUG=1` the runner fails fast on the first scenario
> with "endpoint is gated" (404 from the loopback debug endpoint). The
> `VOYAGER_E2E_TOKEN` pairing is optional but recommended on multi-tenant
> hosts.

**Terminal 3 — run the matrix:**

```bash
cd /Users/frank/Projects/voyager
uv run python scripts/e2e/run_matrix.py \
    --matrix scripts/e2e/matrix.yaml \
    --dashboard http://127.0.0.1:9099 \
    --filter A1 --filter B1     # subset by scenario id prefix; omit to run all
```

To rehearse without touching GitHub:

```bash
uv run python scripts/e2e/run_matrix.py --dry-run-sandbox
```

## Voyager debug endpoint security

The harness reads voyager's decisions from `GET /e2e/recent_writebacks`,
which is layered behind:

1. **`VOYAGER_E2E_DEBUG=1`** required — 404 otherwise (doesn't leak endpoint
   existence)
2. **Loopback-only by default** — non-127.0.0.1 / non-::1 clients get 404.
   Override with `VOYAGER_E2E_ALLOW_NON_LOOPBACK=1` for bastion / split-host
   setups (you almost certainly don't want this).
3. **Optional shared-secret token** — if `VOYAGER_E2E_TOKEN` is set on the
   voyager side, requests must carry `X-Voyager-E2E-Token: <value>`. The
   runner reads the same env var to send the header automatically.
4. **`Cache-Control: no-store`** on the response.

Production never sets `VOYAGER_E2E_DEBUG`; the `E2E` in the var name + the
404-when-disabled behavior together signal intent.

## Phase A scope

5 scenarios across categories A / B / C / E / F. D (β overlay preservation,
4 conditions) deferred to Phase B since it requires multi-thread PR setups.

Each scenario has two assertion blocks:

- **`expected`** — PR-level keys the comparator can assert from the
  writeback record voyager emits today (status, automation_status,
  writeback_skipped, label_present, etc.). See the schema comment at the
  top of `matrix.yaml` for the full list.
- **`phase_b_expected`** — per-thread keys (`codex_severity`,
  `effective_severity`, `finding_kind`, `investigator_invoked`,
  `investigator_verdict`) that would need voyager-side changes to surface
  thread state in the writeback record. The runner **ignores** this block
  in Phase A; it's documented in the matrix to keep scenarios complete.

### Phase A status

- ✅ Scaffold structure + dashboard + UI
- ✅ matrix.yaml schema + 5 scenarios
- ✅ Real branch / file / PR creation via gh CLI
- ✅ Real test-bot review-thread POST via App installation token
- ✅ Polling voyager's `/e2e/recent_writebacks` endpoint (no fixed sleep)
- ✅ Polling filter: event type (`pull_request_review*`) + `since_ts` to
  exclude voyager's pre-review `pull_request opened` writeback
- ✅ Comparator: flattened writeback vs `expected` block (PR-level keys)
- ✅ Cleanup: close PR + delete branch (idempotent, runs in `finally`)
- ✅ Branch-leak fix: tracks `created_branch` independently of `pr_number`
- ✅ Endpoint security hardening (env + loopback + optional token)
- ✅ `force_push_after_review` hook (for E1 stale-verdict scenarios)
- ✅ `thread_reply` posting via PR-author PAT (for F1 investigator scenarios)
- ✅ Unit tests: 17 endpoint + 24 runner helpers + 11 dashboard = 52 new

### Phase B (next)

- Extend voyager's writeback record to include per-thread state
  (`compute_clearance_automation` → emit a `threads_summary` array), so the
  `phase_b_expected` blocks become assertable
- Expand to 30+ scenarios across A / B / C / D / E / F
- A bundle: 3-5 real-Codex sanity cases to validate bypass == real path
- Split `run_matrix.py` (~800 LOC) into smaller modules
