from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AppConfig:
    slug: str
    app_id: str
    installation_id: str
    installations: dict[str, str]

    @property
    def webhook_secret_env(self) -> str:
        normalized = self.slug.upper().replace("-", "_")
        return f"GITHUB_WEBHOOK_SECRET_{normalized}"

    @property
    def private_key_path(self) -> Path:
        configured = os.environ.get(
            f"GITHUB_PRIVATE_KEY_PATH_{self.slug.upper().replace('-', '_')}"
        )
        if configured:
            return Path(configured).expanduser()
        return _work_dir() / "secrets" / f"{self.slug}.private-key.pem"

    def configured_installation_id_for_repository(self, repository: str | None) -> str | None:
        if not repository:
            return self.installation_id or None
        owner, _, _ = repository.partition("/")
        return self.installations.get(repository) or self.installations.get(owner) or None


def _work_dir() -> Path:
    return Path(os.environ.get("WORK_DIR", str(Path.home() / "voyager"))).expanduser()


def load_apps() -> dict[str, AppConfig]:
    path = Path(
        os.environ.get("APP_CONFIG_PATH", str(ROOT_DIR / "config" / "apps.json"))
    ).expanduser()
    raw = json.loads(path.read_text(encoding="utf-8"))
    apps: dict[str, AppConfig] = {}
    for item in raw["apps"]:
        app = AppConfig(
            slug=item["slug"],
            app_id=str(item["app_id"]),
            installation_id=str(item.get("installation_id", "")),
            installations={key: str(value) for key, value in item.get("installations", {}).items()},
        )
        apps[app.slug] = app
    return apps


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
