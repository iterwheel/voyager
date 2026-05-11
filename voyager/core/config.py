from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

_DEFAULT_SEARCH_ORDER = [
    lambda: str(Path.home() / ".voyager" / "config.toml"),
    lambda: str(Path.cwd() / "voyager.toml"),
    lambda: "/etc/voyager/config.toml",
]


@dataclass(frozen=True)
class AppConfig:
    slug: str
    app_id: str
    private_key_path: Path
    installation_id: str
    installations: dict[str, str]

    @property
    def webhook_secret_env(self) -> str:
        """Convention-only env var name derived from the slug.

        e.g. slug "iterwheel-blueprint" -> "GITHUB_WEBHOOK_SECRET_ITERWHEEL_BLUEPRINT".
        Not overridable from TOML; if a future use case needs a custom env name,
        add the override field back as a backward-compatible addition.
        """
        normalized = self.slug.upper().replace("-", "_")
        return f"GITHUB_WEBHOOK_SECRET_{normalized}"

    def configured_installation_id_for_repository(self, repository: str | None) -> str | None:
        if not repository:
            return self.installation_id or None
        owner, _, _ = repository.partition("/")
        return self.installations.get(repository) or self.installations.get(owner) or None


@dataclass(frozen=True)
class VoyagerConfig:
    apps: dict[str, AppConfig]
    work_dir: Path


def _parse_app(item: dict[str, Any]) -> AppConfig:
    slug = item.get("slug")
    if not slug:
        raise ValueError("Each [[apps]] entry must have a 'slug' field")

    app_id = item.get("app_id")
    if app_id is None:
        raise ValueError(f"app_id is required for app {slug!r}")

    raw_key_path = item.get("private_key_path")
    if not raw_key_path:
        raise ValueError(f"private_key_path is required for app {slug!r}")

    private_key_path = Path(raw_key_path).expanduser()

    installation_id = str(item.get("installation_id", ""))
    installations = {k: str(v) for k, v in (item.get("installations") or {}).items()}

    return AppConfig(
        slug=slug,
        app_id=str(app_id),
        private_key_path=private_key_path,
        installation_id=installation_id,
        installations=installations,
    )


def load_config(path: str | Path | None = None) -> VoyagerConfig:
    if path is None:
        env_path = os.environ.get("VOYAGER_CONFIG_PATH")
        if env_path:
            # Explicit override — fail fast if it doesn't exist. Codex round 2 P2
            # (PR #7): silently falling back to the default search order on a
            # missing override masks operator typos and risks loading a stale
            # config with different GitHub App IDs / private keys.
            #
            # Codex round 3 P2: expand tilde first — operators routinely set
            # paths like "~/.voyager/config.toml" and a literal ~ check would
            # always fail. Tilde expansion is consistent with private_key_path
            # and work_dir handling below.
            path = Path(env_path).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"VOYAGER_CONFIG_PATH is set but file not found: {path}")
        else:
            for candidate_fn in _DEFAULT_SEARCH_ORDER:
                candidate = candidate_fn()
                if candidate and Path(candidate).exists():
                    path = Path(candidate)
                    break
            if path is None:
                raise FileNotFoundError(
                    "No voyager config file found. Searched: "
                    + str(Path.home() / ".voyager" / "config.toml")
                    + ", ./voyager.toml, /etc/voyager/config.toml"
                )

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Voyager config file not found: {path}")

    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    voyager_section = raw.get("voyager") or {}
    raw_work_dir = voyager_section.get("work_dir", "~/.voyager/state")
    work_dir = Path(raw_work_dir).expanduser()

    apps: dict[str, AppConfig] = {}
    for item in raw.get("apps") or []:
        app = _parse_app(item)
        apps[app.slug] = app

    return VoyagerConfig(apps=apps, work_dir=work_dir)


def public_app_status(apps: dict[str, AppConfig]) -> list[dict[str, Any]]:
    status = []
    for app in apps.values():
        secret_configured = bool(os.environ.get(app.webhook_secret_env))
        key_exists = app.private_key_path.exists()
        status.append(
            {
                "slug": app.slug,
                "app_id": app.app_id,
                "installation_id_configured": bool(app.installation_id or app.installations),
                "installations": sorted(app.installations.keys()),
                "webhook_secret_configured": secret_configured,
                "private_key_exists": key_exists,
            }
        )
    return status
