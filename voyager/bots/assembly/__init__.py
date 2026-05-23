"""Assembly bot — implementation routing and writeback shaping.

See VOY-1817 for the canonical plan.  This package is the only place the
Assembly bot's behavior lives; the bridge wiring is in
``voyager/server.py`` (one import + list append) and the writeback
dispatch branch is in ``voyager/core/writeback.py``
(``dynamic == "assembly_implementation"``).
"""

from __future__ import annotations

from .adapters import (
    AdapterResult,
    DryRunAdapter,
    ExecutionAdapter,
    PiOhMyPiDeepSeekAdapter,
    select_execution_adapter,
)
from .branch import make_branch_name
from .commands import AssemblyCommand, parse_assembly_command
from .comment import build_assembly_comment
from .constants import (
    ASSEMBLY_AGENT_ID,
    ASSEMBLY_AGENT_SLUG,
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_COMMANDS,
    ASSEMBLY_COMMENT_MARKER,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    CODEX_REVIEW_BOT_LOGIN,
    CODEX_REVIEW_TRIGGER_BODY,
    FORBIDDEN_OPERATIONS,
    VERIFICATION_COMMANDS,
)
from .job_contract import AssemblyJobContract, build_job_contract
from .preconditions import PreconditionResult, validate_preconditions
from .routing import route_assembly_event, should_run_assembly

__all__ = [
    "ASSEMBLY_AGENT_ID",
    "ASSEMBLY_AGENT_SLUG",
    "ASSEMBLY_BACKEND_DRY_RUN",
    "ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK",
    "ASSEMBLY_COMMANDS",
    "ASSEMBLY_COMMENT_MARKER",
    "ASSEMBLY_EXECUTION_BACKEND_ENV",
    "CODEX_REVIEW_BOT_LOGIN",
    "CODEX_REVIEW_TRIGGER_BODY",
    "FORBIDDEN_OPERATIONS",
    "VERIFICATION_COMMANDS",
    "AdapterResult",
    "AssemblyCommand",
    "AssemblyJobContract",
    "DryRunAdapter",
    "ExecutionAdapter",
    "PiOhMyPiDeepSeekAdapter",
    "PreconditionResult",
    "build_assembly_comment",
    "build_job_contract",
    "make_branch_name",
    "parse_assembly_command",
    "route_assembly_event",
    "select_execution_adapter",
    "should_run_assembly",
    "validate_preconditions",
]
