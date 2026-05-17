from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

_VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "max"})

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
class Profile:
    """LLM investigator profile — a named bag of params for one verdict-investigation flavor.

    Loaded from ``[profiles.<name>]`` TOML tables. Voyager's current policy
    documents four template profiles: pro / pro_max / flash / flash_no_thinking.
    The investigator wiring looks up profiles by name from ``cfg.profiles`` and
    uses them to construct a ``DeepSeekInvestigator``.
    """

    name: str
    model: str
    thinking: bool
    reasoning_effort: str | None
    max_diff_chars: int
    min_confidence: float


@dataclass(frozen=True, kw_only=True)
class VoyagerConfig:
    """Top-level voyager configuration.

    Marked ``kw_only=True`` so future optional fields can be added without
    breaking existing instantiations on field-order grounds. All current
    callers already use keyword arguments.
    """

    apps: dict[str, AppConfig]
    work_dir: Path
    profiles: dict[str, Profile]
    default_profile: str | None
    deepseek_api_key: str | None = None


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


def _parse_profile(name: str, item: dict[str, Any]) -> Profile:
    if not isinstance(item, dict):
        raise ValueError(
            f"Profile {name!r}: must be a TOML table (e.g., '[profiles.{name}]'), "
            f"got {type(item).__name__}: {item!r}. "
            "A scalar value under [profiles] is most likely a schema typo — "
            "use '[profiles.<name>]' to define a profile table."
        )
    model_raw = item.get("model")
    if not isinstance(model_raw, str):
        raise ValueError(
            f"Profile {name!r}: 'model' must be a TOML string, got "
            f"{type(model_raw).__name__}: {model_raw!r}"
        )
    model = model_raw.strip()
    if not model:
        raise ValueError(
            f"Profile {name!r}: 'model' must be a non-empty string (not whitespace-only)"
        )

    if "thinking" not in item:
        raise ValueError(
            f"Profile {name!r}: 'thinking' is required (no implicit default — make the choice explicit; "
            "note 'reasoning_effort' is only meaningful when thinking is true)"
        )
    thinking_raw = item["thinking"]
    if not isinstance(thinking_raw, bool):
        raise ValueError(
            f"Profile {name!r}: 'thinking' must be a TOML boolean (true/false), "
            f"got {type(thinking_raw).__name__}: {thinking_raw!r}. "
            "TOML strings 'true'/'false' are coerced incorrectly by Python's bool() — "
            "use bare TOML booleans without quotes."
        )
    thinking = thinking_raw

    reasoning_effort_raw = item.get("reasoning_effort")
    if reasoning_effort_raw is None:
        reasoning_effort: str | None = None
    else:
        if not isinstance(reasoning_effort_raw, str):
            raise ValueError(
                f"Profile {name!r}: 'reasoning_effort' must be a string, "
                f"got {type(reasoning_effort_raw).__name__}: {reasoning_effort_raw!r}"
            )
        if reasoning_effort_raw not in _VALID_REASONING_EFFORTS:
            raise ValueError(
                f"Profile {name!r}: 'reasoning_effort' must be one of "
                f"{sorted(_VALID_REASONING_EFFORTS)!r}, got {reasoning_effort_raw!r}"
            )
        reasoning_effort = reasoning_effort_raw

    if "max_diff_chars" in item:
        raw_value = item["max_diff_chars"]
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise ValueError(
                f"Profile {name!r}: 'max_diff_chars' must be a TOML integer, got "
                f"{type(raw_value).__name__}: {raw_value!r}"
            )
        max_diff_chars = raw_value
    else:
        max_diff_chars = 20000
    if max_diff_chars <= 0:
        raise ValueError(f"Profile {name!r}: 'max_diff_chars' must be > 0, got {max_diff_chars!r}")

    if "min_confidence" in item:
        raw_value = item["min_confidence"]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(
                f"Profile {name!r}: 'min_confidence' must be a TOML number, got "
                f"{type(raw_value).__name__}: {raw_value!r}"
            )
        min_confidence = float(raw_value)
    else:
        min_confidence = 0.78
    if not 0.0 < min_confidence <= 1.0:
        raise ValueError(
            f"Profile {name!r}: 'min_confidence' must be in (0.0, 1.0], got {min_confidence!r}"
        )

    if reasoning_effort is not None and thinking is False:
        raise ValueError(
            f"Profile {name!r}: 'reasoning_effort' is only meaningful when "
            "'thinking' is true (DeepSeek V4 silently nullifies reasoning_effort "
            "when thinking is disabled). Either set thinking=true or drop reasoning_effort."
        )

    return Profile(
        name=name,
        model=model,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        max_diff_chars=max_diff_chars,
        min_confidence=min_confidence,
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

    profiles_section = raw.get("profiles") or {}
    profiles: dict[str, Profile] = {}
    for profile_name, profile_data in profiles_section.items():
        profiles[profile_name] = _parse_profile(profile_name, profile_data)

    default_profile = voyager_section.get("default_profile")
    if default_profile is not None and default_profile not in profiles:
        raise ValueError(
            f"[voyager].default_profile is {default_profile!r} but no "
            f"[profiles.{default_profile}] section exists"
        )

    deepseek_api_key_raw = voyager_section.get("deepseek_api_key")
    if deepseek_api_key_raw is None:
        deepseek_api_key: str | None = None
    else:
        if not isinstance(deepseek_api_key_raw, str):
            raise ValueError(
                f"[voyager].deepseek_api_key must be a string, got "
                f"{type(deepseek_api_key_raw).__name__}: {deepseek_api_key_raw!r}"
            )
        # Empty / whitespace-only strings are treated as "field absent" rather
        # than as an error, matching how operators typically toggle the key
        # by clearing the value rather than deleting the line.
        deepseek_api_key = deepseek_api_key_raw.strip() or None

    # Pure factory: no os.environ mutation. Consumers that need env-over-config
    # precedence (12-factor) combine cfg.deepseek_api_key with os.environ at
    # the call site — see voyager/server.py:_get_investigator.

    return VoyagerConfig(
        apps=apps,
        work_dir=work_dir,
        profiles=profiles,
        default_profile=default_profile,
        deepseek_api_key=deepseek_api_key,
    )


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
