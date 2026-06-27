from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from voyager.bots.assembly import writeback as writeback_module
from voyager.bots.assembly.actor import evaluate_actor_authorization
from voyager.bots.assembly.adapters import (
    DryRunAdapter,
    PiOhMyPiDeepSeekAdapter,
    select_execution_adapter,
)
from voyager.bots.assembly.constants import (
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    ASSEMBLY_IMPLEMENTER_BACKEND_ENV,
    ASSEMBLY_PHASE_MODE_ENV,
    ASSEMBLY_PI_COMMAND_PATH_ENV,
    ASSEMBLY_PI_TIMEOUT_SECONDS_ENV,
    ASSEMBLY_PI_WORKDIR_ENV,
    AUTHORIZED_ACTORS_ENV,
    AUTHORIZED_ASSOCIATIONS_ENV,
)
from voyager.bots.assembly.phase import PhaseMode, PhaseName, select_phase_backend
from voyager.core.config import AssemblyConfig, BridgeConfig, VoyagerConfig, load_config
from voyager.core.writeback import dry_run_enabled


def _cfg(
    *,
    bridge: BridgeConfig | None = None,
    assembly: AssemblyConfig | None = None,
) -> VoyagerConfig:
    return VoyagerConfig(
        apps={},
        work_dir=Path("state"),
        profiles={},
        default_profile=None,
        bridge=bridge or BridgeConfig(),
        assembly=assembly or AssemblyConfig(),
    )


def _actor_payload(login: str, association: str = "NONE") -> dict[str, object]:
    return {
        "action": "created",
        "comment": {
            "body": "/assembly",
            "author_association": association,
            "user": {"login": login, "type": "User"},
        },
        "sender": {"login": login},
    }


