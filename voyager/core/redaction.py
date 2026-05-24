from __future__ import annotations

import re
from typing import Any

_GITHUB_TOKEN_RE = re.compile(r"\b(?:gh[opsru]_|github_pat_)[A-Za-z0-9_]+\b")
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
