"""Tests for src/core/config_store.py: attribution, validation, and write-back.

Every test builds its own ``config/`` directory under ``tmp_path`` and points
``VL_ANCHOR_CONFIG_DIR`` at it, so nothing here can read or modify the real
``config/global.yaml`` or leave an ``overrides.yaml`` behind in the repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from src.config import find_plaintext_secrets, load_settings
from src.core.config_store import ConfigPatch, ConfigStore, RolePatch, config_persistent, mask_secret
from src.utils.secrets import MASTER_KEY_VAR, generate_master_key
from src.utils.yaml_utils import load_yaml, save_yaml_atomic

# Every variable that can influence the resolved configuration, cleared per test.
_ENV_VARS = [
    "VL_MODEL_PROVIDER",
    "VL_MODEL_BASE_URL",
    "VL_MODEL_NAME",
    "VL_MODEL_API_KEY",
    "VL_LLM_PROVIDER",
    "VL_LLM_BASE_URL",
    "VL_LLM_NAME",
    "VL_LLM_API_KEY",
    "VL_VL_PROVIDER",
    "VL_VL_BASE_URL",
    "VL_VL_NAME",
    "VL_VL_API_KEY",
    "VL_ANCHOR_DATA_DIR",
    "VL_ANCHOR_TASKS_ROOT",
    "VL_ANCHOR_DB_PATH",
    "VL_ANCHOR_DB_ENABLED",
    "VL_ANCHOR_AUTH_TOKEN",
    "VL_ANCHOR_AUTH_TOKEN_FILE",
    MASTER_KEY_VAR,
    f"{MASTER_KEY_VAR}_FILE",
]

_GLOBAL: dict[str, Any] = {
    "model": {"provider": "stub", "timeout_s": 30},
    "paths": {"data_dir": ".", "tasks_root": "tasks", "prompts_dir": "prompts"},
    "server": {"host": "127.0.0.1", "port": 8765},
}


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated config directory and point the environment at it.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The created ``config/`` directory.
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    directory = tmp_path / "config"
    directory.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    payload = {**_GLOBAL, "paths": {**_GLOBAL["paths"], "data_dir": data_dir.as_posix()}}
    save_yaml_atomic(payload, directory / "global.yaml")
    monkeypatch.setenv("VL_ANCHOR_CONFIG_DIR", str(directory))
    return directory


@pytest.fixture
def store(config_dir: Path) -> ConfigStore:
    """Build a store over the isolated configuration.

    Args:
        config_dir: Isolated configuration directory.

    Returns:
        A :class:`ConfigStore` bound to the test configuration.
    """
    return ConfigStore(load_settings(), config_dir)


def _write_overrides(config_dir: Path, overrides: dict[str, Any]) -> None:
    """Write an ``overrides.yaml`` into the test configuration.

    Args:
        config_dir: Isolated configuration directory.
        overrides: Mapping to persist.
    """
    save_yaml_atomic(overrides, config_dir / "overrides.yaml")


def test_yaml_layer_is_attributed(store: ConfigStore) -> None:
    """A value from global.yaml is reported as coming from yaml."""
    view = store.effective().vl.provider
    assert view.value == "stub"
    assert view.source == "yaml"
    assert view.locked is False
    assert view.editable is True


def test_overrides_layer_wins_over_yaml(config_dir: Path, store: ConfigStore) -> None:
    """A value from overrides.yaml is attributed to overrides, not yaml."""
    _write_overrides(config_dir, {"model": {"vl": {"base_url": "http://override/v1"}}})
    view = ConfigStore(load_settings(), config_dir).effective().vl.base_url
    assert view.value == "http://override/v1"
    assert view.source == "overrides"


def test_environment_locks_field(store: ConfigStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment variable makes a field locked and non-editable."""
    monkeypatch.setenv("VL_VL_PROVIDER", "remote")
    view = store.effective().vl.provider
    assert view.value == "remote"
    assert view.source == "env"
    assert view.locked is True
    assert view.locked_by == "VL_VL_PROVIDER"
    assert view.editable is False


def test_write_persists_only_to_overrides(config_dir: Path, store: ConfigStore) -> None:
    """Writing leaves global.yaml byte-identical and creates overrides.yaml."""
    global_before = (config_dir / "global.yaml").read_bytes()
    result = store.write(
        ConfigPatch(vl=RolePatch(provider="remote", base_url="http://vl/v1", model="qwen2.5-vl"))
    )
    assert result.ignored == []
    assert result.written["vl"] == {
        "provider": "remote",
        "base_url": "http://vl/v1",
        "model": "qwen2.5-vl",
    }
    assert result.restart_required is True
    assert (config_dir / "global.yaml").read_bytes() == global_before
    assert load_yaml(config_dir / "overrides.yaml")["model"]["vl"]["model"] == "qwen2.5-vl"
    # A fresh store (and therefore the next process) resolves the new values.
    assert ConfigStore(load_settings(), config_dir).effective().vl.provider.source == "overrides"


