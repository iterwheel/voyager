# SOP-1814: Wukong Bridge Launchd and Rollback

**Applies to:** Voyager production bridge on Wukong
**Last updated:** 2026-05-18
**Last reviewed:** 2026-05-18
**Status:** Active
**Related:** VOY-1807 (GitHub App Registry), VOY-1808 (Cross Account Installation), VOY-1810 (Release Process), issue #44

---

## What Is It?

The Wukong operator runbook for running the Voyager GitHub bridge as a
`launchd` user service instead of a manually managed shell or tmux process.
The service owns the local FastAPI bridge on `127.0.0.1:8787`; Cloudflare still
terminates the public `https://gh.iterwheel.com` route and forwards to that
local port.

## Why

Voyager's production bridge previously depended on a manually managed local
process. That works for a narrow canary, but it is fragile across reboot,
terminal loss, operator handoff, and crash recovery. A launchd service gives the
bridge a durable owner while keeping the existing Wukong-local secret and
allow-list boundaries intact.

This SOP also makes rollback explicit. If a deployment misbehaves, the operator
should be able to return to a known release tag, restart the service, and verify
local plus public health without rediscovering the command sequence under
pressure.

## When to Use

- Installing Voyager's bridge as a Wukong user-level launchd service.
- Restarting, stopping, or checking the production bridge.
- Rolling the production checkout back to a previous Voyager tag.
- Auditing where Wukong-local private deployment files live.

## When NOT to Use

- Local development servers on arbitrary ports.
- CI jobs or GitHub Actions runners.
- Cloudflare tunnel management, except for the public `/healthz` verification
  command that confirms the tunnel still reaches the local bridge.
- Expanding repository allow-lists beyond the current canary scope. Use a
  separate rollout issue before adding repositories.

## Steps

### 1. Confirm Repository Artifacts

| Path | Purpose |
|------|---------|
| `deploy/launchd/com.iterwheel.voyager.bridge.plist` | Repo-safe launchd template for Wukong. |
| `deploy/wukong/bridge.env.example` | Non-secret env-file template. Copy it locally before use. |
| `config.example.toml` | Repo-safe app/config template. The real config stays private. |

The launchd plist intentionally sources `/Users/frank/.voyager/bridge.env`
through `/bin/zsh -lc` because launchd does not load dotenv files itself.

### 2. Prepare Private Wukong Files

These files are machine-local and must not be committed:

| Path | Contents | Required permissions |
|------|----------|----------------------|
| `/Users/frank/.voyager/bridge.env` | Live launchd environment, webhook secrets, allow-lists, and `DRY_RUN=false`. | `600` |
| `/Users/frank/.voyager/config.toml` | App IDs, installation IDs, profile config, and private key paths. | `600` |
| `/Users/frank/.voyager/secrets/` | GitHub App private keys referenced by `config.toml`. | directory `700`, files `600` |
| `/Users/frank/.voyager/state/` | Bridge state and Clearance JSONL records. | directory `700` preferred |
| `/Users/frank/Library/LaunchAgents/com.iterwheel.voyager.bridge.plist` | Installed copy of the launchd plist. | `644` |
| `/Users/frank/Library/Logs/voyager/` | launchd stdout/stderr logs. | directory `755` |

If `config.toml` points to another private-key directory, that config remains
the source of truth. Preserve the same private permissions.

### 3. Preserve the Production Environment Contract

The production env file must keep this safety shape until a later approved
rollout changes it:

```bash
DRY_RUN=false
BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_BLUEPRINT=frankyxhl/alfred,frankyxhl/trinity,iterwheel/voyager
BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_STACK=frankyxhl/alfred,frankyxhl/trinity,iterwheel/voyager
BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE=iterwheel/voyager
```

Leave `BRIDGE_ALLOWED_REPOSITORIES` unset unless a route deliberately depends
on the global fallback allow-list. App-specific allow-lists are easier to audit
and prevent a new bot route from inheriting broad write access by accident.

### 4. Run Preflight

Run these from `/Users/frank/Projects/voyager`:

```bash
git status --short --branch
plutil -lint deploy/launchd/com.iterwheel.voyager.bridge.plist
curl -fsS http://127.0.0.1:8787/healthz
lsof -nP -iTCP:8787 -sTCP:LISTEN
```

