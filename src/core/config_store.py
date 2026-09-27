"""Effective configuration as the settings UI sees it, plus guarded write-back.

Reading is the interesting half. Every value is attributed to the layer that
supplied it — environment, ``overrides.yaml``, ``global.yaml``, the encrypted
store, or a built-in default — and a value that came from the environment is
marked *locked*. The layering itself is *not* reimplemented here: this module
calls :func:`src.config.resolve_model_field` and
:func:`src.config.resolve_model_secret_detail` for each field, so the settings
page can never disagree with what the agents actually use at runtime.

Writing is narrow on purpose. Only ``provider`` / ``base_url`` / ``model`` reach
``config/overrides.yaml``; a model key goes to the encrypted store instead, and
YAML never holds a plaintext credential. Environment-locked fields are reported
back in an ``ignored`` list rather than silently dropped, because "I saved it and
nothing changed" is the failure mode worth designing against.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from src.config import (
    OVERRIDES_FILENAME,
    ConfigSource,
    Role,
    Settings,
    find_plaintext_secrets,
    load_config_files,
    resolve_model_field,
    resolve_model_secret_detail,
    secret_store_path,
)
from src.utils.secrets import SecretStore, SecretStoreState
from src.utils.yaml_utils import save_yaml_atomic

logger = logging.getLogger(__name__)

ConfigValue = str | int | float | bool | None
"""Value types a configuration field can hold. ``None`` marks a masked secret."""

IgnoredReason = Literal["env_locked", "read_only", "not_writable", "no_secret_store"]
"""Why a submitted field was not written."""

ROLES: tuple[Role, ...] = ("llm", "vl")
"""The model roles the settings UI edits."""

WRITABLE_FIELDS: tuple[str, ...] = ("provider", "base_url", "model")
"""Model fields that may be written to ``overrides.yaml``."""

_MODEL_DEFAULTS: dict[str, str] = {"provider": "stub", "base_url": "", "model": ""}
"""Built-in fallbacks, mirroring the defaults used by :func:`src.config.load_settings`."""

UNCHANGED_SENTINEL = "***"
"""Value a client may send for ``api_key`` to mean "leave it as it is"."""


class FieldView(BaseModel):
    """One configuration field plus where its value came from.

    Attributes:
        value: Effective value; always ``None`` for a secret.
        source: Layer that supplied the value.
        locked: ``True`` when an environment variable fixed the value; the UI
            must render it read-only and writes to it are refused.
        locked_by: Name of the variable doing the locking, for display.
        editable: Whether a ``PUT`` would actually change this field.
    """

    value: ConfigValue = None
    source: ConfigSource = "default"
    locked: bool = False
    locked_by: str = ""
    editable: bool = False


class RoleView(BaseModel):
    """One model role (PlanAgent's text LLM or AnnotateAgent's VL model).

    Attributes:
        role: ``"llm"`` or ``"vl"``.
        provider: Backend selector.
        base_url: Remote endpoint root.
        model: Model name.
        timeout_s: Per-request timeout, read-only (not a settings-page field).
        api_key: Source metadata only — ``value`` is always ``None``.
        has_api_key: Whether a key is configured at all.
        api_key_masked: Last four characters, ``****3f2a`` style; empty when the
            key is absent or shorter than four characters.
    """

    role: Role
    provider: FieldView
    base_url: FieldView
    model: FieldView
    timeout_s: FieldView
    api_key: FieldView
    has_api_key: bool = False
    api_key_masked: str = ""


class SecretsView(BaseModel):
    """State of the encrypted secret store.

    Attributes:
        available: Whether keys can be read from and written to the store.
        reason: Actionable Chinese explanation when ``available`` is false.
        master_key_source: Variable name that supplied the master key, never the
            key itself.
        store_path: Path of the encrypted store.
    """

    available: bool = False
    reason: str = ""
    master_key_source: str = ""
    store_path: str = ""


class StorageView(BaseModel):
    """Where the platform reads and writes.

    Attributes:
        config_writable: Whether ``config/`` can be written to at all.
        config_persistent: Whether a write to ``config/`` survives a container
            rebuild. False inside a container whose ``config/`` comes from the
            image layer — writable, but not durable.
    """

    data_dir: FieldView
    tasks_root: FieldView
    logs_dir: FieldView
    db_path: FieldView
    db_enabled: FieldView
    config_dir: str
    config_path: str
    overrides_path: str
    config_writable: bool = False
    config_persistent: bool = True


class ServerView(BaseModel):
    """HTTP server settings. Token values are never included.

    Attributes:
        host: Bind address.
        port: Bind port.
        auth_enabled: Whether a bearer token is required.
        auth_token_source: Variable name that supplied the token.
    """

    host: FieldView
    port: FieldView
    auth_enabled: bool = False
    auth_token_source: str = ""


class InferenceView(BaseModel):
    """Platform-wide inference fallbacks, shown read-only.

    Per-task values live in each task's ``plan.yaml``.
    """

    batch_size: FieldView
    conf_threshold: FieldView
    min_defect_pixels: FieldView
    enable_cpu_fallback: FieldView


class EffectiveConfig(BaseModel):
    """Complete effective configuration as served by ``GET /api/config``."""

    llm: RoleView
    vl: RoleView
    storage: StorageView
    server: ServerView
    inference: InferenceView
    secrets: SecretsView


class RolePatch(BaseModel):
    """Submitted changes for one role.

    Attributes:
        api_key: ``None`` leaves the key alone, an empty string deletes it, and
            :data:`UNCHANGED_SENTINEL` is accepted as "unchanged" so a client can
            echo back a masked value harmlessly.
    """

    provider: Literal["stub", "remote"] | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class ConfigPatch(BaseModel):
    """Submitted changes for both roles."""

    llm: RolePatch | None = None
    vl: RolePatch | None = None


class IgnoredField(BaseModel):
    """A submitted field that was not written, and why."""

    role: str
    field: str
    reason: IgnoredReason


class WriteResult(BaseModel):
    """Outcome of :meth:`ConfigStore.write`.

    Attributes:
        written: ``role -> field -> value``; a stored key is echoed as ``"***"``.
        ignored: Submitted fields that were not written.
        overrides_path: File the non-secret fields were written to.
        restart_required: Whether a backend restart is needed to apply them.
    """

    written: dict[str, dict[str, str]] = {}
    ignored: list[IgnoredField] = []
    overrides_path: str = ""
    restart_required: bool = False


def config_persistent(config_dir: Path) -> bool:
    """Report whether writes to ``config_dir`` survive a container rebuild.

    The shipped image copies ``config/`` into an image layer, so inside a
    container the directory is writable while the change is still lost the moment
    the container is recreated. A bind mount is what makes it durable, and a
    bind mount is detectable as a device-number change relative to the parent
    directory.

    Args:
        config_dir: Directory to test.

    Returns:
        ``True`` outside containers and for mounted directories; ``False`` for an
        in-container directory that belongs to the image layer.
    """
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not in_container:
        return True
    try:
        if not (config_dir.is_dir() and config_dir.parent.is_dir()):
            return False
        return config_dir.stat().st_dev != config_dir.parent.stat().st_dev
    except OSError:
        return False


def mask_secret(value: str) -> str:
    """Mask a secret, keeping only its last four characters.

    Args:
        value: Secret plaintext.

    Returns:
        ``"****3f2a"`` style string, or ``""`` for secrets shorter than four
        characters — a mask must not reveal a short secret in full.
    """
    if len(value) < 4:
        return ""
    return f"****{value[-4:]}"


class ConfigStore:
    """Read the effective configuration and write back the editable parts.

    Attributes:
        _settings: Process settings, the fallback for every field.
        _config_dir: Directory holding ``global.yaml`` and ``overrides.yaml``.
        _yaml: Raw ``global.yaml`` mapping.
        _overrides: Raw ``overrides.yaml`` mapping.
        _store: Encrypted store for model keys.
    """

    def __init__(self, settings: Settings, config_dir: Path) -> None:
        """Bind the store to a settings snapshot and config directory.

        Args:
            settings: Effective settings, as returned by
                :func:`src.config.load_settings`.
            config_dir: Directory holding the configuration files.
        """
        self._settings = settings
        self._config_dir = config_dir
        self._yaml, self._overrides = load_config_files(config_dir)
        self._model_yaml = _mapping(self._yaml, "model")
        self._overrides_model = _mapping(self._overrides, "model")
        self._store = SecretStore(secret_store_path(config_dir), index_db_path=settings.db_path)

    @property
    def store(self) -> SecretStore:
        """Return the encrypted store this instance writes keys to.

        Returns:
            The bound :class:`~src.utils.secrets.SecretStore`.
        """
        return self._store

    def overrides_path(self) -> Path:
        """Return the write-back target.

        Returns:
            Path to ``config/overrides.yaml``.
        """
        return self._config_dir / OVERRIDES_FILENAME

    def is_writable(self) -> bool:
        """Report whether the configuration directory accepts writes.

        Returns:
            ``True`` when ``overrides.yaml`` can be created or replaced.
        """
        if self._config_dir.is_dir():
            return os.access(self._config_dir, os.W_OK)
        parent = self._config_dir.parent
        return parent.is_dir() and os.access(parent, os.W_OK)

    def secrets_view(self) -> SecretsView:
        """Describe the encrypted store's availability.

        Returns:
            A :class:`SecretsView`, with an actionable reason when unavailable.
        """
        state = self._store.state()
        return SecretsView(
            available=state is SecretStoreState.OK,
            reason=self._store.describe_state(),
            master_key_source=self._store.master_key_source(),
            store_path=str(self._store.path()),
        )

    def effective(self) -> EffectiveConfig:
        """Build the effective configuration with per-field attribution.

        Read-only: creates no directory and no file, and never touches the
        network.

        Returns:
            The :class:`EffectiveConfig` served to the settings UI.
        """
        writable = self.is_writable()
        secrets_view = self.secrets_view()
        return EffectiveConfig(
            llm=self._role_view("llm", writable, secrets_view),
            vl=self._role_view("vl", writable, secrets_view),
            storage=self._storage_view(),
            server=self._server_view(),
            inference=self._inference_view(),
            secrets=secrets_view,
        )

    def validate_patch(self, patch: ConfigPatch) -> list[str]:
        """Check a patch against the resulting *merged* configuration.

        Validation runs on the merged values, not on the patch alone: submitting
        only ``provider="remote"`` is valid when ``base_url`` already comes from
        ``global.yaml``. Requiring every field in every request would force the
        UI to resend values it is explicitly forbidden from changing.

        Args:
            patch: Submitted changes.

        Returns:
            Chinese error messages; empty when the patch is acceptable.
        """
        errors: list[str] = []
        if patch.llm is None and patch.vl is None:
            return ["未提交任何字段。"]
        if not any(
            patch_role is not None and not _role_patch_is_empty(patch_role) for patch_role in (patch.llm, patch.vl)
        ):
            return ["未提交任何字段。"]
        for role in ROLES:
            role_patch: RolePatch | None = patch.llm if role == "llm" else patch.vl
            if role_patch is None:
                continue
            errors.extend(self._validate_role(role, role_patch))
        return errors

    def write(self, patch: ConfigPatch) -> WriteResult:
        """Apply a patch, writing non-secret fields and storing model keys.

        Fields fixed by an environment variable, or blocked by an unwritable
        configuration directory, are reported in ``ignored`` instead of being
        applied or silently dropped.

        Args:
            patch: Validated changes.

        Returns:
            A :class:`WriteResult` describing what was and was not written.
        """
        writable = self.is_writable()
        secrets_view = self.secrets_view()
        plaintext_before = set(find_plaintext_secrets(self._config_dir))
        written: dict[str, dict[str, str]] = {}
        ignored: list[IgnoredField] = []
        overrides = copy.deepcopy(self._overrides)
        yaml_dirty = False

        for role in ROLES:
            role_patch: RolePatch | None = patch.llm if role == "llm" else patch.vl
            if role_patch is None:
                continue
            view = self._role_view(role, writable, secrets_view)
            role_written: dict[str, str] = {}

            for field in WRITABLE_FIELDS:
                value = getattr(role_patch, field)
                if value is None:
                    continue
                if getattr(view, field).locked:
                    ignored.append(IgnoredField(role=role, field=field, reason="env_locked"))
                    continue
                if not writable:
                    ignored.append(IgnoredField(role=role, field=field, reason="not_writable"))
                    continue
                _set_override(overrides, role, field, value)
                role_written[field] = value
                yaml_dirty = True

            if role_written.get("provider") == "stub":
                # Back to the offline stub: stale endpoint settings would be
                # resurrected the moment the provider flips back to remote.
                # Keyed on what was actually written, not on the request: when
                # the environment pins provider=remote, discarding a base_url
                # the user still needs would break the working endpoint.
                for field in ("base_url", "model"):
                    if _clear_override(overrides, role, field):
                        role_written[field] = ""
                        yaml_dirty = True

            if role_patch.api_key is not None and role_patch.api_key != UNCHANGED_SENTINEL:
                blocker = _api_key_blocker(view, writable, secrets_view)
                if blocker is not None:
                    ignored.append(IgnoredField(role=role, field="api_key", reason=blocker))
                elif self._store.set(_api_key_store_name(role), role_patch.api_key):
                    role_written["api_key"] = UNCHANGED_SENTINEL
                else:
                    ignored.append(IgnoredField(role=role, field="api_key", reason="no_secret_store"))

            if role_written:
                written[role] = role_written

        if yaml_dirty and not self._write_overrides(overrides, plaintext_before):
            # The directory passed the access check but the write failed anyway
            # (full disk, read-only mount). Retract every claim about the YAML
            # fields; a stored key still succeeded and is reported as such.
            for failed_role, failed_fields in written.items():
                for field in list(failed_fields):
                    if field != "api_key":
                        del failed_fields[field]
                        ignored.append(IgnoredField(role=failed_role, field=field, reason="not_writable"))
            written = {name: fields for name, fields in written.items() if fields}

        return WriteResult(
            written=written,
            ignored=ignored,
            overrides_path=str(self.overrides_path()),
            restart_required=bool(written),
        )

    def _write_overrides(self, overrides: dict[str, Any], plaintext_before: set[str]) -> bool:
        """Atomically write the merged overrides file.

        Args:
            overrides: Full mapping to persist.
            plaintext_before: Files already holding a plaintext key, so a
                pre-existing user mistake is reported by ``doctor`` rather than
                blamed on this write.

        Returns:
            ``True`` on success.
        """
        try:
            save_yaml_atomic(overrides, self.overrides_path())
        except OSError as exc:
            logger.warning("Could not write %s: %s", self.overrides_path(), exc)
            return False
        # Regression guard: no code path here may introduce a plaintext key.
        if new := set(find_plaintext_secrets(self._config_dir)) - plaintext_before:
            logger.error(
                "Wrote a plaintext api_key into %s; keys must go to the encrypted store or the "
                "environment.",
                ", ".join(sorted(new)),
            )
            return False
        return True

    def _role_view(self, role: Role, writable: bool, secrets_view: SecretsView) -> RoleView:
        """Build one role's view.

        Args:
            role: ``"llm"`` or ``"vl"``.
            writable: Whether the config directory accepts writes.
            secrets_view: Pre-computed store state.

        Returns:
            The resolved :class:`RoleView`.
        """
        resolved: dict[str, FieldView] = {}
        for field in WRITABLE_FIELDS:
            value, source, locked_by = self._resolve_field(role, field)
            resolved[field] = FieldView(
                value=value,
                source=source,
                locked=source == "env",
                locked_by=locked_by,
                editable=source != "env" and writable,
            )

        timeout_raw = self._model_yaml.get("timeout_s")
        base_timeout = self._settings.llm if role == "llm" else self._settings.vl
        resolved["timeout_s"] = FieldView(
            value=float(timeout_raw) if timeout_raw else base_timeout.timeout_s,
            source="yaml" if timeout_raw else "default",
            editable=False,
        )

        key_value, key_source, key_source_var = resolve_model_secret_detail(role, store=self._store)
        return RoleView(
            role=role,
            provider=resolved["provider"],
            base_url=resolved["base_url"],
            model=resolved["model"],
            timeout_s=resolved["timeout_s"],
            api_key=FieldView(
                value=None,
                source=key_source,
                locked=key_source == "env",
                locked_by=key_source_var if key_source == "env" else "",
                editable=key_source != "env" and secrets_view.available and writable,
            ),
            has_api_key=bool(key_value),
            api_key_masked=mask_secret(key_value),
        )

    def _resolve_field(self, role: Role, field: str) -> tuple[str, ConfigSource, str]:
        """Resolve one model field, attributing it to the layer that set it.

        The role layer is resolved with the shared layer's resolved value as its
        default, so the fall-through order matches
        :func:`src.config.load_settings` exactly while each hit still reports its
        real origin.

        Args:
            role: ``"llm"`` or ``"vl"``.
            field: ``provider`` / ``base_url`` / ``model``.

        Returns:
            ``(value, source, locked_by)``.
        """
        shared_value, _shared_source, _shared_var = resolve_model_field(
            None,
            field,
            yaml_model=self._model_yaml,
            overrides_model=self._overrides_model,
            default=_MODEL_DEFAULTS[field],
        )
        return resolve_model_field(
            role,
            field,
            yaml_model=self._model_yaml,
            overrides_model=self._overrides_model,
            default=shared_value,
        )

    def _storage_view(self) -> StorageView:
        """Build the storage view.

        Returns:
            The resolved :class:`StorageView`.
        """
        settings = self._settings
        paths_yaml = _mapping(self._yaml, "paths")
        database_yaml = _mapping(self._yaml, "database")
        return StorageView(
            data_dir=_field(str(settings.data_dir), _path_source("VL_ANCHOR_DATA_DIR", "data_dir" in paths_yaml)),
            tasks_root=_field(
                str(settings.tasks_root), _path_source("VL_ANCHOR_TASKS_ROOT", "tasks_root" in paths_yaml)
            ),
            logs_dir=_field(str(settings.logs_dir), _path_source("VL_ANCHOR_LOGS_DIR", False)),
            db_path=_field(
                str(settings.db_path), _path_source("VL_ANCHOR_DB_PATH", bool(database_yaml.get("path")))
            ),
            db_enabled=_field(
                settings.db_enabled, _path_source("VL_ANCHOR_DB_ENABLED", "enabled" in database_yaml)
            ),
            config_dir=str(self._config_dir),
            config_path=str(self._config_dir / "global.yaml"),
            overrides_path=str(self.overrides_path()),
            config_writable=self.is_writable(),
            config_persistent=config_persistent(self._config_dir),
        )

    def _server_view(self) -> ServerView:
        """Build the server view, never including the token itself.

        Returns:
            The resolved :class:`ServerView`.
        """
        settings = self._settings
        server_yaml = _mapping(self._yaml, "server")
        return ServerView(
            host=_field(settings.host, _path_source("VL_ANCHOR_HOST", "host" in server_yaml)),
            port=_field(settings.port, _path_source("VL_ANCHOR_PORT", "port" in server_yaml)),
            auth_enabled=bool(settings.auth_token),
            auth_token_source=settings.auth_token_source,
        )

    def _inference_view(self) -> InferenceView:
        """Build the read-only inference view.

        Returns:
            The resolved :class:`InferenceView`.
        """
        inference = self._settings.inference
        inference_yaml = _mapping(self._yaml, "inference")

        def source_of(env_name: str, key: str) -> ConfigSource:
            return _path_source(env_name, key in inference_yaml)

        return InferenceView(
            batch_size=_field(inference.batch_size, source_of("VL_INFERENCE_BATCH_SIZE", "batch_size")),
            conf_threshold=_field(
                inference.conf_threshold, source_of("VL_INFERENCE_CONF_THRESHOLD", "conf_threshold")
            ),
            min_defect_pixels=_field(
                inference.min_defect_pixels, source_of("VL_INFERENCE_MIN_DEFECT_PIXELS", "min_defect_pixels")
            ),
            enable_cpu_fallback=_field(
                inference.enable_cpu_fallback, source_of("VL_INFERENCE_CPU_FALLBACK", "enable_cpu_fallback")
            ),
        )

    def _validate_role(self, role: Role, patch: RolePatch) -> list[str]:
        """Validate one role's submitted values against the merged result.

        Args:
            role: Role being validated.
            patch: Submitted changes for that role.

        Returns:
            Chinese error messages; empty when acceptable.
        """
        view = self._role_view(role, False, self.secrets_view())
        provider = patch.provider if patch.provider is not None else str(view.provider.value or "")
        base_url = patch.base_url if patch.base_url is not None else str(view.base_url.value or "")
        model = patch.model if patch.model is not None else str(view.model.value or "")
        if provider != "remote":
            return []

        errors: list[str] = []
        if not base_url:
            errors.append(f"{role}: provider=remote 时必须提供 base_url。")
        elif not base_url.startswith(("http://", "https://")):
            errors.append(f"{role}: base_url 必须以 http:// 或 https:// 开头。")
        if not model:
            errors.append(f"{role}: provider=remote 时必须提供 model（模型名）。")
        return errors


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a nested mapping, or an empty dict when absent or mistyped.

    Args:
        parent: Containing mapping.
        key: Section name.

    Returns:
        The section when it is a mapping, else ``{}``.
    """
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _field(value: ConfigValue, source: ConfigSource) -> FieldView:
    """Build a read-only field view.

    Args:
        value: Effective value.
        source: Layer that supplied it.

    Returns:
        A :class:`FieldView` with ``editable=False``.
    """
    return FieldView(value=value, source=source, editable=False)


