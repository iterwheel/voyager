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
_URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)"
    r"[A-Z0-9_]*)\s*=\s*[^\s&]+"
)
_API_KEY_SHAPED_RE = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{6,}(?![A-Za-z0-9_-])")


def _redact_secret_assignment(match: re.Match[str]) -> str:
    key = match.group(1)
    if key.lower() == "token":
        return f"{key}=[redacted]"
    return "[redacted]"


def sanitize_public_text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    text = _URL_USERINFO_RE.sub(r"\1[redacted]@", text)
    text = _SECRET_ASSIGNMENT_RE.sub(_redact_secret_assignment, text)
    text = _TOKEN_QUERY_RE.sub(r"\1[redacted]", text)
    text = _GITHUB_TOKEN_RE.sub("[redacted]", text)
    text = _API_KEY_SHAPED_RE.sub("[redacted]", text)
    text = _BEARER_RE.sub(r"\1[redacted]", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
