"""Constants for the changelog merge drafter."""

from __future__ import annotations

from voyager.bots.assembly.constants import ASSEMBLY_AGENT_SLUG

CHANGELOG_AGENT_SLUG = ASSEMBLY_AGENT_SLUG
CHANGELOG_AGENT_ID = "github-changelog-agent"
CHANGELOG_DYNAMIC = "changelog_draft"
CHANGELOG_FILE = "CHANGELOG.md"
CHANGELOG_DEFAULT_BASE = "main"
CHANGELOG_BRANCH_PREFIX = "changelog/pr-"
CHANGELOG_APP_SLUG = ASSEMBLY_AGENT_SLUG

CHANGELOG_RELEVANT_LABELS: frozenset[str] = frozenset(
    {
        "bug",
        "bugfix",
        "enhancement",
        "feature",
        "fix",
        "performance",
        "perf",
        "security",
        "stack-type-bug",
        "stack-type-feature",
        "stack-type-task",
    }
)

CHANGELOG_SKIP_LABELS: frozenset[str] = frozenset(
    {
        "changelog-skip",
        "chore",
        "ci",
        "dependencies",
        "deps",
        "documentation",
        "docs",
        "no-changelog",
        "refactor",
        "skip-changelog",
        "stack-type-chore",
        "stack-type-ci",
        "stack-type-docs",
        "stack-type-refactor",
        "stack-type-spike",
        "stack-type-test",
        "style",
        "test",
        "tests",
    }
)