def _path_source(env_name: str, in_yaml: bool) -> ConfigSource:
    """Attribute a path or scalar setting to its layer.

    Args:
        env_name: Environment variable that can override it.
        in_yaml: Whether ``global.yaml`` sets the corresponding key.

    Returns:
        ``"env"``, ``"yaml"``, or ``"default"``.
    """
    if os.environ.get(env_name):
        return "env"
    return "yaml" if in_yaml else "default"


def _role_patch_is_empty(patch: RolePatch) -> bool:
    """Report whether a role patch submits nothing.

    Args:
        patch: Submitted changes.

    Returns:
        ``True`` when every field is ``None``.
    """
    return all(getattr(patch, field) is None for field in (*WRITABLE_FIELDS, "api_key"))


def _api_key_store_name(role: Role) -> str:
    """Return the encrypted-store key name used for a role's model key.

    Args:
        role: ``"llm"`` or ``"vl"``.

    Returns:
        The role-specific environment-variable name the stored key stands in for.
    """
    return f"VL_{role.upper()}_API_KEY"


def _api_key_blocker(view: RoleView, writable: bool, secrets_view: SecretsView) -> IgnoredReason | None:
    """Report why a model key cannot be stored, if it cannot.

    Args:
        view: The role's already-resolved view.
        writable: Whether the config directory accepts writes.
        secrets_view: Pre-computed store state.

    Returns:
        The blocking reason, or ``None`` when the write may proceed.
    """
    if view.api_key.locked:
        return "env_locked"
    if not writable:
        return "not_writable"
    if not secrets_view.available:
        return "no_secret_store"
    return None


