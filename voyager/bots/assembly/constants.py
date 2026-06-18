"""Assembly bot — constants and canonical boundary lists.

Per VOY-1817 D9: the forbidden-operations and verification-commands tuples
must live here and only here. Adapters consuming the Assembly Job Contract
must not invent or override these constants.

``forbidden_operations`` is a verbatim copy of the Deny column from
VOY-1805 §5 (Assembly Allow/Deny table). Any future change to that SOP
must be mirrored here in a separate review.
"""

from __future__ import annotations

ASSEMBLY_AGENT_SLUG = "iterwheel-assembly"
ASSEMBLY_AGENT_ID = "github-assembly-agent"
ASSEMBLY_COMMENT_MARKER = "<!-- iterwheel:assembly-implementation -->"

# Per VOY-1817 §What and §Surface 3.
ASSEMBLY_COMMANDS: tuple[str, ...] = ("/assembly", "/implement")

# Gate per VOY-1817 D3 / Gate Corner Table. Default backend is ``dry-run``
# so the production allow-list and DRY_RUN env are two independent gates.
ASSEMBLY_EXECUTION_BACKEND_ENV = "ASSEMBLY_EXECUTION_BACKEND"
ASSEMBLY_BACKEND_DRY_RUN = "dry-run"
ASSEMBLY_BACKEND_FAKE_SUBPROCESS = "fake-subprocess"
ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK = "pi-oh-my-pi-deepseek"
ASSEMBLY_FAKE_SUBPROCESS_ALLOW_ENV = "ASSEMBLY_FAKE_SUBPROCESS_ALLOW"
ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV = "ASSEMBLY_FAKE_SUBPROCESS_OUTPUT"
ASSEMBLY_PI_COMMAND_PATH_ENV = "ASSEMBLY_PI_COMMAND_PATH"
ASSEMBLY_PI_TIMEOUT_SECONDS_ENV = "ASSEMBLY_PI_TIMEOUT_SECONDS"
ASSEMBLY_PI_WORKDIR_ENV = "ASSEMBLY_PI_WORKDIR"
ASSEMBLY_PI_DEFAULT_COMMAND_PATH = "omp"
ASSEMBLY_PI_DEFAULT_TIMEOUT_SECONDS = 900
ASSEMBLY_PI_DEFAULT_WORKDIR = "~/.voyager/state/assembly"
ASSEMBLY_AC_SPOTCHECK_ENV = "ASSEMBLY_AC_SPOTCHECK"
ASSEMBLY_AUDIT_DIR_ENV = "ASSEMBLY_AUDIT_DIR"
ASSEMBLY_AUDIT_DIR_DEFAULT = "~/.voyager/state/assembly/audit"
ASSEMBLY_AUDIT_SOP = "VOY-1823-SOP-Assembly-OMP-Audit-Lookup.md"
ASSEMBLY_VERIFICATION_COMMANDS_ENV = "ASSEMBLY_VERIFICATION_COMMANDS"
ASSEMBLY_VERIFICATION_COMMANDS_REPOSITORY_ENV_PREFIX = "ASSEMBLY_VERIFICATION_COMMANDS_"

# VOY-1811 §Codex Review Trigger Phase 8 — pin per D12.
CODEX_REVIEW_BOT_LOGIN = "chatgpt-codex-connector[bot]"
CODEX_REVIEW_TRIGGER_BODY = "@codex review"

# Blueprint label required as a precondition.
BLUEPRINT_READY_LABEL = "blueprint-ready"

# Stack labels — Assembly checks for *any* one of these prefixes; the
# canonical list of stack-type-* labels lives in
# voyager.bots.stack.constants.TYPE_LABELS. Re-imported lazily where needed
# to avoid circular imports.
STACK_TYPE_LABEL_PREFIX = "stack-type-"

# Refusal reasons (VOY-1817 §Writeback Result Schema).
REFUSAL_PR_NOT_ISSUE = "pr_not_issue"
REFUSAL_NOT_BLUEPRINT_READY = "missing_blueprint_ready_label"
REFUSAL_MISSING_STACK_TYPE = "missing_stack_type_label"
REFUSAL_REPOSITORY_NOT_ALLOWED = "repository_not_allowed"
REFUSAL_ISSUE_CLOSED = "issue_closed"

# Actor authorization (VOY-1818).
REFUSAL_UNAUTHORIZED_ACTOR = "unauthorized_actor"
AUTHORIZED_ACTORS_ENV = "BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS"
AUTHORIZED_ASSOCIATIONS_ENV = "BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS"
DEFAULT_AUTHORIZED_ASSOCIATIONS: tuple[str, ...] = ("OWNER", "MEMBER", "COLLABORATOR")

# Verification commands the adapter is expected to run before pushing
# commits. Locked per D9 as the default command set; operators may override
# via trusted runtime env for non-Voyager repositories.
VERIFICATION_COMMANDS: tuple[str, ...] = (
    "pytest tests/",
    "ruff check .",
    "mypy voyager",
)

# Forbidden operations — verbatim Deny column from VOY-1805 §5.
# Order preserved from the SOP table. The contract surfaces these to the
# adapter as guard text; tests assert exact equality with this tuple, so do
# not reorder without updating tests/unit/test_assembly_job_contract.py.
FORBIDDEN_OPERATIONS: tuple[str, ...] = (
    "Merge pull requests",
    "Approve its own pull requests",
    "Resolve review threads as a reviewer",
    "Apply `clearance-4-ready-for-merge` or `countdown-go` labels",
    "Modify branch protection rules",
    "Close issues directly without a linked PR",
    "Override Static Fire, Clearance, or Countdown verdicts",
)

# Maximum slug length per D8 — keeps branch names readable, mirrors the
# convention from PRs #70 / #71 / #72.
MAX_SLUG_LENGTH = 50

# Phase mode — controls whether Assembly runs in single-phase (implementer only)
# or two-phase (implementer + testpilot) mode. Default is single-phase for
# backward compatibility.
ASSEMBLY_PHASE_MODE_ENV = "ASSEMBLY_PHASE_MODE"
ASSEMBLY_PHASE_MODE_SINGLE = "single"
ASSEMBLY_PHASE_MODE_TWO_PHASE = "two-phase"
ASSEMBLY_PHASE_MODE_DEFAULT = ASSEMBLY_PHASE_MODE_SINGLE

# Per-phase backend selection env vars. When set, these override the
# global ASSEMBLY_EXECUTION_BACKEND for the specific phase.
ASSEMBLY_IMPLEMENTER_BACKEND_ENV = "ASSEMBLY_IMPLEMENTER_BACKEND"
ASSEMBLY_TESTPILOT_BACKEND_ENV = "ASSEMBLY_TESTPILOT_BACKEND"
# Circuit breaker — caps automated fix rounds per PR (issue #157).
ASSEMBLY_MAX_FIX_ROUNDS_ENV = "ASSEMBLY_MAX_FIX_ROUNDS"
ASSEMBLY_MAX_FIX_ROUNDS_DEFAULT = 8
ASSEMBLY_FIX_ROUND_LABEL_PREFIX = "assembly-fix-round-"
LOOP_CIRCUIT_BROKEN_LABEL = "loop-circuit-broken"
