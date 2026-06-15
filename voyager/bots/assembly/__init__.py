"""Assembly bot — implementation routing and writeback shaping.

See VOY-1817 for the canonical plan.  This package is the only place the
Assembly bot's behavior lives; the bridge wiring is in
``voyager/server.py`` (one import + list append) and the writeback
dispatch branch is in ``voyager/core/writeback.py``
(``dynamic == "assembly_implementation"``).
"""

from __future__ import annotations

from .actor import ActorAuthorization, evaluate_actor_authorization
from .adapters import (
    AdapterExecutionContext,
    AdapterResult,
    DryRunAdapter,
    ExecutionAdapter,
    FakeSubprocessAdapter,
    PiOhMyPiDeepSeekAdapter,
    select_execution_adapter,
)
from .audit import (
    AssemblyAuditManifest,
    audit_manifest_path,
    find_audit_manifest,
    generate_audit_id,
    is_audit_id,
    load_audit_manifest,
    lookup_hint,
    write_audit_manifest,
)
from .branch import make_branch_name
from .commands import AssemblyCommand, parse_assembly_command
from .comment import build_assembly_comment
from .constants import (
    ASSEMBLY_AC_SPOTCHECK_ENV,
    ASSEMBLY_AGENT_ID,
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_AUDIT_DIR_DEFAULT,
    ASSEMBLY_AUDIT_DIR_ENV,
    ASSEMBLY_AUDIT_SOP,
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_BACKEND_FAKE_SUBPROCESS,
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_COMMANDS,
    ASSEMBLY_COMMENT_MARKER,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    ASSEMBLY_FAKE_SUBPROCESS_ALLOW_ENV,
    ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV,
    AUTHORIZED_ACTORS_ENV,
    AUTHORIZED_ASSOCIATIONS_ENV,
    CODEX_REVIEW_BOT_LOGIN,
    CODEX_REVIEW_TRIGGER_BODY,
    DEFAULT_AUTHORIZED_ASSOCIATIONS,
    FORBIDDEN_OPERATIONS,
    REFUSAL_UNAUTHORIZED_ACTOR,
    VERIFICATION_COMMANDS,
)
from .job_contract import AcceptanceCriterionItem, AssemblyJobContract, build_job_contract
from .phase import (
    PhaseMode,
    PhaseName,
    PhaseResult,
    combine_phase_results,
    select_phase_backend,
)
from .preconditions import PreconditionResult, validate_preconditions
from .publish import PublishResult, publish_branch
from .routing import route_assembly_event, should_run_assembly

__all__ = [
    "ASSEMBLY_AC_SPOTCHECK_ENV",
    "ASSEMBLY_AGENT_ID",
    "ASSEMBLY_AGENT_SLUG",
    "ASSEMBLY_AUDIT_DIR_DEFAULT",
    "ASSEMBLY_AUDIT_DIR_ENV",
    "ASSEMBLY_AUDIT_SOP",
    "ASSEMBLY_BACKEND_DRY_RUN",
    "ASSEMBLY_BACKEND_FAKE_SUBPROCESS",
    "ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK",
    "ASSEMBLY_COMMANDS",
    "ASSEMBLY_COMMENT_MARKER",
    "ASSEMBLY_EXECUTION_BACKEND_ENV",
    "ASSEMBLY_FAKE_SUBPROCESS_ALLOW_ENV",
    "ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV",
    "AUTHORIZED_ACTORS_ENV",
    "AUTHORIZED_ASSOCIATIONS_ENV",
    "CODEX_REVIEW_BOT_LOGIN",
    "CODEX_REVIEW_TRIGGER_BODY",
    "DEFAULT_AUTHORIZED_ASSOCIATIONS",
    "FORBIDDEN_OPERATIONS",
    "REFUSAL_UNAUTHORIZED_ACTOR",
    "VERIFICATION_COMMANDS",
    "AcceptanceCriterionItem",
    "ActorAuthorization",
    "AdapterExecutionContext",
    "AdapterResult",
    "AssemblyAuditManifest",
    "AssemblyAuditManifest",
    "AssemblyCommand",
    "AssemblyJobContract",
    "DryRunAdapter",
    "ExecutionAdapter",
    "FakeSubprocessAdapter",
    "PhaseMode",
    "PhaseMode",
    "PhaseName",
    "PhaseResult",
    "PiOhMyPiDeepSeekAdapter",
    "PreconditionResult",
    "PublishResult",
    "audit_manifest_path",
    "build_assembly_comment",
    "build_job_contract",
    "combine_phase_results",
    "evaluate_actor_authorization",
    "find_audit_manifest",
    "generate_audit_id",
    "is_audit_id",
    "load_audit_manifest",
    "lookup_hint",
    "make_branch_name",
    "parse_assembly_command",
    "publish_branch",
    "route_assembly_event",
    "select_execution_adapter",
    "select_phase_backend",
    "should_run_assembly",
    "validate_preconditions",
    "write_audit_manifest",
]