If port `8787` is already owned by a tmux or shell-launched uvicorn process,
keep it running until the env file and launchd plist are ready. Stop that old
process immediately before `launchctl bootstrap`; launchd cannot bind the port
while the old process owns it.

### 5. Install the LaunchAgent

```bash
cd /Users/frank/Projects/voyager

install -d -m 700 /Users/frank/.voyager
install -d -m 700 /Users/frank/.voyager/state
install -d -m 755 /Users/frank/Library/Logs/voyager
install -d -m 755 /Users/frank/Library/LaunchAgents

install -m 600 deploy/wukong/bridge.env.example /Users/frank/.voyager/bridge.env
$EDITOR /Users/frank/.voyager/bridge.env

plutil -lint deploy/launchd/com.iterwheel.voyager.bridge.plist
install -m 644 deploy/launchd/com.iterwheel.voyager.bridge.plist \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.bridge.plist

launchctl bootstrap gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.bridge.plist
launchctl enable gui/$(id -u)/com.iterwheel.voyager.bridge
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge
```

### 6. Operate the Service

Start after an explicit bootout:

```bash
launchctl bootstrap gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.bridge.plist
launchctl enable gui/$(id -u)/com.iterwheel.voyager.bridge
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge
```

Stop:

```bash
launchctl bootout gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.bridge.plist
```

Restart:

```bash
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge
```

Status:

```bash
launchctl print gui/$(id -u)/com.iterwheel.voyager.bridge
pgrep -fl "uvicorn voyager.server:app"
lsof -nP -iTCP:8787 -sTCP:LISTEN
```

Logs:

```bash
tail -n 100 -F /Users/frank/Library/Logs/voyager/bridge.out.log
tail -n 100 -F /Users/frank/Library/Logs/voyager/bridge.err.log
```

Healthcheck:

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS https://gh.iterwheel.com/healthz
```

The local `/healthz` response must include `"ok": true`, service
`"iterwheel-github-bridge"`, and `"dry_run": false` for production writes.

### 7. Roll Back to a Previous Tag

Rollback changes the code checkout to a known release tag, restarts launchd,
and verifies the local and public health endpoints. It does not edit secrets or
repository allow-lists.

```bash
PREVIOUS_TAG=v0.3.0

cd /Users/frank/Projects/voyager
git fetch origin --tags
git switch --detach "${PREVIOUS_TAG}"
uv sync

launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge

curl -fsS http://127.0.0.1:8787/healthz
curl -fsS https://gh.iterwheel.com/healthz
```

After the incident is resolved, return to main:

```bash
cd /Users/frank/Projects/voyager
git switch main
git pull --ff-only origin main
uv sync
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge
curl -fsS http://127.0.0.1:8787/healthz
```

## Verification

Before declaring the launchd migration complete, record the following in the
handoff or PR:

- `plutil -lint deploy/launchd/com.iterwheel.voyager.bridge.plist` passes.
- `launchctl print gui/$(id -u)/com.iterwheel.voyager.bridge` shows the service.
- `curl -fsS http://127.0.0.1:8787/healthz` returns `dry_run: false`.
- `launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge` restarts
  the service without losing `/healthz`.
- `tail` of `bridge.err.log` shows no startup error after restart.
- A rollback tag was named and tested or explicitly deferred.

## Pitfalls

- launchd does not expand `~`; use absolute `/Users/frank/...` paths in the
  plist.
- launchd does not parse dotenv files. The template uses zsh to source the env
  file before `exec`ing uvicorn.
- The plist uses `/bin/zsh -lc`, so the operator's login shell files may run
  before `bridge.env` is sourced. Keep shell startup files free of
  stdout-producing commands and env overrides that conflict with
  `/Users/frank/.voyager/bridge.env`.
- A missing env file or syntax error causes fast launchd restart loops. Use
  `launchctl print` and `bridge.err.log` first when diagnosing.
- Do not enable a global repository allow-list casually. A global allow-list can
  grant future bot routes more writeback scope than intended.
- Do not bootstrap while the old tmux process owns port `8787`.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-18 | Initial Wukong launchd and rollback SOP for issue #44. | Codex |
