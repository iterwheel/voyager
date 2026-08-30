"""Scoped environments for Assembly's untrusted execution boundary."""

from __future__ import annotations

import os
from pathlib import Path

_UNTRUSTED_ENV_KEYS = (
    "COLORTERM",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "TZ",
    "VIRTUAL_ENV",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
)
_GIT_TRANSPORT_ENV_KEYS = (
    "ALL_PROXY",
    "CURL_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "GIT_SSL_CERT",
    "GIT_SSL_KEY",
    "GIT_SSL_NO_VERIFY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)
_APPROVED_GIT_HTTP_OPTIONS = frozenset(
    {
        "followredirects",
        "lowspeedlimit",
        "lowspeedtime",
        "proxy",
        "sslcert",
        "sslkey",
        "sslcainfo",
        "sslverify",
        "version",
    }
)
_MAX_GIT_CONFIG_PAIRS = 32


def untrusted_subprocess_env() -> dict[str, str]:
    """Return the minimal ambient env for model and model-written code."""
    env = {name: os.environ[name] for name in _UNTRUSTED_ENV_KEYS if name in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def scoped_git_env(*, token: str | None = None, askpass: Path | None = None) -> dict[str, str]:
    """Return a Git-only env with approved transport settings and optional auth."""
    env = untrusted_subprocess_env()
    env.update(_git_transport_env())
    if token and askpass is not None:
        env["GIT_ASKPASS"] = str(askpass)
        env["ASSEMBLY_GITHUB_TOKEN"] = token
    return env


def _git_transport_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in _GIT_TRANSPORT_ENV_KEYS if name in os.environ}
    try:
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    output_index = 0
    for source_index in range(min(max(count, 0), _MAX_GIT_CONFIG_PAIRS)):
        key = os.environ.get(f"GIT_CONFIG_KEY_{source_index}", "")
        value = os.environ.get(f"GIT_CONFIG_VALUE_{source_index}")
        option = key.lower().rsplit(".", 1)[-1]
        if not key.lower().startswith("http.") or option not in _APPROVED_GIT_HTTP_OPTIONS:
            continue
        if value is None:
            continue
        env[f"GIT_CONFIG_KEY_{output_index}"] = key
        env[f"GIT_CONFIG_VALUE_{output_index}"] = value
        output_index += 1
    if output_index:
        env["GIT_CONFIG_COUNT"] = str(output_index)
    return env
