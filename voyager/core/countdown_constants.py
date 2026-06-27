"""Dependency-free constants for the Countdown resolve loop.

Kept separate from :mod:`voyager.core.countdown_loop` (which imports ``httpx``) so the
CLI can reference these defaults at module-load time without eager-loading the HTTP
stack on every ``vyg`` invocation — including ``vyg --help``.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_LOCK_PATH = Path.home() / ".voyager" / "locks" / "countdown-resolve-loop.lock"
DEFAULT_MAX_RESOLVES = 10
