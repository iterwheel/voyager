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
   - Metadata: **Read**
   - Reactions: **Read & Write**
5. **Where can this App be installed**: Only on this account
6. After creation: generate a private key (.pem), download it
7. Install the App on `iterwheel/voyager-sandbox`

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

**Terminal 2 — start voyager** (with the test-bot login in env):

```bash
cd /Users/frank/Projects/voyager
export VOYAGER_TEST_BOT_LOGINS="voyager-e2e-bot[bot]"
export DRY_RUN=true   # voyager won't write labels/merges during testing
uv run uvicorn voyager.server:app --host 127.0.0.1 --port 8000
```

(Make sure cloudflared tunnel points to `:8000`. See top-level docs for the
sandbox-only swap procedure per Q1(c).)

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

## Phase A scope

5 scenarios across the 6 categories — A / B / C / E / F. D (β overlay
preservation, 4 conditions) deferred to Phase B since it requires more
nuanced multi-thread PR setups.

Phase A status:
- ✅ Scaffold structure + dashboard + UI
- ✅ matrix.yaml schema
- ✅ Real branch / file / PR creation via gh CLI
- ✅ Real test-bot review-thread POST via App installation token
- ⏳ Waiting on voyager's webhook processing (sleeps 8s — replace with poll-for-marker)
- ⏳ Comparator: actual-vs-expected deep-eq (currently always fails as "TODO")
- ⏳ Cleanup: close PR + delete branch after assertion

## Phase B (next)

- Wire actual-vs-expected comparator (read voyager's writeback log)
- Implement `wait_for: log_marker` poll
- Cleanup hooks (close PR + delete branch on success)
- Implement `force_push_after_review` for E1
- Implement `thread_reply` for F1
- Expand to 30+ scenarios across A / B / C / D / E / F
- A bundle: real Codex on 3-5 sanity PRs to validate the bypass path matches
  the real path