def test_load_config_parses_bridge_and_assembly_runtime_sections(tmp_path: Path) -> None:
    path = tmp_path / "voyager.toml"
    path.write_text(
        """
[voyager]
work_dir = "~/.voyager/state"

[bridge]
dry_run = false

[bridge.allowed_repositories]
iterwheel-clearance = ["Iterwheel/Voyager", "frankyxhl/*"]
iterwheel-assembly = ["iterwheel/voyager-sandbox"]

[assembly]
execution_backend = "pi-oh-my-pi-deepseek"
phase_mode = "two-phase"
implementer_backend = "pi-oh-my-pi-deepseek"
testpilot_backend = "dry-run"
pi_command_path = "/Users/frank/.local/bin/omp"
pi_workdir = "~/.voyager/state/assembly"
pi_timeout_seconds = 900
authorized_actors = ["FrankXHL", "ryosaeba1985"]
authorized_associations = ["owner", "member"]
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.bridge.dry_run is False
    assert cfg.bridge.allowed_repositories == {
        "iterwheel-clearance": ("iterwheel/voyager", "frankyxhl/*"),
        "iterwheel-assembly": ("iterwheel/voyager-sandbox",),
    }
    assert cfg.assembly.execution_backend == "pi-oh-my-pi-deepseek"
    assert cfg.assembly.phase_mode == "two-phase"
    assert cfg.assembly.implementer_backend == "pi-oh-my-pi-deepseek"
    assert cfg.assembly.testpilot_backend == "dry-run"
    assert cfg.assembly.pi_command_path == "/Users/frank/.local/bin/omp"
    assert str(cfg.assembly.pi_workdir).endswith("/.voyager/state/assembly")
    assert cfg.assembly.pi_timeout_seconds == 900
    assert cfg.assembly.authorized_actors == ("frankxhl", "ryosaeba1985")
    assert cfg.assembly.authorized_associations == ("OWNER", "MEMBER")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[bridge]\ndry_run = "false"\n', "[bridge].dry_run"),
        (
            '[bridge.allowed_repositories]\niterwheel-clearance = "iterwheel/voyager"\n',
            "[bridge.allowed_repositories].iterwheel-clearance",
        ),
        ('[assembly]\npi_timeout_seconds = "900"\n', "[assembly].pi_timeout_seconds"),
        ('[assembly]\nauthorized_actors = "frank"\n', "[assembly].authorized_actors"),
    ],
)
def test_load_config_rejects_malformed_runtime_sections(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    path = tmp_path / "voyager.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(message)):
        load_config(path)


def test_dry_run_env_overrides_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(bridge=BridgeConfig(dry_run=False))
    monkeypatch.delenv("DRY_RUN", raising=False)

    assert dry_run_enabled(cfg) is False

    monkeypatch.setenv("DRY_RUN", "true")
    assert dry_run_enabled(cfg) is True

    monkeypatch.setenv("DRY_RUN", "false")
    assert dry_run_enabled(_cfg(bridge=BridgeConfig(dry_run=True))) is False


def test_repository_allowlist_uses_toml_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    cfg = _cfg(
        bridge=BridgeConfig(
            dry_run=False,
            allowed_repositories={"iterwheel-clearance": ("frankyxhl/*",)},
        )
    )

    assert _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance", cfg)
    assert not _repository_allowed_for_agent("iterwheel/voyager", "iterwheel-clearance", cfg)
    assert not _repository_allowed_for_agent(None, "iterwheel-clearance", cfg)


def test_repository_allowlist_env_overrides_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", "iterwheel/voyager")
    cfg = _cfg(
        bridge=BridgeConfig(
            dry_run=False,
            allowed_repositories={"iterwheel-clearance": ("frankyxhl/*",)},
        )
    )

    assert _repository_allowed_for_agent("iterwheel/voyager", "iterwheel-clearance", cfg)
    assert not _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance", cfg)


def test_repository_allowlist_empty_env_overrides_toml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", "")
    cfg = _cfg(
        bridge=BridgeConfig(
            dry_run=False,
            allowed_repositories={"iterwheel-clearance": ("frankyxhl/*",)},
        )
    )

    assert not _repository_allowed_for_agent("frankyxhl/trinity", "iterwheel-clearance", cfg)


def test_repository_allowlist_missing_both_preserves_dry_run_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voyager.server import _repository_allowed_for_agent

    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)

    assert _repository_allowed_for_agent(
        "iterwheel/voyager",
        "iterwheel-clearance",
        _cfg(bridge=BridgeConfig(dry_run=True)),
    )
    assert not _repository_allowed_for_agent(
        "iterwheel/voyager",
        "iterwheel-clearance",
        _cfg(bridge=BridgeConfig(dry_run=False)),
    )


def test_server_config_loader_retries_after_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voyager import server

    cfg = _cfg()
    calls = 0

    def load_config() -> VoyagerConfig:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary config read failure")
        return cfg

    monkeypatch.setattr(server, "_config", server._SENTINEL)
    monkeypatch.setattr("voyager.core.config.load_config", load_config)

    assert server._get_config() is None
    assert server._get_config() is cfg
    assert calls == 2


def test_actor_authorization_uses_toml_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTHORIZED_ACTORS_ENV, raising=False)
    monkeypatch.delenv(AUTHORIZED_ASSOCIATIONS_ENV, raising=False)
    cfg = _cfg(
        assembly=AssemblyConfig(
            authorized_actors=("frankxhl",),
            authorized_associations=("OWNER",),
        )
    )

    assert evaluate_actor_authorization(_actor_payload("frankxhl"), cfg).ok is True
    assert evaluate_actor_authorization(_actor_payload("owner-user", "OWNER"), cfg).ok is True
    assert evaluate_actor_authorization(_actor_payload("drive-by", "CONTRIBUTOR"), cfg).ok is False


def test_actor_authorization_env_overrides_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTHORIZED_ACTORS_ENV, "env-user")
    monkeypatch.delenv(AUTHORIZED_ASSOCIATIONS_ENV, raising=False)
    cfg = _cfg(assembly=AssemblyConfig(authorized_actors=("toml-user",)))

    assert evaluate_actor_authorization(_actor_payload("env-user"), cfg).ok is True
    assert evaluate_actor_authorization(_actor_payload("toml-user"), cfg).ok is False


def test_execution_backend_uses_toml_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ASSEMBLY_EXECUTION_BACKEND_ENV, raising=False)
    cfg = _cfg(assembly=AssemblyConfig(execution_backend=ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK))

    assert isinstance(select_execution_adapter(cfg=cfg), PiOhMyPiDeepSeekAdapter)


def test_execution_backend_env_overrides_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    cfg = _cfg(assembly=AssemblyConfig(execution_backend=ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK))

    assert isinstance(select_execution_adapter(cfg=cfg), DryRunAdapter)


def test_phase_config_uses_toml_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ASSEMBLY_PHASE_MODE_ENV, raising=False)
    monkeypatch.delenv(ASSEMBLY_IMPLEMENTER_BACKEND_ENV, raising=False)
    cfg = _cfg(
        assembly=AssemblyConfig(
            phase_mode="two-phase",
            implementer_backend=ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
        )
    )

    assert PhaseMode.from_env(cfg) == PhaseMode.TWO_PHASE
    assert (
        select_phase_backend(ASSEMBLY_BACKEND_DRY_RUN, PhaseName.IMPLEMENTER, cfg)
        == ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK
    )


def test_phase_global_env_overrides_toml_phase_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_DRY_RUN)
    monkeypatch.delenv(ASSEMBLY_IMPLEMENTER_BACKEND_ENV, raising=False)
    cfg = _cfg(
        assembly=AssemblyConfig(
            implementer_backend=ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
        )
    )

    assert (
        select_phase_backend(
            ASSEMBLY_BACKEND_DRY_RUN,
            PhaseName.IMPLEMENTER,
            cfg,
            global_backend_is_env=True,
        )
        == ASSEMBLY_BACKEND_DRY_RUN
    )


@pytest.mark.asyncio
async def test_adapter_context_uses_toml_omp_knobs_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(ASSEMBLY_PI_COMMAND_PATH_ENV, raising=False)
    monkeypatch.delenv(ASSEMBLY_PI_WORKDIR_ENV, raising=False)
    monkeypatch.delenv(ASSEMBLY_PI_TIMEOUT_SECONDS_ENV, raising=False)
    cfg = _cfg(
        assembly=AssemblyConfig(
            pi_command_path="/custom/bin/omp",
            pi_workdir=tmp_path / "assembly-work",
            pi_timeout_seconds=123,
        )
    )

    context = await writeback_module._build_adapter_context(
        AsyncMock(),
        PiOhMyPiDeepSeekAdapter(),
        "iterwheel/voyager",
        is_dry_run=True,
        cfg=cfg,
    )

    assert context.command_path == "/custom/bin/omp"
    assert context.workdir == tmp_path / "assembly-work"
    assert context.timeout_seconds == 123


@pytest.mark.asyncio
async def test_adapter_context_env_overrides_toml_omp_knobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ASSEMBLY_PI_COMMAND_PATH_ENV, "/env/bin/omp")
    monkeypatch.setenv(ASSEMBLY_PI_WORKDIR_ENV, str(tmp_path / "env-work"))
    monkeypatch.setenv(ASSEMBLY_PI_TIMEOUT_SECONDS_ENV, "456")
    cfg = _cfg(
        assembly=AssemblyConfig(
            pi_command_path="/toml/bin/omp",
            pi_workdir=tmp_path / "toml-work",
            pi_timeout_seconds=123,
        )
    )

    context = await writeback_module._build_adapter_context(
        AsyncMock(),
        PiOhMyPiDeepSeekAdapter(),
        "iterwheel/voyager",
        is_dry_run=True,
        cfg=cfg,
    )

    assert context.command_path == "/env/bin/omp"
    assert context.workdir == tmp_path / "env-work"
    assert context.timeout_seconds == 456