def _set_override(overrides: dict[str, Any], role: Role, field: str, value: str) -> None:
    """Set one ``model.<role>.<field>`` value in the overrides mapping.

    Args:
        overrides: Overrides mapping, mutated in place.
        role: Target role.
        field: Field name.
        value: Value to store; an empty string removes the key.
    """
    model = overrides.setdefault("model", {})
    if not isinstance(model, dict):  # pragma: no cover - guarded by load_config_files
        model = {}
        overrides["model"] = model
    section = model.setdefault(role, {})
    if not isinstance(section, dict):
        section = {}
        model[role] = section
    if value:
        section[field] = value
    else:
        section.pop(field, None)
    if not section:
        model.pop(role, None)
    if not model:
        overrides.pop("model", None)


def _clear_override(overrides: dict[str, Any], role: Role, field: str) -> bool:
    """Remove one overridden field.

    Args:
        overrides: Overrides mapping, mutated in place.
        role: Target role.
        field: Field name.

    Returns:
        ``True`` when a key was actually removed.
    """
    model = overrides.get("model")
    if not isinstance(model, dict):
        return False
    section = model.get(role)
    if not isinstance(section, dict) or field not in section:
        return False
    section.pop(field)
    if not section:
        model.pop(role, None)
    if not model:
        overrides.pop("model", None)
    return True
