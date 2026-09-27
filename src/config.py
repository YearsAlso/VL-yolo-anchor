"""Central runtime configuration.

Precedence, highest first:

1. Environment variables (``VL_ANCHOR_*`` / ``VL_MODEL_*``) — how Docker injects
   per-deployment values.
2. The mounted ``config/overrides.yaml`` — written by the settings UI.
3. The mounted ``config/global.yaml`` (located via ``VL_ANCHOR_CONFIG_DIR``).
4. The built-in defaults below.

This lets the same image run as a local desktop backend (bind 127.0.0.1, stub
model) or a containerized server (bind 0.0.0.0, remote VL endpoint) purely via
configuration, without code changes.

Secrets (model ``api_key``, the API ``auth_token``) are never read from the YAML
files. Each resolves through the environment first — including the Docker/K8s
``*_FILE`` indirection, so a deployment can keep the value out of ``.env``, the
image, and ``docker inspect`` output — and then through the encrypted store in
:mod:`src.utils.secrets`. Both mechanisms live there; this module only decides
where they sit in the precedence order.

The layering helpers here are the single source of truth for *how* a value is
resolved: :mod:`src.core.config_store` calls :func:`resolve_model_field` to
attribute each effective value to the layer it came from, so the settings UI can
never disagree with the runtime.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.utils.secrets import SECRETS_FILENAME, SecretStore, read_secret
from src.utils.yaml_utils import load_yaml

logger = logging.getLogger(__name__)

ConfigSource = Literal["env", "overrides", "yaml", "secrets", "default"]
"""Which layer supplied an effective configuration value."""

Role = Literal["llm", "vl"]
"""The two model roles: PlanAgent (text) and AnnotateAgent (vision)."""

MODEL_FIELD_ENV_SUFFIX: dict[str, str] = {
    "provider": "PROVIDER",
    "base_url": "BASE_URL",
    "model": "NAME",
    "api_key": "API_KEY",
}
"""Environment-variable suffix per model field (``model`` maps to ``NAME``)."""

ROLE_ENV_PREFIX: dict[str, str] = {"llm": "VL_LLM", "vl": "VL_VL"}
"""Per-role environment-variable prefixes."""

SHARED_ENV_PREFIX = "VL_MODEL"
"""Prefix of the environment variables shared by both roles."""

OVERRIDES_FILENAME = "overrides.yaml"
"""File the settings UI writes; layered between the environment and global.yaml."""

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


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable.

    Args:
        name: Variable name.
        default: Fallback when unset or empty.

    Returns:
        ``True`` for ``1/true/yes/on`` (case-insensitive), ``False`` for
        ``0/false/no/off``, and ``default`` when unset.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


def secret_store_path(config_dir: Path) -> Path:
    """Return the encrypted-store path for a config directory.

    Args:
        config_dir: Directory holding ``global.yaml`` / ``overrides.yaml``.

    Returns:
        Path to ``config/secrets.db`` (which need not exist).
    """
    return config_dir / SECRETS_FILENAME


def model_secret_names(role: Role | None) -> list[str]:
    """List the environment variable names that can supply a model ``api_key``.

    The names double as keys in the encrypted store, so ``"which layer won"`` is
    readable directly from the name.

    Args:
        role: ``"llm"`` / ``"vl"``, or ``None`` for the shared layer.

    Returns:
        Names in descending priority: the role-specific one first (when given),
        then the shared one.
    """
    suffix = MODEL_FIELD_ENV_SUFFIX["api_key"]
    names = [f"{ROLE_ENV_PREFIX[role]}_{suffix}"] if role is not None else []
    names.append(f"{SHARED_ENV_PREFIX}_{suffix}")
    return names


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML mapping, tolerating a missing or malformed file.

    Args:
        path: YAML file path.

    Returns:
        Parsed mapping, or an empty dict when the file is absent or unreadable.
    """
    if not path.is_file():
        return {}
    try:
        return load_yaml(path)
    except Exception:  # noqa: BLE001 - a bad config must not crash import
        logger.warning("Ignoring unreadable config file: %s", path)
        return {}


