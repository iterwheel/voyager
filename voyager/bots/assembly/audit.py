"""Assembly bot audit manifests for private OMP execution traces.

GitHub comments may expose an audit ID and lookup hint. The manifest itself
stays on the operator machine under the Assembly state tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .constants import ASSEMBLY_AUDIT_DIR_DEFAULT, ASSEMBLY_AUDIT_DIR_ENV, ASSEMBLY_AUDIT_SOP

_SCHEMA_VERSION = 1
_SESSION_SCHEMA_VERSION = 1
_SESSION_TTL_DAYS = 7
_AUDIT_ID_RE = re.compile(r"^asmb-[0-9a-f]{16}$")
_TOKEN_VALUE_RE = re.compile(r"\bgh[opsru]_[A-Za-z0-9_]+\b")
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|private[_-]?key|api[_-]?key|credential)",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def generate_audit_id(*, delivery_id: str, repository: str, issue_number: int) -> str:
    """Return a stable non-secret ID for a single webhook-triggered run."""
    seed = f"{delivery_id}|{repository}|{issue_number}".encode()
    return f"asmb-{hashlib.sha256(seed).hexdigest()[:16]}"


def is_audit_id(value: str) -> bool:
    return bool(_AUDIT_ID_RE.fullmatch(value))


def audit_storage_root() -> Path:
    raw = os.environ.get(ASSEMBLY_AUDIT_DIR_ENV) or ASSEMBLY_AUDIT_DIR_DEFAULT
    return Path(raw).expanduser()


def audit_manifest_path(
    *,
    audit_id: str,
    repository: str,
    issue_number: int,
    root: Path | None = None,
) -> Path:
    owner, _, repo = repository.partition("/")
    if not owner or not repo:
        owner, repo = "unknown", repository or "unknown"
    return (root or audit_storage_root()) / owner / repo / str(issue_number) / f"{audit_id}.json"


def _session_storage_key(
    *,
    repository: str,
    issue_number: int,
    branch_name: str,
    pr_number: int,
) -> str:
    seed = f"{repository}|{issue_number}|{branch_name}|{pr_number}".encode()
    return hashlib.sha256(seed).hexdigest()[:16]


def session_metadata_path(
    *,
    repository: str,
    issue_number: int,
    branch_name: str,
    pr_number: int,
    root: Path | None = None,
) -> Path:
    owner, _, repo = repository.partition("/")
    if not owner or not repo:
        owner, repo = "unknown", repository or "unknown"
    key = _session_storage_key(
        repository=repository,
        issue_number=issue_number,
        branch_name=branch_name,
        pr_number=pr_number,
    )
    return (
        (root or audit_storage_root())
        / owner
        / repo
        / str(issue_number)
        / "sessions"
        / f"{key}.json"
    )


def lookup_hint(audit_id: str, repository: str, issue_number: int) -> str:
    path = audit_manifest_path(
        audit_id=audit_id,
        repository=repository,
        issue_number=issue_number,
        root=_display_audit_root(),
    )
    return f"Audit ID `{audit_id}`. Private lookup: `{path}`. SOP: `rules/{ASSEMBLY_AUDIT_SOP}`."


def _display_audit_root() -> Path:
    raw = os.environ.get(ASSEMBLY_AUDIT_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path(ASSEMBLY_AUDIT_DIR_DEFAULT)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _TOKEN_VALUE_RE.sub("[redacted]", value)
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY_RE.search(key_text):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = _redact(item)
        return redacted
    return value


@dataclass(frozen=True)
class AssemblyAuditManifest:
    audit_id: str
    repository: str
    issue_number: int
    delivery_id: str
    backend_name: str
    branch_name: str | None = None
    pr_number: int | None = None
    checkout_dir: str | None = None
    omp_session_jsonl_path: str | None = None
    exported_html_path: str | None = None
    verification_commands: tuple[str, ...] = ()
    adapter_status: str | None = None
    adapter_summary: str | None = None
    commit_shas: tuple[str, ...] = ()
    session_mode: str = "fresh"
    resume_requested: bool = False
    resume_fallback_reason: str | None = None
    session_id: str | None = None
    expected_head_sha: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    schema_version: int = _SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verification_commands"] = list(self.verification_commands)
        data["commit_shas"] = list(self.commit_shas)
        redacted = _redact(data)
        return redacted if isinstance(redacted, dict) else {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssemblyAuditManifest:
        return cls(
            audit_id=str(data.get("audit_id") or ""),
            repository=str(data.get("repository") or ""),
            issue_number=int(data.get("issue_number") or 0),
            delivery_id=str(data.get("delivery_id") or ""),
            backend_name=str(data.get("backend_name") or ""),
            branch_name=data.get("branch_name"),
            pr_number=data.get("pr_number"),
            checkout_dir=data.get("checkout_dir"),
            omp_session_jsonl_path=data.get("omp_session_jsonl_path"),
            exported_html_path=data.get("exported_html_path"),
            verification_commands=tuple(data.get("verification_commands") or ()),
            adapter_status=data.get("adapter_status"),
            adapter_summary=data.get("adapter_summary"),
            commit_shas=tuple(data.get("commit_shas") or ()),
            session_mode=str(data.get("session_mode") or "fresh"),
            resume_requested=bool(data.get("resume_requested")),
            resume_fallback_reason=data.get("resume_fallback_reason"),
            session_id=data.get("session_id"),
            expected_head_sha=data.get("expected_head_sha"),
            created_at=str(data.get("created_at") or ""),
            completed_at=data.get("completed_at"),
            schema_version=int(data.get("schema_version") or _SCHEMA_VERSION),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class AssemblySessionMetadata:
    repository: str
    issue_number: int
    branch_name: str
    pr_number: int
    head_sha: str
    backend_name: str
    session_id: str
    audit_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    expires_at: str = field(
        default_factory=lambda: (datetime.now(UTC) + timedelta(days=_SESSION_TTL_DAYS)).isoformat()
    )
    schema_version: int = _SESSION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        redacted = _redact(data)
        return redacted if isinstance(redacted, dict) else {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssemblySessionMetadata:
        return cls(
            repository=str(data.get("repository") or ""),
            issue_number=int(data.get("issue_number") or 0),
            branch_name=str(data.get("branch_name") or ""),
            pr_number=int(data.get("pr_number") or 0),
            head_sha=str(data.get("head_sha") or ""),
            backend_name=str(data.get("backend_name") or ""),
            session_id=str(data.get("session_id") or ""),
            audit_id=data.get("audit_id"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            expires_at=str(data.get("expires_at") or ""),
            schema_version=int(data.get("schema_version") or _SESSION_SCHEMA_VERSION),
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return True
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        current = now or datetime.now(UTC)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= current


def write_audit_manifest(manifest: AssemblyAuditManifest) -> Path:
    path = audit_manifest_path(
        audit_id=manifest.audit_id,
        repository=manifest.repository,
        issue_number=manifest.issue_number,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return path


def load_audit_manifest(path: Path) -> AssemblyAuditManifest:
    return AssemblyAuditManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def find_audit_manifest(audit_id: str, *, root: Path | None = None) -> Path | None:
    storage = root or audit_storage_root()
    matches = sorted(storage.glob(f"*/*/*/{audit_id}.json"))
    return matches[0] if matches else None


def write_session_metadata(metadata: AssemblySessionMetadata) -> Path:
    path = session_metadata_path(
        repository=metadata.repository,
        issue_number=metadata.issue_number,
        branch_name=metadata.branch_name,
        pr_number=metadata.pr_number,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(metadata.to_dict(), indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return path


def load_session_metadata(path: Path) -> AssemblySessionMetadata:
    return AssemblySessionMetadata.from_dict(json.loads(path.read_text(encoding="utf-8")))


def find_session_metadata(
    *,
    repository: str,
    issue_number: int,
    branch_name: str,
    pr_number: int,
    root: Path | None = None,
) -> Path | None:
    path = session_metadata_path(
        repository=repository,
        issue_number=issue_number,
        branch_name=branch_name,
        pr_number=pr_number,
        root=root,
    )
    return path if path.exists() else None
