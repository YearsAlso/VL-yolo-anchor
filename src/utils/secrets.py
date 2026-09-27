"""Secret resolution: environment indirection and an encrypted local store.

Two layers, in priority order:

1. **Environment variables** — ``{NAME}`` or ``{NAME}_FILE`` (the Docker/K8s
   secrets convention; the file form wins, so a deployment can keep the value
   out of ``.env``, the image, and ``docker inspect`` output).
2. **``config/secrets.db``** — a Fernet-encrypted SQLite store written by the
   settings UI or ``run.py secret-set``. Values never touch the disk in
   plaintext, and the master key is deliberately *not* stored beside them.

This module must not import :mod:`src.config`: ``config.py`` imports from here,
and the reverse would be an import cycle.

The store is a convenience layer, not a startup requirement. With no master key
configured, :meth:`SecretStore.state` reports
:attr:`SecretStoreState.NO_MASTER_KEY`, no file is created, and the platform runs
on environment secrets alone.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

MASTER_KEY_VAR = "VL_ANCHOR_SECRET_KEY"
"""Environment variable holding the Fernet master key (or its ``_FILE`` twin)."""

SECRETS_FILENAME = "secrets.db"
"""File name of the encrypted store, relative to the config directory."""

MANAGED_SECRET_NAMES: tuple[str, ...] = (
    "VL_ANCHOR_AUTH_TOKEN",
    "VL_MODEL_API_KEY",
    "VL_LLM_API_KEY",
    "VL_VL_API_KEY",
)
"""Secrets the CLI is allowed to write, named after the variable they replace."""

_KEY_CHECK = "key_check"
"""``meta`` row holding a known plaintext, used to tell a wrong key from a missing one."""

_KEY_CHECK_PLAINTEXT = "vl-yolo-anchor-secret-store-v1"
"""The value encrypted into :data:`_KEY_CHECK`."""

_SCHEMA_SECRETS = """
CREATE TABLE IF NOT EXISTS secrets (
  name TEXT PRIMARY KEY,
  ciphertext BLOB NOT NULL,
  updated_at TEXT NOT NULL
)
"""

_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
)
"""


def read_secret(name: str) -> tuple[str, str]:
    """Read a secret from ``{name}_FILE`` (preferred) or ``{name}``.

    The file indirection is the Docker/Kubernetes secrets convention: pointing
    the variable at a mounted file keeps the value out of ``.env``, out of the
    image, and out of ``docker inspect`` output. Its contents are read as UTF-8
    (BOM tolerated) and stripped, so a trailing newline from ``echo`` is fine.

    When ``{name}_FILE`` is set but unreadable or empty the direct variable is
    used instead, with a warning: a broken mount should not silently leave the
    platform unauthenticated without explanation.

    Args:
        name: Base variable name, e.g. ``"VL_MODEL_API_KEY"``.

    Returns:
        ``(value, source_var)``. Both are empty strings when the secret is not
        configured. ``source_var`` names the variable that supplied the value,
        for display in the settings UI — never the value itself.
    """
    file_var = f"{name}_FILE"
    raw_path = os.environ.get(file_var)
    if raw_path:
        try:
            value = Path(raw_path).read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            logger.warning(
                "Could not read secret file %s=%s (%s); falling back to %s", file_var, raw_path, exc, name
            )
        else:
            if value:
                return value, file_var
            logger.warning("%s=%s is empty; falling back to %s", file_var, raw_path, name)
    value = os.environ.get(name) or ""
    return (value, name) if value else ("", "")


def generate_master_key() -> str:
    """Generate a new Fernet master key.

    The caller is responsible for delivering it to a Docker secret or a
    ``*_FILE`` mount. Nothing here writes it to disk: a key stored next to the
    ciphertext it protects is obfuscation, not encryption.

    Returns:
        A URL-safe base64 key string suitable for :func:`is_valid_master_key`.
    """
    return Fernet.generate_key().decode("ascii")


