"""Append-only JSONL audit logs.

These files are the platform's source-of-truth records for *what happened*
(step runs, model calls, diagnostics). The SQLite index in
:mod:`src.core.metadata_store` is a rebuildable projection of them: deleting the
database never loses history because the JSONL is authoritative.

Writes are best-effort by design. An unwritable or full disk must not fail a
pipeline step, so :func:`append_record` reports failure via its return value
instead of raising.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    """Return the process-wide append lock guarding ``path``.

    Uvicorn serves requests from a thread pool, so two steps can append to the
    same log concurrently. One record per ``write()`` under a per-path lock
    keeps lines from interleaving.

    Args:
        path: Audit log path.

    Returns:
        The lock shared by all writers of that path in this process.
    """
    key = str(path.resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def append_record(path: Path, record: dict[str, Any]) -> bool:
    """Append one JSON object to a JSONL file as a single line.

    Creates parent directories as needed. Never raises: a failed append is
    logged as a warning and reported through the return value.

    Args:
        path: Audit log path.
        record: JSON-serializable mapping; must not contain secrets.

    Returns:
        ``True`` when the record was written, ``False`` on any write failure.
    """
    line = json.dumps(record, ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock_for(path), path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Could not append audit record to %s: %s", path, exc)
        return False
    return True


def read_records(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Read JSONL records in file (chronological) order.

    A missing or unreadable file yields an empty list — an absent log means an
    empty history, not an error. Malformed lines are skipped with a warning so
    one corrupt line cannot hide the rest of the file.

    Args:
        path: Audit log path.
        limit: Keep only the last ``limit`` records (0 yields none); ``None``
            returns everything.

    Returns:
        Parsed records, oldest first.
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Could not read audit log %s: %s", path, exc)
        return []

    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed: Any = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed JSONL line %s:%d", path.name, lineno)
            continue
        if not isinstance(parsed, dict):
            logger.warning("Skipping non-object JSONL line %s:%d", path.name, lineno)
            continue
        records.append(parsed)

    if limit is not None:
        records = records[-limit:] if limit > 0 else []
    return records
