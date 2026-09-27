"""Tests for src/utils/secrets.py: env indirection and the encrypted store.

Every test that needs a master key sets it through ``monkeypatch.setenv``; the
real process environment is never touched, so the suite cannot leak a key into
a later test or into the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.utils.secrets import (
    MASTER_KEY_VAR,
    SecretStore,
    SecretStoreState,
    generate_master_key,
    is_valid_master_key,
    read_secret,
)

_NAME = "VL_VL_API_KEY"


def _key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a fresh master key in the environment.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The generated key.
    """
    key = generate_master_key()
    monkeypatch.setenv(MASTER_KEY_VAR, key)
    return key


def test_read_secret_prefers_file_indirection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``{NAME}_FILE`` wins over the direct variable and strips whitespace."""
    secret_file = tmp_path / "api_key"
    secret_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv(f"{_NAME}_FILE", str(secret_file))
    monkeypatch.setenv(_NAME, "from-env")
    assert read_secret(_NAME) == ("from-file", f"{_NAME}_FILE")


def test_read_secret_tolerates_bom(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A UTF-8 BOM (Windows ``echo`` / Notepad) does not corrupt the value."""
    secret_file = tmp_path / "api_key"
    secret_file.write_bytes("﻿sk-secret".encode())
    monkeypatch.setenv(f"{_NAME}_FILE", str(secret_file))
    value, _source = read_secret(_NAME)
    assert value == "sk-secret"


def test_read_secret_falls_back_when_file_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unreadable ``_FILE`` path falls back to the direct variable."""
    monkeypatch.setenv(f"{_NAME}_FILE", str(tmp_path / "absent"))
    monkeypatch.setenv(_NAME, "from-env")
    assert read_secret(_NAME) == ("from-env", _NAME)


def test_read_secret_unset_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No variable and no file reports an unconfigured secret, not an error."""
    monkeypatch.delenv(_NAME, raising=False)
    monkeypatch.delenv(f"{_NAME}_FILE", raising=False)
    assert read_secret(_NAME) == ("", "")


def test_master_key_validation() -> None:
    """Generated keys are accepted; arbitrary strings are not."""
    assert is_valid_master_key(generate_master_key())
    assert not is_valid_master_key("not-a-key")
    assert not is_valid_master_key("")


def test_store_without_master_key_is_inert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no master key the store reports NO_MASTER_KEY and creates no file."""
    monkeypatch.delenv(MASTER_KEY_VAR, raising=False)
    monkeypatch.delenv(f"{MASTER_KEY_VAR}_FILE", raising=False)
    db = tmp_path / "secrets.db"
    store = SecretStore(db)
    assert store.state() is SecretStoreState.NO_MASTER_KEY
    assert store.get(_NAME) == ""
    assert store.set(_NAME, "value") is False
    assert not db.exists()
    assert store.describe_state()


def test_store_roundtrip_encrypts_at_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored value reads back and never appears as plaintext in the file."""
    _key(monkeypatch)
    db = tmp_path / "secrets.db"
    store = SecretStore(db)
    assert store.state() is SecretStoreState.OK
    assert store.set(_NAME, "sk-plaintext-marker") is True
    assert store.get(_NAME) == "sk-plaintext-marker"
    assert b"sk-plaintext-marker" not in db.read_bytes()


def test_store_delete_removes_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Storing an empty string deletes the secret."""
    _key(monkeypatch)
    store = SecretStore(tmp_path / "secrets.db")
    assert store.set(_NAME, "value") is True
    assert store.set(_NAME, "") is True
    assert store.get(_NAME) == ""


def test_store_rejects_wrong_master_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A different master key is BAD_MASTER_KEY, not an empty store."""
    _key(monkeypatch)
    db = tmp_path / "secrets.db"
    assert SecretStore(db).set(_NAME, "value") is True

    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    reopened = SecretStore(db)
    assert reopened.state() is SecretStoreState.BAD_MASTER_KEY
    assert reopened.get(_NAME) == ""
    assert reopened.set(_NAME, "other") is False


def test_store_rejects_malformed_master_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A key that is not a valid Fernet key is reported as BAD_MASTER_KEY."""
    monkeypatch.setenv(MASTER_KEY_VAR, "definitely-not-a-fernet-key")
    store = SecretStore(tmp_path / "secrets.db")
    assert store.state() is SecretStoreState.BAD_MASTER_KEY
    assert store.set(_NAME, "value") is False


def test_store_refuses_path_collision_with_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The store and the disposable index may not share a file."""
    _key(monkeypatch)
    shared = tmp_path / "index.db"
    store = SecretStore(shared, index_db_path=shared)
    assert store.state() is SecretStoreState.UNAVAILABLE
    assert store.set(_NAME, "value") is False
    assert not shared.exists()


def test_store_reports_unavailable_on_corrupt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that exists but is not a SQLite database does not crash the store."""
    _key(monkeypatch)
    db = tmp_path / "secrets.db"
    db.write_bytes(b"not a sqlite database, not even close" * 8)
    store = SecretStore(db)
    assert store.state() is SecretStoreState.UNAVAILABLE
    assert store.get(_NAME) == ""


def test_master_key_source_names_variable_not_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``master_key_source`` reports the variable name for display in the UI."""
    key = _key(monkeypatch)
    store = SecretStore(tmp_path / "secrets.db")
    assert store.master_key_source() == MASTER_KEY_VAR
    assert key not in store.master_key_source()