def test_write_stores_api_key_encrypted(config_dir: Path, store: ConfigStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """An api_key goes to the encrypted store, never into a YAML file."""
    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    store = ConfigStore(load_settings(), config_dir)
    result = store.write(ConfigPatch(vl=RolePatch(api_key="sk-marker-value")))
    assert result.written["vl"]["api_key"] == "***"
    view = ConfigStore(load_settings(), config_dir).effective().vl
    assert view.has_api_key is True
    assert view.api_key.value is None  # the plaintext is never echoed back
    assert view.api_key.source == "secrets"
    assert view.api_key_masked == "****alue"
    assert not find_plaintext_secrets(config_dir)
    overrides = config_dir / "overrides.yaml"
    assert not overrides.exists() or b"sk-marker-value" not in overrides.read_bytes()
    assert b"sk-marker-value" not in (config_dir / "secrets.db").read_bytes()


def test_write_api_key_blocked_without_master_key(config_dir: Path, store: ConfigStore) -> None:
    """Without a master key the key is refused with an actionable reason."""
    result = store.write(ConfigPatch(vl=RolePatch(api_key="sk-value")))
    assert result.written == {}
    assert [i.reason for i in result.ignored] == ["no_secret_store"]
    assert not (config_dir / "secrets.db").exists()


def test_write_skips_env_locked_field(store: ConfigStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """A field pinned by the environment is reported, not silently overwritten."""
    monkeypatch.setenv("VL_VL_PROVIDER", "remote")
    result = store.write(ConfigPatch(vl=RolePatch(provider="stub")))
    assert result.written == {}
    assert [(i.field, i.reason) for i in result.ignored] == [("provider", "env_locked")]


def test_write_reports_unwritable_directory(
    config_dir: Path, store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only configuration directory yields not_writable and no file."""
    monkeypatch.setattr(ConfigStore, "is_writable", lambda self: False)
    result = store.write(ConfigPatch(vl=RolePatch(provider="remote")))
    assert result.written == {}
    assert [(i.field, i.reason) for i in result.ignored] == [("provider", "not_writable")]
    assert not (config_dir / "overrides.yaml").exists()


def test_provider_back_to_stub_clears_stale_endpoint(config_dir: Path) -> None:
    """Reverting to stub drops base_url/model so they cannot resurface later."""
    _write_overrides(
        config_dir,
        {"model": {"vl": {"provider": "remote", "base_url": "http://old/v1", "model": "old-model"}}},
    )
    store = ConfigStore(load_settings(), config_dir)
    result = store.write(ConfigPatch(vl=RolePatch(provider="stub")))
    assert result.written["vl"]["provider"] == "stub"
    role_overrides = load_yaml(config_dir / "overrides.yaml")["model"]["vl"]
    assert "base_url" not in role_overrides
    assert "model" not in role_overrides


def test_validate_patch_requires_endpoint(store: ConfigStore) -> None:
    """provider=remote without base_url/model is rejected with both messages."""
    errors = store.validate_patch(ConfigPatch(vl=RolePatch(provider="remote")))
    assert len(errors) == 2
    assert all("base_url" in e or "model" in e for e in errors)


def test_validate_patch_rejects_bad_scheme(store: ConfigStore) -> None:
    """A base_url without an http(s) scheme is rejected before it is written."""
    errors = store.validate_patch(ConfigPatch(vl=RolePatch(provider="remote", base_url="vl:8000", model="m")))
    assert len(errors) == 1
    assert "http://" in errors[0]


def test_validate_patch_accepts_merged_values(config_dir: Path) -> None:
    """Submitting only provider is valid when base_url/model already exist."""
    _write_overrides(config_dir, {"model": {"vl": {"base_url": "http://vl/v1", "model": "qwen2.5-vl"}}})
    store = ConfigStore(load_settings(), config_dir)
    assert store.validate_patch(ConfigPatch(vl=RolePatch(provider="remote"))) == []


def test_validate_patch_rejects_empty_patch(store: ConfigStore) -> None:
    """An empty patch is rejected rather than treated as a no-op success."""
    assert store.validate_patch(ConfigPatch()) == ["未提交任何字段。"]
    assert store.validate_patch(ConfigPatch(vl=RolePatch())) == ["未提交任何字段。"]


def test_unchanged_sentinel_does_not_touch_key(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mask round-tripped by the UI must not overwrite the stored key."""
    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    store = ConfigStore(load_settings(), config_dir)
    assert store.write(ConfigPatch(vl=RolePatch(api_key="sk-original"))).written
    result = store.write(ConfigPatch(vl=RolePatch(provider="remote", base_url="http://vl/v1", model="m", api_key="***")))
    assert "api_key" not in result.written["vl"]
    assert ConfigStore(load_settings(), config_dir).effective().vl.has_api_key is True


def test_plaintext_yaml_key_is_ignored(config_dir: Path) -> None:
    """A key committed into global.yaml is reported, never used as the credential."""
    payload = {
        "model": {"provider": "remote", "base_url": "http://vl/v1", "model": "qwen2.5-vl", "api_key": "sk-leaked"},
        "paths": _GLOBAL["paths"],
    }
    save_yaml_atomic(payload, config_dir / "global.yaml")
    assert find_plaintext_secrets(config_dir) == ["global.yaml"]
    view = ConfigStore(load_settings(), config_dir).effective().vl
    assert view.has_api_key is False
    assert view.api_key.source == "default"


def test_secret_store_is_not_the_index_db(config_dir: Path) -> None:
    """The encrypted store is a sibling of index.db, not the index itself."""
    settings = load_settings()
    view = ConfigStore(settings, config_dir).effective()
    assert view.secrets.store_path == str(config_dir / "secrets.db")
    assert str(settings.db_path) != view.secrets.store_path


def test_config_persistent_true_on_a_plain_directory(config_dir: Path) -> None:
    """Outside a container the directory is the real filesystem and is durable."""
    assert config_persistent(config_dir) is True


def test_mask_secret_hides_short_values() -> None:
    """A short secret is masked entirely instead of mostly revealed."""
    assert mask_secret("") == ""
    assert mask_secret("abc") == ""
    assert mask_secret("abcdef") == "****cdef"