def load_config_files(config_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the raw ``global.yaml`` and ``overrides.yaml`` mappings.

    Args:
        config_dir: Directory holding both files.

    Returns:
        ``(global_yaml, overrides_yaml)``; either is empty when absent.
    """
    return _load_yaml_file(config_dir / "global.yaml"), _load_yaml_file(config_dir / OVERRIDES_FILENAME)


def _yaml_field(section: dict[str, Any], field: str) -> str:
    """Read a model field from a YAML section.

    Args:
        section: ``model`` / ``model.<role>`` mapping.
        field: Field name (``provider`` / ``base_url`` / ``model`` / ``api_key``).

    Returns:
        The value as a string, or ``""`` when unset. For ``model`` the legacy
        scaffold-era alias ``vl_model_name`` is accepted as a fallback.
    """
    value = section.get(field)
    if not value and field == "model":
        value = section.get("vl_model_name")
    return str(value) if value else ""


def resolve_model_field(
    role: Role | None,
    field: str,
    *,
    yaml_model: dict[str, Any],
    overrides_model: dict[str, Any],
    default: str = "",
) -> tuple[str, ConfigSource, str]:
    """Resolve one non-secret model field through the layered precedence.

    Order, highest first::

        role env  >  role overrides  >  role yaml
                  >  shared env  >  shared overrides  >  shared yaml  >  default

    A role that sets nothing inherits the shared layer, which is what lets a
    single-endpoint deployment avoid per-role configuration. Empty strings count
    as unset at every layer, matching :func:`_env` and :func:`_env_path`.

    Args:
        role: ``"llm"`` / ``"vl"``, or ``None`` for the shared ``model:`` layer.
        field: Field name (``provider`` / ``base_url`` / ``model``).
        yaml_model: ``model`` section of ``global.yaml``.
        overrides_model: ``model`` section of ``overrides.yaml``.
        default: Value to use when no layer sets the field.

    Returns:
        ``(value, source, source_var)``. ``source_var`` is the environment
        variable that locked the value, or ``""`` for non-env layers.
    """
    suffix = MODEL_FIELD_ENV_SUFFIX[field]
    if role is not None:
        role_env = f"{ROLE_ENV_PREFIX[role]}_{suffix}"
        if os.environ.get(role_env):
            return os.environ[role_env], "env", role_env
        role_overrides = overrides_model.get(role)
        if isinstance(role_overrides, dict):
            value = _yaml_field(role_overrides, field)
            if value:
                return value, "overrides", ""
        role_yaml = yaml_model.get(role)
        if isinstance(role_yaml, dict):
            value = _yaml_field(role_yaml, field)
            if value:
                return value, "yaml", ""

    shared_env = f"{SHARED_ENV_PREFIX}_{suffix}"
    if os.environ.get(shared_env):
        return os.environ[shared_env], "env", shared_env
    value = _yaml_field(overrides_model, field)
    if value:
        return value, "overrides", ""
    value = _yaml_field(yaml_model, field)
    if value:
        return value, "yaml", ""
    return default, "default", ""


def resolve_model_secret_detail(
    role: Role | None, *, store: SecretStore | None = None
) -> tuple[str, ConfigSource, str]:
    """Resolve a model ``api_key`` and report which layer supplied it.

    YAML is deliberately excluded: ``config/global.yaml`` is a tracked file, so
    honouring a key stored there invites committing a live credential. A
    plaintext key found in YAML is reported by :func:`find_plaintext_secrets`
    instead of being used.

    Args:
        role: ``"llm"`` / ``"vl"``, or ``None`` for the shared layer.
        store: Encrypted store consulted when no environment variable provides a
            key. ``None`` — or a store with no usable master key — means the
            environment is the only source.

    Returns:
        ``(value, source, source_var)``. ``source`` is ``"env"`` for an
        environment hit (including ``*_FILE``) and ``"secrets"`` for a store hit;
        ``source_var`` is the variable or key name that won, or the empty string
        when unconfigured.
    """
    names = model_secret_names(role)
    for name in names:
        value, source_var = read_secret(name)
        if value:
            return value, "env", source_var
    if store is not None:
        for name in names:
            value = store.get(name)
            if value:
                return value, "secrets", name
    return "", "default", ""


def resolve_model_secret(role: Role | None, *, store: SecretStore | None = None) -> tuple[str, str]:
    """Resolve a model ``api_key``, returning only the value and its source name.

    Args:
        role: ``"llm"`` / ``"vl"``, or ``None`` for the shared layer.
        store: Optional encrypted store, as in :func:`resolve_model_secret_detail`.

    Returns:
        ``(value, source_var)``; both empty when unconfigured.
    """
    value, _source, source_var = resolve_model_secret_detail(role, store=store)
    return value, source_var


def find_plaintext_secrets(config_dir: Path) -> list[str]:
    """List config files that hold a plaintext secret and are therefore ignored.

    Only ``api_key`` entries are inspected. ``global.yaml`` and
    ``overrides.yaml`` are both repo-adjacent files, so a key written into
    either is one ``git add`` away from leaking.

    Args:
        config_dir: Directory holding ``global.yaml`` / ``overrides.yaml``.

    Returns:
        Sorted file names (not full paths) containing a non-empty ``api_key``.
    """
    flagged: list[str] = []
    for filename in ("global.yaml", OVERRIDES_FILENAME):
        data = _load_yaml_file(config_dir / filename)
        model = data.get("model")
        if not isinstance(model, dict):
            continue
        sections: list[dict[str, Any]] = [model]
        sections.extend(s for s in model.values() if isinstance(s, dict))
        if any(_yaml_field(section, "api_key") for section in sections):
            flagged.append(filename)
    return sorted(flagged)


class ModelSettings(BaseModel):
    """VL / LLM backend configuration.

    Attributes:
        provider: ``"stub"`` for the offline deterministic stub, ``"remote"``
            for an OpenAI-compatible HTTP endpoint.
        base_url: Remote endpoint root, e.g. ``http://host:8000/v1``.
        api_key: Bearer token for the remote endpoint; never logged, never
            written to disk in plaintext, sourced from the environment or the
            encrypted store — never from YAML.
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


class InferenceSettings(BaseModel):
    """Global defaults for annotation/inspection thresholds.

    Per-task values are written into each task's ``plan.yaml`` by PlanAgent;
    these are the platform-wide fallbacks shown read-only in the settings UI.

    Attributes:
        batch_size: Images per inference batch.
        conf_threshold: Minimum detection confidence to keep.
        min_defect_pixels: Minimum defect area in pixels.
        enable_cpu_fallback: Allow running without a GPU.
    """

    batch_size: int = 1
    conf_threshold: float = 0.25
    min_defect_pixels: int = 16
    enable_cpu_fallback: bool = True


class Settings(BaseModel):
    """Aggregated runtime settings.

    Attributes:
        auth_token: Bearer token required by the API; empty disables auth.
        auth_token_source: Variable name that supplied ``auth_token``, for the
            diagnostics report — ``"VL_ANCHOR_AUTH_TOKEN"`` also covers a hit in
            the encrypted store. Never the token itself.
    """

    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Field(default_factory=lambda: Path("tasks").parent)
    tasks_root: Path = Field(default_factory=lambda: Path("tasks"))
    logs_dir: Path = Field(default_factory=lambda: Path("logs"))
    config_dir: Path = _DEFAULT_CONFIG_DIR
    prompts_dir: Path = Field(default_factory=lambda: Path("prompts"))
    cors_origins: list[str] = Field(default_factory=lambda: list(_DEFAULT_CORS_ORIGINS))
    auth_token: str = ""
    auth_token_source: str = ""
    llm: ModelSettings = Field(default_factory=ModelSettings)
    vl: ModelSettings = Field(default_factory=ModelSettings)
    inference: InferenceSettings = Field(default_factory=InferenceSettings)
    db_enabled: bool = True
    db_path: Path = Field(default_factory=lambda: Path("index.db"))


def _section(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a nested mapping section, or an empty dict when absent.

    Args:
        parent: Containing mapping.
        key: Section name.

    Returns:
        The section when it is a mapping, else ``{}``.
    """
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _role_model_settings(
    role: Role,
    base: ModelSettings,
    yaml_model: dict[str, Any],
    overrides_model: dict[str, Any],
    store: SecretStore | None = None,
) -> ModelSettings:
    """Layer a role-specific model config over the shared base.

    Args:
        role: ``"llm"`` or ``"vl"``.
        base: Shared settings resolved from the ``model:`` section.
        yaml_model: ``model`` section of ``global.yaml``.
        overrides_model: ``model`` section of ``overrides.yaml``.
        store: Optional encrypted store used as the last resort for ``api_key``.

    Returns:
        Resolved :class:`ModelSettings` for that role.
    """

    def pick(field: str, fallback: str) -> str:
        return resolve_model_field(
            role, field, yaml_model=yaml_model, overrides_model=overrides_model, default=fallback
        )[0]

    api_key, _ = resolve_model_secret(role, store=store)
    return base.model_copy(
        update={
            "provider": pick("provider", base.provider),
            "base_url": pick("base_url", base.base_url),
            "model": pick("model", base.model),
            "api_key": api_key,
        }
    )


def load_settings() -> Settings:
    """Build the effective :class:`Settings` from env, YAML, and defaults.

    Returns:
        Resolved settings with environment overrides applied on top of any
        mounted ``overrides.yaml`` and ``global.yaml`` values.
    """
    config_dir = _env_path("VL_ANCHOR_CONFIG_DIR", _DEFAULT_CONFIG_DIR)
    yaml, overrides = load_config_files(config_dir)
    server_yaml = _section(yaml, "server")
    paths_yaml = _section(yaml, "paths")
    model_yaml = _section(yaml, "model")
    overrides_model = _section(overrides, "model")

    if plaintext := find_plaintext_secrets(config_dir):
        logger.warning(
            "Ignoring api_key found in %s: model keys come from the environment "
            "(VL_MODEL_API_KEY / VL_LLM_API_KEY / VL_VL_API_KEY, including the *_FILE variants) "
            "or from the encrypted store config/%s — never from YAML.",
            ", ".join(plaintext),
            SECRETS_FILENAME,
        )

    data_dir = _env_path("VL_ANCHOR_DATA_DIR", Path(str(paths_yaml.get("data_dir", "."))))
    default_tasks = data_dir / str(paths_yaml.get("tasks_root", "tasks"))
    tasks_root = _env_path("VL_ANCHOR_TASKS_ROOT", default_tasks)
    prompts_dir = _env_path("VL_ANCHOR_PROMPTS_DIR", config_dir.parent / str(paths_yaml.get("prompts_dir", "prompts")))
    logs_dir = _env_path("VL_ANCHOR_LOGS_DIR", data_dir / "logs")

    cors_raw = os.environ.get("VL_ANCHOR_CORS_ORIGINS")
    cors_origins = (
        [o.strip() for o in cors_raw.split(",") if o.strip()] if cors_raw else list(_DEFAULT_CORS_ORIGINS)
    )

    database_yaml = _section(yaml, "database")
    db_enabled = _env_bool("VL_ANCHOR_DB_ENABLED", bool(database_yaml.get("enabled", True)))
    db_default = data_dir / str(database_yaml.get("path") or "index.db")
    db_path = _env_path("VL_ANCHOR_DB_PATH", db_default)

    # The encrypted store is separate from the (disposable) index DB, and is
    # told where the index lives so it can refuse a path collision.
    store = SecretStore(secret_store_path(config_dir), index_db_path=db_path)

    shared_key, _shared_source = resolve_model_secret(None, store=store)
    base_model = ModelSettings(
        provider=resolve_model_field(
            None, "provider", yaml_model=model_yaml, overrides_model=overrides_model, default="stub"
        )[0],
        base_url=resolve_model_field(None, "base_url", yaml_model=model_yaml, overrides_model=overrides_model)[0],
        model=resolve_model_field(None, "model", yaml_model=model_yaml, overrides_model=overrides_model)[0],
        api_key=shared_key,
        timeout_s=float(model_yaml.get("timeout_s", 120.0)),
        max_retries=int(model_yaml.get("max_retries", 2)),
        max_new_tokens=int(model_yaml.get("max_new_tokens", 2048)),
    )
    # Plan (text LLM) and Annotate (VL) can point at different providers/endpoints.
    # Both default to the shared ``model:`` block above unless overridden.
    llm = _role_model_settings("llm", base_model, model_yaml, overrides_model, store)
    vl = _role_model_settings("vl", base_model, model_yaml, overrides_model, store)

    inference_yaml = _section(yaml, "inference")
    inference = InferenceSettings(
        batch_size=int(_env("VL_INFERENCE_BATCH_SIZE", str(inference_yaml.get("batch_size", 1)))),
        conf_threshold=float(_env("VL_INFERENCE_CONF_THRESHOLD", str(inference_yaml.get("conf_threshold", 0.25)))),
        min_defect_pixels=int(
            _env("VL_INFERENCE_MIN_DEFECT_PIXELS", str(inference_yaml.get("min_defect_pixels", 16)))
        ),
        enable_cpu_fallback=_env_bool(
            "VL_INFERENCE_CPU_FALLBACK", bool(inference_yaml.get("enable_cpu_fallback", True))
        ),
    )

    database_yaml = _section(yaml, "database")
    db_enabled = _env_bool("VL_ANCHOR_DB_ENABLED", bool(database_yaml.get("enabled", True)))
    db_default = data_dir / str(database_yaml.get("path") or "index.db")
    db_path = _env_path("VL_ANCHOR_DB_PATH", db_default)

    auth_token, auth_source = read_secret("VL_ANCHOR_AUTH_TOKEN")
    if not auth_token:
        auth_token = store.get("VL_ANCHOR_AUTH_TOKEN")
        auth_source = "VL_ANCHOR_AUTH_TOKEN" if auth_token else ""

    return Settings(
        host=_env("VL_ANCHOR_HOST", str(server_yaml.get("host", "127.0.0.1"))),
        port=int(_env("VL_ANCHOR_PORT", str(server_yaml.get("port", 8765)))),
        data_dir=data_dir,
        tasks_root=tasks_root,
        logs_dir=logs_dir,
        config_dir=config_dir,
        prompts_dir=prompts_dir,
        cors_origins=cors_origins,
        auth_token=auth_token,
        auth_token_source=auth_source,
        llm=llm,
        vl=vl,
        inference=inference,
        db_enabled=db_enabled,
        db_path=db_path,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`.

    Returns:
        Lazily loaded, cached settings.
    """
    return load_settings()
