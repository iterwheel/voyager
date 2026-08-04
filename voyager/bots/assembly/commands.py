"""Assembly bot — command parsing.

Per VOY-1817 Surface 3: ``/assembly`` or ``/implement`` must match at the
*start of a line* in the comment body (so that a body like
``please run /assembly later`` does not silently fire).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .constants import ASSEMBLY_COMMANDS

# Match a leading slash-command on a line, optionally with whitespace
# before it.  Captures the command (without the slash) and the rest of
# the line for flag parsing.  re.MULTILINE so the start anchor matches
# every line, not just the first one.
#
# Codex round-4 P1: the trailing ``(?=[ \t\r]|$)`` lookahead enforces a
# hard token boundary so neighbouring strings like ``/assemblyx``,
# ``/assembly-now``, or ``/implementation`` do NOT match.  Only an exact
# command followed by whitespace or end-of-line is accepted; this
# matters in production where a typo could otherwise trigger real
# GitHub mutations.
#
# Codex round-5 P1: ``\r`` is included in the boundary so GitHub
# webhook bodies with CRLF line endings (e.g. ``/assembly\r\n``) still
# match.  Without ``\r`` in the class, the lookahead fails after ``y``
# because ``$`` in MULTILINE mode anchors before ``\n``, not before
# ``\r``, and ``\r`` is neither tab nor space.
_COMMAND_RE = re.compile(
    r"^[ \t]*(/(?:assembly|implement))(?=[ \t\r]|$)(?P<rest>[^\n]*)$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class AssemblyCommand:
    """Parsed Assembly slash-command invocation."""

    command: str  # canonical lowercase form, e.g. "/assembly"
    dry_run: bool
    allow_missing_stack: bool
    resume: bool


# Canonical flag names (without leading --)
_KNOWN_FLAGS: frozenset[str] = frozenset({
    "dry-run",
    "allow-missing-stack",
    "resume",
})

# Mapping of normalized flag names back to their canonical form.
# This handles common typo patterns: underscores, missing hyphens.
_FLAG_ALIASES: dict[str, str] = {
    "dry-run": "dry-run",
    "dry_run": "dry-run",      # underscore typo
    "dryrun": "dry-run",       # missing hyphen
    "allow-missing-stack": "allow-missing-stack",
    "allow_missing_stack": "allow-missing-stack",  # underscore typo
    "allowmissingstack": "allow-missing-stack",     # missing hyphens
    "resume": "resume",
}


def _normalize_flag(flag: str) -> str:
    """Normalize a flag token for matching.

    Handles common typo patterns:
    - ``--dry_run`` → ``--dry-run`` (underscore → hyphen)
    - ``--dryrun`` → ``--dry-run`` (missing hyphen)
    - ``--allow_missing_stack`` → ``--allow-missing-stack``
    - ``--allowmissingstack`` → ``--allow-missing-stack``
    """
    raw = flag.lstrip("-")
    # Collapse runs of hyphens and trim leading/trailing hyphens
    normalized = re.sub(r"-+", "-", raw.replace("_", "-")).strip("-")
    # Look up canonical form via aliases
    return _FLAG_ALIASES.get(normalized, normalized)


def parse_assembly_command(body: str | None) -> AssemblyCommand | None:
    """Return the parsed Assembly command, or ``None`` if no command matches.

    Behavior:
    - ``/assembly`` and ``/implement`` are equivalent triggers.
    - The command must start a line (leading whitespace allowed).
    - ``--dry-run``, ``--allow-missing-stack``, and ``--resume`` flags are
      recognised in any order following the command.
    - Case-insensitive matching: ``/Assembly --Dry-Run`` is accepted.
    - Typo variants (``--dry_run``, ``--dryrun``,
      ``--allow_missing_stack``) are accepted and treated as the canonical flag.
    - Only the *first* matching line wins.  Additional commands in the same
      body are ignored.
    """
    if not body:
        return None
    match = _COMMAND_RE.search(body)
    if not match:
        return None
    command = match.group(1).lower()
    if command not in ASSEMBLY_COMMANDS:  # defensive — re cannot mis-fire
        return None
    rest = (match.group("rest") or "").lower()
    raw_flags = [token for token in rest.split() if token.startswith("--")]

    # Normalize each flag, rejecting truly unknown ones
    normalized_flags: set[str] = set()
    for f in raw_flags:
        norm = _normalize_flag(f)
        if norm not in _KNOWN_FLAGS:
            # Unknown flag — reject the entire command to prevent silent misuse
            return None
        normalized_flags.add(norm)

    return AssemblyCommand(
        command=command,
        dry_run="dry-run" in normalized_flags,
        allow_missing_stack="allow-missing-stack" in normalized_flags,
        resume="resume" in normalized_flags,
    )
