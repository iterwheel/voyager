from __future__ import annotations

import hashlib
import hmac


def github_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def match_signature(body: bytes, signature: str | None, secrets: dict[str, str]) -> str | None:
    if not signature:
        return None
    for slug, secret in secrets.items():
        if not secret:
            continue
        expected = github_signature(secret, body)
        if hmac.compare_digest(expected, signature):
            return slug
    return None
