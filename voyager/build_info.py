"""Runtime accessors for Voyager version + build commit metadata."""

from __future__ import annotations

from voyager import __version__ as VERSION  # noqa: N812

try:
    from voyager._build_info import BUILD_COMMIT as _GENERATED_COMMIT  # type: ignore[import-untyped]  # noqa: I001
except ImportError:
    _GENERATED_COMMIT = "dev"

BUILD_COMMIT: str = _GENERATED_COMMIT


def get_info() -> dict[str, str]:
    """Return version + build_commit as a dict (used by /healthz)."""
    return {"version": VERSION, "build_commit": BUILD_COMMIT}
