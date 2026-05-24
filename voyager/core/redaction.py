from __future__ import annotations

import re
from typing import Any

_GITHUB_LEGACY_TOKEN = r"(?<![A-Za-z0-9_])gh[opru]_[A-Za-z0-9_]+(?![A-Za-z0-9_])"  # nosec B105
_GITHUB_STATELESS_INSTALLATION_TOKEN = (  # nosec B105
    r"(?<![A-Za-z0-9_.-])ghs_[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
    r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])"
)
_GITHUB_FINE_GRAINED_PAT = r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]+(?![A-Za-z0-9_])"
_GITHUB_TOKEN_RE = re.compile(
    rf"(?:{_GITHUB_LEGACY_TOKEN}|{_GITHUB_STATELESS_INSTALLATION_TOKEN}|"
    rf"{_GITHUB_FINE_GRAINED_PAT})"
)
_TOKEN_QUERY_RE = re.compile(r"(?i)(token=)[^\s&]+")
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)\S+")


def sanitize_public_text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    text = _TOKEN_QUERY_RE.sub(r"\1[redacted]", text)
    text = _GITHUB_TOKEN_RE.sub("[redacted]", text)
    text = _BEARER_RE.sub(r"\1[redacted]", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
