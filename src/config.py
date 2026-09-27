"""Central runtime configuration.

Precedence, highest first:

1. Environment variables (``VL_ANCHOR_*`` / ``VL_MODEL_*``) — how Docker injects
   per-deployment values.
2. The mounted ``config/global.yaml`` (located via ``VL_ANCHOR_CONFIG_DIR``).
3. The built-in defaults below.

This lets the same image run as a local desktop backend (bind 127.0.0.1, stub
model) or a containerized server (bind 0.0.0.0, remote VL endpoint) purely via
configuration, without code changes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.utils.yaml_utils import load_yaml

_DEFAULT_CONFIG_DIR = Path("config")
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
]


def _env(name: str, default: str) -> str:
    """Read a string environment variable with a required default.

    Args:
        name: Variable name.
        default: Fallback when unset or empty.

    Returns:
        The value, or ``default``.
    """
    value = os.environ.get(name)
    return value if value else default


def _env_path(name: str, default: Path) -> Path:
    """Read a path environment variable, falling back to a default.

    Args:
        name: Variable name.
        default: Fallback path when unset or empty.

    Returns:
        Resolved :class:`Path`.
    """
    raw = os.environ.get(name)
    return Path(raw) if raw else default


class ModelSettings(BaseModel):
    """VL / LLM backend configuration.

    Attributes:
        provider: ``"stub"`` for the offline deterministic stub, ``"remote"``
            for an OpenAI-compatible HTTP endpoint.
        base_url: Remote endpoint root, e.g. ``http://host:8000/v1``.
        api_key: Bearer token for the remote endpoint; never logged.
        model: Model name passed to the endpoint.
        timeout_s: Per-request timeout.
        max_retries: Transport-level retries.
        max_new_tokens: Generation budget.
    """

    provider: str = "stub"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_s: float = 120.0
    max_retries: int = 2
    max_new_tokens: int = 2048


class Settings(BaseModel):
    """Aggregated runtime settings."""

    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Field(default_factory=lambda: Path("tasks").parent)
    tasks_root: Path = Field(default_factory=lambda: Path("tasks"))
    logs_dir: Path = Field(default_factory=lambda: Path("logs"))
    config_dir: Path = _DEFAULT_CONFIG_DIR
    prompts_dir: Path = Field(default_factory=lambda: Path("prompts"))
    cors_origins: list[str] = Field(default_factory=lambda: list(_DEFAULT_CORS_ORIGINS))
    auth_token: str = ""
    llm: ModelSettings = Field(default_factory=ModelSettings)
    vl: ModelSettings = Field(default_factory=ModelSettings)


def _read_yaml_model_section(config_dir: Path) -> dict[str, Any]:
    """Load the ``model``/``server``/``paths`` sections from global.yaml.

    Args:
        config_dir: Directory holding ``global.yaml``.

    Returns:
        Raw mapping (empty when the file is missing).
    """
    path = config_dir / "global.yaml"
    if path.is_file():
        try:
            return load_yaml(path)
        except Exception:  # noqa: BLE001 - a bad config must not crash import
            return {}
    return {}


def _overlay_model(base: ModelSettings, yaml_sec: dict[str, Any] | None, env_prefix: str) -> ModelSettings:
    """Layer a role-specific model config over a shared base.

    Precedence, lowest to highest: ``base`` (the shared ``model:`` section plus
    ``VL_MODEL_*`` env) < ``yaml_sec`` (``model.llm`` / ``model.vl``) <
    ``{env_prefix}_*`` env (``VL_LLM_*`` / ``VL_VL_*``). A role that provides
    no override simply inherits the base, so a single-endpoint deployment needs
    no per-role config.

    Args:
        base: Shared/default model settings to build on.
        yaml_sec: Optional role sub-section from ``global.yaml``.
        env_prefix: Env var prefix, e.g. ``"VL_LLM"``.

    Returns:
        The resolved :class:`ModelSettings` for that role.
    """
    provider = base.provider
    base_url = base.base_url
    api_key = base.api_key
    name = base.model
    if yaml_sec:
        provider = str(yaml_sec.get("provider", provider))
        base_url = str(yaml_sec.get("base_url", base_url))
        api_key = str(yaml_sec.get("api_key", api_key))
        role_name = yaml_sec.get("model") or yaml_sec.get("vl_model_name")
        if role_name:
            name = str(role_name)
    provider = _env(f"{env_prefix}_PROVIDER", provider)
    base_url = _env(f"{env_prefix}_BASE_URL", base_url)
    api_key = _env(f"{env_prefix}_API_KEY", api_key)
    name = _env(f"{env_prefix}_NAME", name)
    return base.model_copy(update={"provider": provider, "base_url": base_url, "api_key": api_key, "model": name})


def load_settings() -> Settings:
    """Build the effective :class:`Settings` from env, YAML, and defaults.

    Returns:
        Resolved settings with environment overrides applied on top of any
        mounted ``global.yaml`` values.
    """
    config_dir = _env_path("VL_ANCHOR_CONFIG_DIR", _DEFAULT_CONFIG_DIR)
    yaml = _read_yaml_model_section(config_dir)
    server_yaml = yaml.get("server", {}) if isinstance(yaml, dict) else {}
    paths_yaml = yaml.get("paths", {}) if isinstance(yaml, dict) else {}
    model_yaml = yaml.get("model", {}) if isinstance(yaml, dict) else {}

    data_dir = _env_path("VL_ANCHOR_DATA_DIR", Path(str(paths_yaml.get("data_dir", "."))))
    default_tasks = data_dir / str(paths_yaml.get("tasks_root", "tasks"))
    tasks_root = _env_path("VL_ANCHOR_TASKS_ROOT", default_tasks)
    prompts_dir = _env_path("VL_ANCHOR_PROMPTS_DIR", config_dir.parent / str(paths_yaml.get("prompts_dir", "prompts")))
    logs_dir = _env_path("VL_ANCHOR_LOGS_DIR", data_dir / "logs")

    cors_raw = os.environ.get("VL_ANCHOR_CORS_ORIGINS")
    cors_origins = (
        [o.strip() for o in cors_raw.split(",") if o.strip()] if cors_raw else list(_DEFAULT_CORS_ORIGINS)
    )

    provider = _env("VL_MODEL_PROVIDER", str(model_yaml.get("provider", "stub")))
    base_model = ModelSettings(
        provider=provider,
        base_url=_env("VL_MODEL_BASE_URL", str(model_yaml.get("base_url", ""))),
        api_key=_env("VL_MODEL_API_KEY", str(model_yaml.get("api_key", ""))),
        model=_env("VL_MODEL_NAME", str(model_yaml.get("vl_model_name", model_yaml.get("model", "")))),
        timeout_s=float(model_yaml.get("timeout_s", 120.0)),
        max_retries=int(model_yaml.get("max_retries", 2)),
        max_new_tokens=int(model_yaml.get("max_new_tokens", 2048)),
    )
    # Plan (text LLM) and Annotate (VL) can point at different providers/endpoints.
    # Both default to the shared ``model:`` block above unless overridden.
    llm_sec = model_yaml.get("llm") if isinstance(model_yaml.get("llm"), dict) else None
    vl_sec = model_yaml.get("vl") if isinstance(model_yaml.get("vl"), dict) else None
    llm = _overlay_model(base_model, llm_sec, "VL_LLM")
    vl = _overlay_model(base_model, vl_sec, "VL_VL")

    return Settings(
        host=_env("VL_ANCHOR_HOST", str(server_yaml.get("host", "127.0.0.1"))),
        port=int(_env("VL_ANCHOR_PORT", str(server_yaml.get("port", 8765)))),
        data_dir=data_dir,
        tasks_root=tasks_root,
        logs_dir=logs_dir,
        config_dir=config_dir,
        prompts_dir=prompts_dir,
        cors_origins=cors_origins,
        auth_token=_env("VL_ANCHOR_AUTH_TOKEN", ""),
        llm=llm,
        vl=vl,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`.

    Returns:
        Lazily loaded, cached settings.
    """
    return load_settings()