def is_valid_master_key(value: str) -> bool:
    """Report whether a string is a usable Fernet key.

    Args:
        value: Candidate key.

    Returns:
        ``True`` when :class:`cryptography.fernet.Fernet` accepts it.
    """
    if not value:
        return False
    try:
        Fernet(value.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    return True


class SecretStoreState(StrEnum):
    """Availability of the encrypted store."""

    OK = "ok"
    """Master key present and existing ciphertext readable: reads and writes work."""

    NO_MASTER_KEY = "no_master_key"
    """No master key configured. Legal default; the store is simply not used."""

    BAD_MASTER_KEY = "bad_master_key"
    """A master key is configured but is malformed or cannot decrypt the store."""

    UNAVAILABLE = "unavailable"
    """The store file is unreadable, corrupt, or shares a path with the index DB."""


_STATE_REASONS: dict[SecretStoreState, str] = {
    SecretStoreState.NO_MASTER_KEY: (
        "未配置主密钥，密钥只能来自环境变量。"
        f"用 `run.py secret-keygen` 生成主密钥，再通过 {MASTER_KEY_VAR}_FILE 指向的挂载文件提供。"
    ),
    SecretStoreState.BAD_MASTER_KEY: (
        "已配置的主密钥无法使用：要么格式不是合法 Fernet key，要么与 config/secrets.db 中的密文不匹配。"
        "请恢复原主密钥，或删除 config/secrets.db 后重新录入密钥。"
    ),
    SecretStoreState.UNAVAILABLE: (
        "密文库不可用：文件损坏或与索引库路径冲突（两者不能是同一个文件）。"
        "删除 config/secrets.db 可重建（已存的密钥将丢失，需重新录入）。"
    ),
}


class SecretStore:
    """Encrypted key/value store backed by a Fernet-encrypted SQLite file.

    The store is deliberately a *separate file* from the rebuildable metadata
    index: ``index.db`` may be deleted at any time and ``index --rebuild``
    rewrites it, either of which would destroy credentials stored there.

    Attributes:
        _db_path: Path of the encrypted store.
        _index_db_path: Path of the metadata index, checked for collision.
        _cache: Per-process value cache, cleared on every write.
    """

    def __init__(self, db_path: Path, *, index_db_path: Path | None = None) -> None:
        """Initialize the store handle.

        Args:
            db_path: Location of the encrypted store; need not exist yet.
            index_db_path: Location of the metadata index, used only to refuse a
                configuration where both resolve to the same file.
        """
        self._db_path = db_path
        self._index_db_path = index_db_path
        self._cache: dict[str, str] = {}
        self._state: SecretStoreState | None = None

    def path(self) -> Path:
        """Return the store's path.

        Returns:
            Path to the encrypted store.
        """
        return self._db_path

    def master_key_source(self) -> str:
        """Return the name of the variable that supplied the master key.

        Returns:
            ``"VL_ANCHOR_SECRET_KEY_FILE"`` / ``"VL_ANCHOR_SECRET_KEY"``, or
            ``""`` when no master key is configured. Never the key itself.
        """
        _value, source = read_secret(MASTER_KEY_VAR)
        return source

    def state(self) -> SecretStoreState:
        """Report whether the store can be read and written.

        The result is cached for the process lifetime and refreshed by
        :meth:`set`, which is the only thing that can create the file.

        Returns:
            The current :class:`SecretStoreState`.
        """
        if self._state is None:
            self._state = self._compute_state()
        return self._state

    def describe_state(self) -> str:
        """Return a user-facing, actionable explanation of the current state.

        Shared by the settings UI and ``doctor`` so the two cannot drift apart.

        Returns:
            A Chinese explanation; empty when the store is usable.
        """
        return _STATE_REASONS.get(self.state(), "")

    def get(self, name: str) -> str:
        """Read and decrypt one secret.

        Args:
            name: Secret name, e.g. ``"VL_VL_API_KEY"``.

        Returns:
            The plaintext, or ``""`` when unset or when the store is unusable.
        """
        if self.state() is not SecretStoreState.OK:
            return ""
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        readable, value = self._fetch(name)
        if not readable or value is None:
            return ""
        plain = self._decrypt(value)
        if plain:
            self._cache[name] = plain
        return plain

    def set(self, name: str, value: str) -> bool:
        """Encrypt and store one secret, or delete it when ``value`` is empty.

        Args:
            name: Secret name, e.g. ``"VL_ANCHOR_AUTH_TOKEN"``.
            value: Plaintext to store; an empty string deletes the entry.

        Returns:
            ``True`` when the write committed, ``False`` when the store is
            unusable or the write failed. Never raises.
        """
        state = self.state()
        if state is not SecretStoreState.OK:
            logger.warning("Refusing to write secret %s: %s", name, self.describe_state())
            return False

        key, _source = read_secret(MASTER_KEY_VAR)
        if not is_valid_master_key(key):
            logger.warning("Refusing to write secret %s: master key is not a valid Fernet key", name)
            return False
        cipher = Fernet(key.encode("utf-8"))

        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            try:
                with conn:
                    conn.execute(_SCHEMA_SECRETS)
                    conn.execute(_SCHEMA_META)
                    self._write_key_check(conn, cipher)
                    if value:
                        conn.execute(
                            "INSERT OR REPLACE INTO secrets (name, ciphertext, updated_at) VALUES (?, ?, ?)",
                            (name, cipher.encrypt(value.encode("utf-8")), _utc_now()),
                        )
                    else:
                        conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Could not write secret %s to %s: %s", name, self._db_path, exc)
            return False

        self._cache.pop(name, None)
        self._state = None
        return True

    def _compute_state(self) -> SecretStoreState:
        """Derive the store's state, including a sentinel decryption attempt.

        Returns:
            The computed :class:`SecretStoreState`.
        """
        if self._paths_conflict():
            logger.warning(
                "Secret store and metadata index resolve to the same file (%s); "
                "refusing to use it so the index stays disposable.",
                self._db_path,
            )
            return SecretStoreState.UNAVAILABLE

        key, _source = read_secret(MASTER_KEY_VAR)
        if not key:
            return SecretStoreState.NO_MASTER_KEY
        if not is_valid_master_key(key):
            return SecretStoreState.BAD_MASTER_KEY
        if not self._db_path.is_file():
            return SecretStoreState.OK

        readable, check = self._fetch_meta(_KEY_CHECK)
        if not readable:
            return SecretStoreState.UNAVAILABLE
        if check is None:
            return SecretStoreState.OK  # store exists without a sentinel; usable
        try:
            Fernet(key.encode("utf-8")).decrypt(check.encode("utf-8"))
        except (InvalidToken, ValueError, TypeError):
            return SecretStoreState.BAD_MASTER_KEY
        return SecretStoreState.OK

    def _paths_conflict(self) -> bool:
        """Report whether the store and the metadata index share a path.

        Returns:
            ``True`` when both paths resolve to the same file.
        """
        if self._index_db_path is None:
            return False
        try:
            return self._db_path.resolve() == self._index_db_path.resolve()
        except OSError:
            return False

    def _fetch(self, name: str) -> tuple[bool, str | None]:
        """Read one ciphertext from the ``secrets`` table.

        Args:
            name: Secret name.

        Returns:
            ``(readable, ciphertext)``; ``ciphertext`` is ``None`` when absent.
        """
        return self._query("SELECT ciphertext FROM secrets WHERE name = ?", (name,))

    def _fetch_meta(self, meta_key: str) -> tuple[bool, str | None]:
        """Read one value from the ``meta`` table.

        Args:
            meta_key: Row key.

        Returns:
            ``(readable, value)``; ``value`` is ``None`` when absent.
        """
        return self._query("SELECT value FROM meta WHERE key = ?", (meta_key,))

    def _query(self, sql: str, params: tuple[str, ...]) -> tuple[bool, str | None]:
        """Run a single-value query against the store.

        A missing file is reported as readable-but-absent so callers can treat
        "no store yet" as "no secrets yet". A file that exists but cannot be
        queried is reported as unreadable.

        Args:
            sql: Query returning at most one row and one column.
            params: Query parameters.

        Returns:
            ``(readable, value)``.
        """
        if not self._db_path.is_file():
            return True, None
        try:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            try:
                row = conn.execute(sql, params).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("Could not read secret store %s: %s", self._db_path, exc)
            return False, None
        if row is None or row[0] is None:
            return True, None
        value = row[0]
        if isinstance(value, bytes):
            return True, value.decode("utf-8", errors="replace")
        return True, str(value)

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext read from the store.

        Args:
            ciphertext: Token produced by :meth:`set`.

        Returns:
            The plaintext, or ``""`` when it cannot be decrypted.
        """
        key, _source = read_secret(MASTER_KEY_VAR)
        try:
            return Fernet(key.encode("utf-8")).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError) as exc:
            logger.warning("Could not decrypt a secret in %s: %s", self._db_path, exc)
            return ""

    def _write_key_check(self, conn: sqlite3.Connection, cipher: Fernet) -> None:
        """Write the sentinel row on first use.

        The sentinel is what distinguishes "no master key configured" from "the
        wrong master key is configured" — without it every decrypt failure would
        look like an empty store.

        Args:
            conn: Open connection inside a transaction.
            cipher: Fernet instance built from the current master key.
        """
        row = conn.execute("SELECT 1 FROM meta WHERE key = ?", (_KEY_CHECK,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_KEY_CHECK, cipher.encrypt(_KEY_CHECK_PLAINTEXT.encode("utf-8")).decode("utf-8")),
            )


def _utc_now() -> str:
    """Return the current UTC timestamp in ISO 8601 form.

    Returns:
        Timestamp string with a ``Z`` suffix.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
