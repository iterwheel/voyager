# CHG-1818: Assembly Actor Authorization Gate

**Applies to:** VOY project
**Last updated:** 2026-08-30
**Last reviewed:** 2026-08-30
**Status:** Completed
**Date:** 2026-05-23
**Scheduled:** After CHG plan-review approval in the active VOY-1811 loop.
**Requested by:** Frank Xu (via issue #76, 2026-05-23)
**Priority:** P1
**Change Type:** Normal
**Targets:** `voyager/bots/assembly/`, `voyager/server.py`, `tests/unit/`, `tests/bdd/features/assembly.feature`, `tests/bdd/step_defs/test_assembly_steps.py`, `tests/fixtures/webhooks/`, `rules/VOY-1805-SOP-GitHub-Bot-Accounts-and-Responsibilities.md`, `config.example.toml`
**Closes:** #76
**Related:** VOY-1805 SOP §5 (Assembly boundary), VOY-1806 SOP (permission matrix), VOY-1807 REF (Assembly registry row), VOY-1811 REF (Multi-Agent Loop), VOY-1817 CHG (Assembly MVP)

---

## What

Add an **actor authorization gate** to the Assembly bot so only trusted GitHub
users can trigger `/assembly` or `/implement` against an issue. The gate fires
at routing time, before any precondition or backend dispatch, and rejects
untrusted invocations with a refusal comment carrying reason
`unauthorized_actor`. Authorization considers two independent signals from the
incoming webhook payload:

1. An **explicit allow-list** of GitHub logins configured via the env var
   `BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS` (whitespace/comma-separated, same
   parsing as the existing `BRIDGE_ALLOWED_REPOSITORIES_*` pattern).
2. An **author_association policy** of trusted associations configured via the
   env var `BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS` (default
   `OWNER,MEMBER,COLLABORATOR`).

A comment is authorized when **either** signal matches. Bot actors and missing
actor metadata are denied by default. The gate runs in dry-run and real-backend
paths alike — it is upstream of the `DRY_RUN` and `ASSEMBLY_EXECUTION_BACKEND`
gates already documented in VOY-1817.

## Why

VOY-1817 shipped the Assembly MVP with `DryRunAdapter` as the default
backend and the `BRIDGE_ALLOWED_REPOSITORIES_*` allow-list defaulting to empty,
so today no GitHub mutations can fire from `iterwheel/voyager` even if an
untrusted commenter typed `/assembly`. That posture is safe **only** because
the real backend is a `NotImplementedError` stub. Once the
`pi -> oh-my-pi -> DeepSeek V4 Pro` backend is wired and the production
allow-list grows beyond a sandbox repo, the issue body becomes the trigger
surface for code-writing work — and that surface is open to any GitHub user
who can comment on an issue in a public repository. Without an actor gate,
`/assembly` on a public repo is "any commenter writes code".

The repository allow-list (VOY-1817 D6 / D13) and the dry-run / backend gates
do not solve this: a repo on the allow-list with the real backend enabled
would let *any* commenter run Assembly. The actor gate is the orthogonal
control that closes this exposure. Issue #76 captures this prerequisite
explicitly: ship this gate **before** the real backend is enabled or before
the production allow-list expands.

The two-signal design (allow-list OR association) matches how GitHub's own
Actions community filters commenters today and keeps Iterwheel-side
configuration ergonomic: the day-to-day case is "maintainers trigger
Assembly", which the association policy covers out of the box; the explicit
allow-list adds support for external collaborators or for non-org repos where
`MEMBER` is not granted automatically.

## Out of Scope

- Wiring the real `pi -> oh-my-pi -> DeepSeek V4 Pro` backend; this CHG is a
  prerequisite for that follow-up, not a part of it.
- Expanding the bridge repository allow-list beyond `iterwheel/voyager-sandbox`.
- Adding new GitHub App permissions; the gate reads fields that already arrive
  on the existing `issue_comment.created` webhook.
- Reactive controls (revoking authorization mid-job, kill switches); the gate
  is checked at routing time only because the actor identity is fixed at
  webhook ingestion.
- Per-repository overrides of the authorization policy; the env-var policy is
  global to the bridge instance. A per-repo override would require a new
  config shape and is deferred.
- Audit-trail persistence beyond the existing routing log + Assembly refusal
  comment. A durable audit ledger is deferred.
- **Refusal-comment disclosure**: the `unauthorized_actor` refusal body must
  not enumerate the allow-list, the trusted-association set, or any other
  operator-configured value (per D12). Refusal bodies are public on issue
  comments and must not leak the org's trusted-actor surface to an attacker
  triggering refusals on a public repo. Surface 5 enforces this; the unit
  test in Surface 7 asserts the rendered body does not contain the env-var
  contents.

## Impact Analysis

### Systems affected

- Assembly bot package (`voyager/bots/assembly/`): adds a new `actor.py`
  module, extends `constants.py` with a new refusal reason and env-var keys,
  and threads the gate through `routing.py` ahead of preconditions.
- Refusal comment renderer (`voyager/bots/assembly/comment.py`): adds an
  `unauthorized_actor` rendering branch.
- Webhook router (`voyager/server.py`): unchanged — the gate is inside
  `route_assembly_event`, not a new server-level filter. Documented here for
  clarity.
- Documentation: extends VOY-1805 with an Actor Authorization section
  parallel to the Assembly boundary table.

### Channels affected

- GitHub issue comments (the refusal comment is upserted on the source issue
  when the dry-run gate is off **and** the repository allow-list pass — see
  Gate Corner Table for the AC−/AL+ vs AC−/AL− split).
- Bridge `_recent_writebacks` ring: refused routes appear with
  `validation.status = "assembly_refused"` and the new
  `refusal.reason = "unauthorized_actor"` **only when the repository allow-list
  also passes (AL+)**. AC−/AL− routes are dropped by the server-level
  `_filter_routes_by_repository` filter before reaching the writeback path, so
  no ring entry and no refusal comment is produced in that combined case. The
  existing `repository_allowlist_denied` warning still logs the AL− deny.

### Downtime required

None. The default deny when no env var is set is a strict tightening; an
operator who has not configured the gate sees Assembly refuse every
invocation (rather than silently failing later in the chain). Existing
sandbox tests run as the GitHub App identity through the test fixtures,
which the new fixtures will explicitly authorize.

### External dependencies

None. The gate reads `payload.sender.login`,
`payload.comment.user.login`, `payload.comment.user.type`, and
`payload.comment.author_association` from the existing webhook payload.

### Rollback plan

Revert the implementation commits. The added module and refusal reason are
additive; removing them restores the pre-CHG behavior where Assembly accepts
any commenter on an allow-listed, blueprint-ready issue. No state files change.

**Operator action required on rollback when the env vars were already set.**
After the revert, `BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS` and
`BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS` become inert orphan config — the
bridge no longer reads them, but they remain in the environment / deploy
manifest and may mislead future operators. The rollback runbook MUST instruct
the operator to unset both env vars (or remove the corresponding lines from
the deploy manifest) and to verify their absence with the post-rollback grep
below. Leaving the orphan config in place is not a security regression (the
gate logic is gone), but it is a misleading surface.

Post-rollback verification:

1. `pytest tests/unit/test_assembly_routing.py tests/unit/test_assembly_preconditions.py`
   stays green against the pre-CHG branch.
2. `tests/bdd/features/assembly.feature` runs the five existing scenarios that
   shipped with VOY-1817.
3. `! grep -rn "BRIDGE_ASSEMBLY_AUTHORIZED" voyager/ tests/ config.example.toml`
   prints nothing — proves the gate symbols are gone from code, tests, and
   config example.
4. `! grep -n "unauthorized_actor" voyager/bots/assembly/constants.py
   voyager/bots/assembly/comment.py` prints nothing — proves the refusal
   reason is gone.
5. `gh api /repos/iterwheel/voyager/installation` is unchanged.
6. `_recent_writebacks` ring entries written after rollback do not carry
   `validation.actor` or `refusal.actor_login` keys (the ring is in-memory and
   clears on bridge restart; restart after deploy to flush).

## Surfaces

| # | Surface | Change |
|---|---------|--------|
| 1 | `voyager/bots/assembly/actor.py` | New module. Exposes `evaluate_actor_authorization(payload) -> ActorAuthorization` (dataclass with `ok: bool`, `reason: str \| None`, `actor_login: str \| None`, `actor_association: str \| None`, `actor_type: str \| None`, `actor_sender_login: str \| None`, `sender_divergent: bool`, `matched_signal: str \| None`). Logic (per D7 precedence): (1) extract `comment.user.login`, `comment.user.type`, `comment.author_association`, `sender.login`; (2) when login is missing / malformed, deny with `reason="unauthorized_actor"`; (3) when `actor_type == "Bot"` **OR** `login.endswith("[bot]")` (case-insensitive suffix check), deny — this fires **before** allow-list / association checks per D7; (4) otherwise pass when the canonical-lower-cased login is in the env allow-list (per D10), OR the upper-cased association is in the trusted set; (5) when `sender.login` is present and differs from `comment.user.login`, set `sender_divergent=True` and log a `WARNING` via `_log.warning("assembly_actor_sender_divergence: comment=%r sender=%r", comment_login, sender_login)` — the gate still bases its decision on `comment.user.login` per D8. `matched_signal` is one of `"allow_list"` / `"association"` on pass and `None` on deny. |
| 2 | `voyager/bots/assembly/constants.py` | Add `REFUSAL_UNAUTHORIZED_ACTOR = "unauthorized_actor"`, `AUTHORIZED_ACTORS_ENV = "BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS"`, `AUTHORIZED_ASSOCIATIONS_ENV = "BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS"`, `DEFAULT_AUTHORIZED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")`. No changes to existing names. |
| 3 | `voyager/bots/assembly/routing.py` | In `route_assembly_event`, after `should_run_assembly` and command parse, call `evaluate_actor_authorization(payload)`. On deny, build the refusal-shape route with `validation.status = "assembly_refused"`, `validation.refusal = {"reason": "unauthorized_actor", "missing_labels": [], "outside_allow_list": False, "actor_login": <login>, "actor_association": <assoc>}`, no `contract`, no `branch_name`. The route still flows through `_filter_routes_by_repository` and the writeback dispatcher so the refusal comment is upserted. Add an `"actor"` block to the route's `validation` payload with `{login, association, type, matched_signal}` for operator audit. |
| 4 | `voyager/bots/assembly/writeback.py` | No new dispatch branch; the existing refusal short-circuit (`refusal_router is not None or contract_dict is None`) already routes through `_post_refusal_comment`. Only change: the refusal payload now optionally carries `actor_login` / `actor_association`, which the renderer in Surface 5 reads. |
| 5 | `voyager/bots/assembly/comment.py` | Extend `_format_refusal` to recognise `reason == "unauthorized_actor"` and render the actor identity, association, and a short pointer to VOY-1805 §5 / VOY-1818 for the policy. Existing reasons keep their existing rendering. The marker is unchanged. |
| 6 | `voyager/bots/assembly/__init__.py` | Re-export `evaluate_actor_authorization`, `ActorAuthorization`, `REFUSAL_UNAUTHORIZED_ACTOR`, `AUTHORIZED_ACTORS_ENV`, `AUTHORIZED_ASSOCIATIONS_ENV`, `DEFAULT_AUTHORIZED_ASSOCIATIONS` so tests and consumers do not reach into private modules. |
| 7 | `tests/unit/test_assembly_actor.py` | New unit suite. RED→GREEN→REFACTOR cadence: write each test failing first, implement just enough of `actor.py` to pass, refactor in a final commit. Uses `monkeypatch.setenv` / `monkeypatch.delenv` for all env-var manipulation so parallel/xdist runs cannot leak `BRIDGE_ASSEMBLY_AUTHORIZED_*` across tests. Cases: (a) authorized via allow-list; (b) authorized via association (`OWNER` / `MEMBER` / `COLLABORATOR`); (c) refused on association `CONTRIBUTOR` / `NONE` / `FIRST_TIME_CONTRIBUTOR`; (d) refused on unrecognized association value (`"FOO"` — must fall through to allow-list, then deny if not in list); (e) refused on missing `comment.user`; (f) refused on missing `sender`; (g) refused on empty-string login; (h) refused on `actor.type == "Bot"`; (i) refused on `[bot]`-suffix login even when login is on the allow-list (D7 precedence; explicit bot-on-allow-list test); (j) refused on malformed payload (non-dict `comment`, non-dict `user`); (k) env-var parsing — whitespace + comma-separated, **case-insensitive** logins (operator typing `Ryosaeba1985` matches webhook `ryosaeba1985`; D10), case-insensitive associations; (l) **default deny on associations** when `BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS` is **unset** (only the allow-list is consulted; D6); (m) **default trusted associations** when `BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS=""` is **set but empty** (D6 — exercise the empty branch explicitly); (n) `sender.login != comment.user.login` divergence: gate uses `comment.user.login`, sets `sender_divergent=True`, and emits the WARNING log entry (assert via `caplog`); (o) full deny when both env vars are unset and no allow-list match. |
| 8 | `tests/unit/test_assembly_routing.py` | Extend the existing routing tests. New scenarios: (1) authorized actor → route built; (2) unauthorized actor → refusal route with `reason == "unauthorized_actor"`; (3) authorized actor but `blueprint-ready` missing → existing precondition refusal still wins (actor gate first per D3 — assert by checking that an unauthorized-actor request with the same missing label produces `reason == "unauthorized_actor"` not `missing_blueprint_ready_label`); (4) **negative assertion** — existing refusal payloads for `pr_not_issue`, `missing_blueprint_ready_label`, and `missing_stack_type_label` MUST NOT carry `actor_login` or `actor_association` keys (assert via `not in refusal` or key-set equality); (5) update the existing `_comment_payload` helper to include a default authorized `sender.login`, `comment.user.login`, `comment.user.type="User"`, and `comment.author_association="OWNER"` so the pre-existing five route-shape tests continue to pass under the new gate; touch is mechanical, no assertion changes. |
| 9 | `tests/unit/test_assembly_writeback_dispatcher.py` | Add cases: (1) `DRY_RUN=false` — dispatcher receives a route refused at the actor gate; calls `upsert_issue_comment` exactly once with the `unauthorized_actor` body; performs no branch / PR / codex calls. (2) `DRY_RUN=true` — same input route; no `upsert_issue_comment` call at all (the dispatcher already short-circuits dry-run refusals via `_post_refusal_comment` per writeback.py:374); the refusal still appears in the returned result dict so the writeback ring captures it. Both cases use `monkeypatch.setenv` to control `DRY_RUN`. (3) **Negative assertion** — when a non-actor refusal (`pr_not_issue`) reaches the dispatcher, the refusal payload passed to the comment renderer does NOT carry `actor_login` / `actor_association` (regression guard for Surface 5's renderer fork). |
| 10 | `tests/bdd/features/assembly.feature` | Add three scenarios (per #76 acceptance criteria): (A) `/assembly` from an authorized maintainer (`author_association: OWNER`) on a ready, allow-listed issue → route runs the dry-run plan; (B) `/assembly` from an unauthorized contributor (`author_association: CONTRIBUTOR`) → refusal comment with `unauthorized_actor`, no branch / PR / codex / dispatch; (C) `/assembly` from a configured allow-list login whose association is `NONE` → route runs (allow-list overrides association). The existing five scenarios stay green; their fixtures will be updated to include the `OWNER` association so they remain authorized under the new gate (touched in Surface 13). |
| 11 | `tests/bdd/step_defs/test_assembly_steps.py` | Step definitions for the three new scenarios in Surface 10, sharing the existing webhook-fixture loader. New Given steps: "the BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS env contains '<logins>'", "the BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS env contains '<set>'", "the webhook comes from '<login>' with association '<assoc>'". |
| 12 | `tests/fixtures/webhooks/assembly_*.json` | Update the three existing Assembly fixtures (`assembly_command_ready.json`, `assembly_command_not_ready.json`, `assembly_command_missing_stack.json`) to include `sender.login`, `comment.user.login`, `comment.user.type = "User"`, and `comment.author_association = "OWNER"`. Add three new fixtures: `assembly_command_authorized_member.json` (`OWNER`), `assembly_command_unauthorized_contributor.json` (`CONTRIBUTOR`), `assembly_command_allowlist_only.json` (`NONE` association, login on allow-list). |
| 13 | `tests/unit/test_assembly_xtest_*.py` (cross-test suite from VOY-1817 §Phase 6) | The cross-test fixtures consume the same JSON fixtures, so the field additions in Surface 12 propagate. Two cross-tests get explicit assertions: `test_assembly_xtest_preconditions.py` (actor accepted → preconditions still gate `blueprint-ready`), and a new `test_assembly_xtest_actor_authz.py` (one happy path, one deny path) for the independent cross-test author per VOY-1817 §Phase 6 contract. **Independent-author rule restated**: per VOY-1817 §Phase 6, cross-tests are written by a separate subagent that does not see the primary test suite's source — they exercise the public `evaluate_actor_authorization` / `route_assembly_event` API only and assert against documented schemas, not implementation details. The Phase 5 split (Surfaces 7-13 to a tests subagent, Surfaces 1-6/14-16 to an implementation subagent) preserves this independence. |
| 14 | `rules/VOY-1805-SOP-GitHub-Bot-Accounts-and-Responsibilities.md` | Insert a new step **immediately after the existing "Use the canonical first-batch roster" step** (currently step 1) and **before the "Use the Blueprint label standard" step** (currently step 2), titled "Actor authorization for Assembly". The renumbered steps cascade. The new step documents: the two env vars; default deny posture; default trusted associations; allow-list precedence (allow-list match alone is sufficient); D7 bot precedence — bots are refused even on the allow-list; the `unauthorized_actor` refusal reason; explicit statement that unknown actor metadata is refused; reference to VOY-1818 for the gate's evaluation order. Add a Change History row dated 2026-05-23. |
| 15 | `config.example.toml` | Add two commented (default-off) lines in the Assembly App block, mirroring the existing pattern from VOY-1817 Surface 25: `# BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS="frankyxhl ryosaeba1985"` and `# BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS="OWNER MEMBER COLLABORATOR"`. Both stay commented so the default deny is preserved — uncommenting is an explicit operator action. |
| 16 | `rules/VOY-1807-REF-GitHub-App-Registry.md` | Append a sentence to the `iterwheel-assembly` write-back row: "Requires actor authorization per VOY-1818; default deny when `BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS` and `BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS` are unset." Plus a Change History row. |

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | The actor gate runs **inside** `route_assembly_event`, before `validate_preconditions`. | The gate produces a refusal route whose comment must be upserted on the source issue, matching #76 acceptance criterion 4 ("Refusal is visible in the Assembly comment"). A server-level pre-route filter (like `_repository_allowed_for_agent`) would drop the route silently. Mirrors VOY-1817 D4's defense-in-depth posture: routing-time check + dispatcher-time refusal comment. |
| D2 | The gate runs **once at routing time**, not also in the dispatcher. | The actor identity is fixed at webhook ingestion; the comment's author cannot change between routing and dispatch. This is different from preconditions, where the live issue can mutate (a label can be removed). VOY-1817 D4 re-validates preconditions in the dispatcher; this CHG does not re-validate the actor for the same reason: there is no "live actor" to re-fetch. |
| D3 | **Gate ordering**: actor gate first, then preconditions. | When both fail, the actor refusal is the higher-signal one for an operator triaging unexpected behavior ("a stranger tried to trigger Assembly") and avoids leaking issue-state info ("the issue is missing the blueprint label") to an unauthorized commenter. Surface 8 tests assert this ordering. |
| D4 | **Default deny** when both env vars are unset. | Matches VOY-1817 D6's bridge repository allow-list default. The operator must explicitly opt in, so a fresh deploy cannot let the first untrusted commenter trigger code-writing work. The default `DEFAULT_AUTHORIZED_ASSOCIATIONS` tuple only becomes active when the env var is **set but empty** — see D6. |
| D5 | **Either signal is sufficient**: allow-list OR association. | The allow-list is the precise control for external collaborators and edge cases; the association covers the common maintainer case without per-login configuration churn. Matches the way GitHub Actions community workflows commonly filter commenters (`author_association` OR explicit list). |
| D6 | Env-var semantics: **unset** = default deny on associations (only the allow-list is consulted); **set but empty** = default trusted associations (`OWNER`, `MEMBER`, `COLLABORATOR`); **set to a value** = use exactly that value. | Distinguishes "I never configured this" from "I want defaults" from "I want a custom policy". The unset-vs-empty distinction follows the convention already used by `BRIDGE_ALLOWED_REPOSITORIES_*` per `voyager/server.py` `_repository_allowed_for_agent`. |
| D7 | Bots (`comment.user.type == "Bot"` or login ending in `[bot]` — case-insensitive suffix check) are **always denied** regardless of allow-list or association. **Precedence**: the bot check fires **before** the allow-list and association checks; a bot login on `BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS` is still denied. Both the `actor_type == "Bot"` field and the `[bot]` suffix must be checked because GitHub does not always populate `comment.user.type` for legacy bots, and the suffix is a stable identity convention. | Avoids reentrant loops where one bot's comment could trigger Assembly. The explicit precedence rule resolves the apparent contradiction with D5 ("either signal sufficient") — D5 governs the allow-list/association decision; D7 is an upstream filter that runs first. Operators who genuinely want a bot to invoke Assembly must request a follow-up CHG to explicitly opt the bot in; this is a deliberate friction. |
| D8 | The gate ignores `sender.login` for authorization decisions and uses `comment.user.login` as the canonical actor identity. | GitHub sets `sender` to the user whose action emitted the webhook; for `issue_comment.created` this is the same as `comment.user`, but the comment author is the more semantically precise field. Cross-check is logged when they diverge (defensive — should not happen for `issue_comment.created`). |
| D9 | Missing or malformed actor metadata is treated as **deny**, not error. | An attacker who could craft a malformed payload should not be able to bypass the gate by deleting `comment.user`. Logging the malformed shape and refusing keeps the bridge resilient. |
| D10 | Login comparison is **case-insensitive** with canonical-lowercase normalization on both env-list entries and the webhook `comment.user.login`. Association comparison is also case-insensitive. | GitHub logins are case-insensitive in URLs and de-facto-canonical in the API, but env vars are operator-typed: a deploy manifest with `Ryosaeba1985` should still authorize `ryosaeba1985`. Reverses the round-1 decision per MiniMax P2 (ergonomics-over-strict-matching). Implementation: lowercase both sides at compare time; the dataclass `actor_login` stores the canonical lowercase value, so log lines and refusal payloads are consistent. |
| D11 | The `validation.actor` block exposes the gate's decision context to the writeback ring (`_recent_writebacks`) and the refusal comment renderer. **Visibility only holds for AL+ routes**; AL−/AC− routes are dropped by `_filter_routes_by_repository` before reaching the ring. The Gate Corner Table makes this explicit. | Per #76 acceptance criterion 4, the refusal must be visible to authorized observers; per VOY-1817's writeback ring conventions, every routing decision that survives the repository allow-list must be inspectable through `/e2e/recent_writebacks`. The AL−/AC− case has no visibility surface beyond the existing `repository_allowlist_denied` warning log — this is the same posture as any other AL− route. |
| D12 | **Refusal-comment disclosure non-goal**: the `unauthorized_actor` refusal body MUST NOT enumerate the allow-list contents, the trusted-association set, or any other operator-configured value. Surface 5 renders only: the refusal heading, the `unauthorized_actor` reason, the actor's own login + association (so the actor knows which identity was checked), and a pointer to VOY-1805 / VOY-1818 for the policy. | Refusal bodies are public on issue comments. Echoing the allow-list back to an unauthorized commenter would let an attacker enumerate the org's trusted-actor set by triggering refusals on a public repo. The actor's own login/association are not sensitive — the commenter already knows them. |
| D13 | **Sender divergence handling**: when `payload.sender.login` is present and differs from `payload.comment.user.login`, the gate uses `comment.user.login` as authoritative (per D8) and emits a `WARNING` log line. The decision is not changed; the divergence is recorded in `ActorAuthorization.actor_sender_login` + `sender_divergent` and surfaced in the `validation.actor` block. | For `issue_comment.created`, GitHub always sets both to the same login; a divergence implies an unusual webhook path (e.g., GitHub App acting on behalf of, or a forged payload). The log is the audit trail; the conservative behavior (use `comment.user.login`) avoids surprising operators while flagging the anomaly. |

## Gate Corner Table

The actor gate is the new upstream gate. It composes with the existing
`AL` (allow-list), `DR` (DRY_RUN), and `BE` (backend) gates from VOY-1817.

Legend: `AC+` = actor authorized (allow-list or trusted association),
`AC−` = actor refused. Other letters from VOY-1817 Gate Corner Table.

| AC | AL | DR | BE | Route built? | Survives `_filter_routes_by_repository`? | Adapter runs? | GitHub writes? | Result shape |
|----|----|----|----|--------------|-----|---------------|----------------|--------------|
| AC− | AL+ | DR+ | * | Yes (refusal-shape, `validation.status="assembly_refused"`) | Yes | No | No (DR+ short-circuits the refusal comment per writeback.py:374) | `applied: false, refusal.reason: "unauthorized_actor", refusal.actor_login: <login>, refusal.actor_association: <assoc>, assembly_comment_id: null`. Ring entry present. |
| AC− | AL+ | DR− | * | Yes (refusal-shape) | Yes | No | **Comment-only** — `_post_refusal_comment` upserts `unauthorized_actor` body | `applied: false, refusal.reason: "unauthorized_actor", refusal.actor_login: <login>, refusal.actor_association: <assoc>, assembly_comment_id: <int>`. Ring entry present. |
| AC− | AL− | * | * | Yes (refusal-shape, built inside `route_assembly_event`) | **No — dropped by `_filter_routes_by_repository`** | No | No | No ring entry, no comment. Bridge response: `filtered.routes` includes the dropped refusal. Existing `repository_allowlist_denied` warning logs the deny. **The actor refusal is not GitHub-visible in this corner.** |
| AC+ | AL− | * | * | Yes (existing VOY-1817 row) | No | No | No | Same as VOY-1817 row 1. |
| AC+ | AL+ | DR+ | BE=dry | Existing VOY-1817 row | Yes | Yes (plan) | No | Same as VOY-1817 row 2. |
| AC+ | AL+ | DR+ | BE=pi | Existing VOY-1817 row | Yes | NotImplementedError | No | Same as VOY-1817 row 3. |
| AC+ | AL+ | DR− | BE=dry | Existing VOY-1817 row | Yes | Yes (plan) | Comment-only | Same as VOY-1817 row 4. |
| AC+ | AL+ | DR− | BE=pi | Existing VOY-1817 row | Yes | NotImplementedError | Progress comment only | Same as VOY-1817 row 5. |

The three new AC− rows make explicit what GLM-P1-#3 flagged: the audit-ring
and refusal-comment visibility claims hold only on the AL+ subset; an
AC−/AL− invocation is operationally indistinguishable from a generic
allow-list deny at the bridge level. This is intentional — the repository
allow-list is the most-upstream gate; running the actor gate inside
`route_assembly_event` does not promote AC− routes past the server-level
filter.

## ActorAuthorization Schema

```python
@dataclass(frozen=True)
class ActorAuthorization:
    ok: bool
    reason: str | None  # None on pass, "unauthorized_actor" on fail
    actor_login: str | None  # canonical lowercase comment.user.login (None when malformed)
    actor_association: str | None  # canonical upper-case association or None
    actor_type: str | None  # "User" | "Bot" | None
    actor_sender_login: str | None  # canonical lowercase sender.login or None
    sender_divergent: bool  # True when actor_login != actor_sender_login (both non-None)
    matched_signal: str | None  # "allow_list" | "association" | None when refused
```

The dataclass is `frozen=True` and exported from `voyager.bots.assembly`.
`sender_divergent` is informational (D13); the WARNING log is the audit trail.

## Refusal Payload Extension

The existing refusal payload (VOY-1817 §Writeback Result Schema) gains two
optional fields when `reason == "unauthorized_actor"`:

```python
{
    "reason": "unauthorized_actor",
    "missing_labels": [],
    "outside_allow_list": False,
    "actor_login": str | None,
    "actor_association": str | None,
}
```

Existing refusals (`pr_not_issue`, `missing_blueprint_ready_label`, etc.) do
not carry these fields and are unaffected.

## Refusal Comment Body (informative)

```
<!-- iterwheel:assembly-implementation -->
**Assembly refused this invocation.**

Reason: `unauthorized_actor`

Actor: `<login>` (association: `<assoc>` / `none`)

Assembly only writes code when the triggering actor is authorized per
VOY-1805 §Actor Authorization for Assembly. See VOY-1818 for the gate
policy and how to add an actor to the allow-list.
```

## Testing / Verification

**TDD cadence**: tests in Surfaces 7-13 are written before the corresponding
implementation (Surfaces 1-6, 14-16) and assert against the schemas in this
CHG. Each test is committed in a **RED** state first (failing because the
gate does not yet exist), then **GREEN** (Surface 1's `actor.py` lands), then
a **REFACTOR** commit consolidates any helper extraction in `actor.py` or
the test files. The Phase 5 split between the tests subagent and the
implementation subagent enforces RED-before-GREEN: the tests subagent
finishes its RED batch before the implementation subagent starts, and the
implementation subagent does not see the tests until it begins Surface 1.

**Env-var test isolation**: every test that reads or writes
`BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS` or
`BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS` MUST use pytest's
`monkeypatch.setenv` / `monkeypatch.delenv` so the env mutation is scoped to
the test and reverted on teardown. Direct `os.environ[...] = ...` writes are
banned — they leak across tests in parallel/xdist runs and across the BDD
session fixture. CI must run `pytest -p no:cacheprovider -n auto` at least
once before merge to surface any isolation regression.

Unit:

- `tests/unit/test_assembly_actor.py` — every case enumerated in Surface 7.
- `tests/unit/test_assembly_routing.py` — five scenarios per Surface 8.
- `tests/unit/test_assembly_writeback_dispatcher.py` — three new cases per
  Surface 9 (both `DRY_RUN` values + the negative-assertion case).
- `tests/unit/test_assembly_preconditions.py` — unchanged behavior; new
  fixtures still pass preconditions when authorized.

BDD:

- `tests/bdd/features/assembly.feature` — three new scenarios (Surface 10),
  five existing scenarios remain green with updated fixtures.

Cross-test (per VOY-1817 §Phase 6):

- `tests/unit/test_assembly_xtest_actor_authz.py` — independent author
  exercises one happy path and one deny path through the same public API
  (`evaluate_actor_authorization` and `route_assembly_event` only).

Fixture-scope audit (per GLM-P2-#7):

The Surface 12 fixtures (`assembly_command_*.json`) are consumed by:
`tests/unit/test_assembly_routing.py`, `tests/unit/test_assembly_preconditions.py`,
`tests/unit/test_assembly_xtest_preconditions.py`,
`tests/unit/test_assembly_xtest_commands.py`,
`tests/bdd/step_defs/test_assembly_steps.py`,
`tests/bdd/step_defs/test_assembly_xtest_steps.py`. The shape additions
(`sender`, `comment.user.login`, `comment.user.type`, `comment.author_association`)
are additive — no existing test asserts that those keys are absent — but
the CHG-author MUST grep-verify before merge:

```
git grep -nl "assembly_command_" tests/
```

Any consumer not listed above gets explicit review and a follow-up note in
the PR description.

Tooling:

- `ruff check .`
- `mypy voyager`
- `pytest tests/unit tests/bdd -k assembly`
- `pytest -p no:cacheprovider -n auto` (env-isolation regression check)

The full suite (`pytest`) must remain green; only the Assembly fixtures
change shape, and the changes are additive (the existing assertions on
those fixtures continue to hold).

## Open Questions for Reviewers

1. **Default association set.** This CHG defaults to
   `OWNER,MEMBER,COLLABORATOR`. Should `CONTRIBUTOR` be included? Current
   proposal: no, because `CONTRIBUTOR` means "has previously contributed an
   issue or PR", which is too permissive for code-writing authority.
2. **Per-repository overrides.** Should the authorization policy be
   configurable per repository (matching the `BRIDGE_ALLOWED_REPOSITORIES_*`
   per-agent pattern)? Current proposal: defer; bridge-wide policy is
   simpler for the first iteration. Revisit when more repos enter the
   Assembly production allow-list.
3. **Bot opt-in.** Should we provide a separate env var to opt-in specific
   bot logins (e.g., to let a future Iterwheel orchestrator bot drive
   Assembly)? Current proposal: defer; bot triggers are a different threat
   model and warrant their own CHG.

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-23 | Initial CHG draft for issue #76 — Assembly actor authorization gate. | Claude (via VOY-1811 #76) |
| 2026-05-23 | Round 1 plan-review remediation (GLM 8.0 FIX, DeepSeek 8.6 FIX, MiniMax 9.3 PASS): **P1 fixes** — D7 precedence rule made explicit (bot check fires before allow-list/association; bot-on-allow-list still denied; Surface 1 logic + D7 reconciled to include the `[bot]`-suffix check on both sides; Surface 7 adds the bot-on-allow-list test case) [GLM #1/#2 + DeepSeek + MiniMax]; Gate Corner Table expanded from one collapsed AC− row into three explicit rows (AC−/AL+/DR+, AC−/AL+/DR−, AC−/AL−) so the audit-ring + comment-visibility claims are corner-accurate [GLM #3 + MiniMax]; D11 visibility scope clarified to AL+ subset only [GLM #3]; Surface 7 adds the D6 set-but-empty case and the D7 `[bot]`-suffix case [DeepSeek]; §Rollback plan extended with explicit orphan-env-var operator action and `grep -rn` verification [DeepSeek + MiniMax]. **P1/P2 hardening** — added D12 (refusal-comment disclosure non-goal; refusal MUST NOT enumerate allow-list / association set) and §Out of Scope row [DeepSeek]; added D13 (sender-vs-comment.user divergence logging + `actor_sender_login` / `sender_divergent` fields on `ActorAuthorization`) [DeepSeek + GLM]; Surface 8 adds negative-assertion case (existing refusal reasons must NOT carry actor fields) [DeepSeek]; Surface 9 adds DR+ and negative-assertion cases [DeepSeek]; D10 reversed from case-sensitive to case-insensitive login compare (lowercase canonicalization) [MiniMax]; Surface 7 / §Testing add `monkeypatch.setenv` env-isolation requirement + `pytest -n auto` regression check [MiniMax]; Surface 13 restates the independent-author rule one line + ties to the Phase 5 subagent split [MiniMax]; Surface 14 specifies the VOY-1805 step insertion point [GLM #6]; Surface 8 documents the `_comment_payload` helper update [self-noticed during review]; §Testing adds explicit RED-GREEN-REFACTOR cadence and a fixture-scope audit list [GLM #5/#7]. | Claude (via VOY-1811 #76) |
| 2026-08-02 | Applied Ruff 0.16.1 formatting to an embedded code example; no semantic changes. | Codex |
| 2026-08-30 | Lifecycle closeout: implementation PR #77 merged as `376a278e`; source issue #76 is closed. Status changed from Proposed to Completed. | Codex |
