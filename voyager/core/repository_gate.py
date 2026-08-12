"""Repository-allowlist gate shared by the webhook route and the Clearance pipeline.

Moved out of ``voyager.server`` (CHG-1842) so ``voyager.bots.clearance.pipeline``
can reuse the exact same ``_repository_allowed_for_agent`` predicate the
webhook route uses without creating an import cycle (``voyager.server`` imports
``voyager.bots.clearance``, which loads ``pipeline.py``).
"""

from __future__ import annotations

import os
import re
from typing import Any

from voyager.core.writeback import dry_run_enabled


def _allowed_repositories_env_key(agent_slug: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", agent_slug).strip("_").upper()
    return f"BRIDGE_ALLOWED_REPOSITORIES_{normalized}"


def _parse_allowed_repositories(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip().lower() for item in re.split(r"[\s,]+", value) if item.strip()}


def _repository_pattern_matches(pattern: str, repository: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("/*"):
        owner = pattern[:-2]
        repo_owner, separator, repo_name = repository.partition("/")
        return repo_owner == owner and separator == "/" and bool(repo_name) and "/" not in repo_name
    return pattern == repository


def _repository_allowed_for_agent(
    repository: str | None,
    agent_slug: str,
    cfg: Any | None = None,
) -> bool:
    """Return whether a route may run for this repository and agent.

    Production defaults to deny when no allow-list is configured. Dry-run keeps
    the historical permissive behavior so local routing tests and exploratory
    dry-runs do not need allow-list env setup.
    """
    specific_key = _allowed_repositories_env_key(agent_slug)
    if specific_key in os.environ:
        allowed = _parse_allowed_repositories(os.environ.get(specific_key))
    elif "BRIDGE_ALLOWED_REPOSITORIES" in os.environ:
        allowed = _parse_allowed_repositories(os.environ.get("BRIDGE_ALLOWED_REPOSITORIES"))
    else:
        bridge = getattr(cfg, "bridge", None)
        allowed = set(
            (getattr(bridge, "allowed_repositories", {}) or {}).get(agent_slug.lower(), ())
        )
    if not allowed:
        return dry_run_enabled(cfg)
    if not repository:
        return False
    normalized_repo = repository.strip().lower()
    return any(_repository_pattern_matches(pattern, normalized_repo) for pattern in allowed)
