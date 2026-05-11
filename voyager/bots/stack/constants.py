"""Stack bot — constants and label definitions."""

from __future__ import annotations

STACK_AGENT_SLUG = "iterwheel-stack"
STACK_AGENT_ID = "github-stack-agent"
STACK_COMMENT_MARKER = "<!-- iterwheel:stack-classification -->"
STACK_NEEDS_REVIEW_LABEL = "stack-needs-review"
STACK_CLASSIFIER_VERSION = "stack-v2"

STACK_TYPES = (
    "task",
    "bug",
    "feature",
    "docs",
    "refactor",
    "chore",
    "ci",
    "test",
    "spike",
)
STACK_AREAS = (
    "github",
    "automation",
    "docs",
    "ci",
    "tests",
    "frontend",
    "backend",
    "infra",
    "unknown",
)
STACK_SIZES = ("xs", "s", "m", "l", "xl")
STACK_RISKS = ("low", "medium", "high")

TYPE_LABELS = tuple(f"stack-type-{item}" for item in STACK_TYPES)
AREA_LABELS = tuple(f"stack-area-{item}" for item in STACK_AREAS)
SIZE_LABELS = tuple(f"stack-size-{item}" for item in STACK_SIZES)
RISK_LABELS = tuple(f"stack-risk-{item}" for item in STACK_RISKS)
ALL_STACK_LABELS = (
    TYPE_LABELS + AREA_LABELS + SIZE_LABELS + RISK_LABELS + (STACK_NEEDS_REVIEW_LABEL,)
)

ISSUE_KIND_TO_TYPE: dict[str, str] = {
    "task": "task",
    "bug": "bug",
    "feature": "feature",
    "docs": "docs",
    "refactor": "refactor",
    "chore": "chore",
    "ci": "ci",
    "test": "test",
    "spike": "spike",
}
CONVENTIONAL_TYPE_TO_TYPE: dict[str, str] = {
    "feat": "feature",
    "fix": "bug",
    "docs": "docs",
    "refactor": "refactor",
    "chore": "chore",
    "ci": "ci",
    "test": "test",
    "perf": "refactor",
    "build": "ci",
}

TYPE_FIELD_NAMES = ("Stack Type", "Work Type")
AREA_FIELD_NAMES = ("Stack Area", "Area")

TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "task": ("task", "maintenance"),
    "bug": ("bug", "fix", "failure", "regression"),
    "feature": ("feature", "feat", "enhancement"),
    "docs": ("docs", "doc", "documentation"),
    "refactor": ("refactor", "cleanup", "rename", "sop amendment", "policy amendment"),
    "chore": ("chore", "housekeeping"),
    "ci": ("ci", "build", "github actions"),
    "test": ("test", "tests", "coverage"),
    "spike": ("spike", "research", "investigation"),
}
AREA_ALIASES: dict[str, tuple[str, ...]] = {
    "github": ("github", "repo", "repository", "issues", "labels", "webhooks"),
    "automation": ("automation", "agent", "bot", "orchestrator", "workflow", "review panel"),
    "docs": ("docs", "documentation", "sop", "adr", "runbook", "changelog"),
    "ci": ("ci", "build", "github actions", "checks"),
    "tests": ("tests", "test", "coverage"),
    "frontend": ("frontend", "ui", "react"),
    "backend": ("backend", "api", "server", "database"),
    "infra": ("infra", "deploy", "cloudflare", "tailscale", "secrets"),
}

AREA_SIGNALS: dict[str, tuple[tuple[str, int], ...]] = {
    "github": (
        ("webhook", 5),
        ("github app", 4),
        ("issue template", 4),
        ("github", 4),
        ("repository", 2),
        ("repo", 2),
        ("blueprint", 1),
        ("stack", 1),
        ("countdown", 1),
        ("label", 1),
        ("labels", 1),
        ("issue", 1),
        ("issues", 1),
        ("pull request", 1),
        ("pr", 1),
    ),
    "automation": (
        ("orchestrator", 5),
        ("review panel", 5),
        ("review panels", 5),
        ("provider", 4),
        ("providers", 4),
        ("automation", 4),
        ("workflow", 3),
        ("scheduler", 3),
        ("agent", 2),
        ("agents", 2),
        ("bot", 2),
        ("bots", 2),
        ("cron", 2),
        ("gate", 2),
        ("loop", 2),
    ),
    "docs": (
        ("sop amendment", 5),
        ("sop", 4),
        ("adr", 4),
        ("chg", 4),
        ("changelog", 4),
        ("readme", 4),
        ("rules", 3),
        ("docs", 3),
        ("documentation", 3),
        ("runbook", 3),
    ),
    "ci": (
        ("github actions", 5),
        ("check run", 4),
        ("status check", 4),
        ("ci", 4),
        ("actions", 3),
        ("action", 2),
        ("build", 3),
    ),
    "tests": (
        ("pytest", 5),
        ("unittest", 5),
        ("coverage", 4),
        ("fixture", 4),
        ("fixtures", 4),
        ("tests", 3),
        ("test", 3),
    ),
    "frontend": (
        ("frontend", 5),
        ("react", 4),
        ("component", 3),
        ("browser", 3),
        ("css", 3),
        ("html", 3),
        ("page", 2),
        ("ui", 2),
    ),
    "backend": (
        ("backend", 5),
        ("database", 4),
        ("endpoint", 4),
        ("server", 4),
        ("worker", 3),
        ("api", 3),
        ("db", 3),
    ),
    "infra": (
        ("cloudflare", 5),
        ("tailscale", 5),
        ("linode", 5),
        ("private key", 5),
        ("secret", 4),
        ("secrets", 4),
        ("permission", 4),
        ("deploy", 4),
        ("infra", 4),
        ("tunnel", 4),
    ),
}

RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "high": (
        "auth",
        "permission",
        "secret",
        "token",
        "private key",
        "billing",
        "payment",
        "merge",
        "deploy",
        "production",
        "migration",
        "security",
    ),
    "medium": (
        "api",
        "webhook",
        "workflow",
        "database",
        "concurrency",
        "rate limit",
        "installation",
        "cross-account",
        "label",
    ),
}
