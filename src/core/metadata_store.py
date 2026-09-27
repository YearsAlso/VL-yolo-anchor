"""SQLite metadata index derived from on-disk audit logs.

**The disk is the only source of truth.** Every row in this database can be
rebuilt from ``tasks/**`` plus the three append-only JSONL logs, so ``index.db``
may be deleted at any time — ``run.py index --rebuild`` restores it, and nothing
in the platform's read paths depends on it. Two consequences shape this module:

* Writes are best-effort: they log a warning and return rather than raising, so
  a read-only mount or a corrupt file can never fail a pipeline step.
* The store never touches ``config/secrets.db``. ``rebuild()`` deletes rows from
  ``index.db`` only; credentials live in a different file precisely so an index
  rebuild cannot destroy them.

Privacy: no prompt text, no image bytes, no ``api_key``. A ``base_url`` is
reduced to ``scheme://host:port`` before it is stored, so a URL with embedded
credentials (``http://user:pass@host/v1``) leaves no secret in the database.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, Field

from src.agents.model_client import host_of
from src.utils.file_utils import list_images

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checking only
    from src.core.diagnostics import DiagnosticReport
    from src.core.task_manager import TaskManager

__all__ = [
    "CallEntry",
    "DIAGNOSTICS_FILENAME",
    "IndexStats",
    "MAX_HISTORY_LIMIT",
    "MODEL_CALLS_FILENAME",
    "MetadataStore",
    "RUN_HISTORY_FILENAME",
    "RebuildResult",
    "RunEntry",
    "TaskCounts",
    "TaskHistory",
    "count_task_dir",
    "host_of",
    "read_task_history",
]

logger = logging.getLogger(__name__)

RUN_HISTORY_FILENAME = "run_history.jsonl"
"""Per-task audit log of ``run_step`` executions."""

MODEL_CALLS_FILENAME = "model_calls.jsonl"
"""Per-task audit log of model calls."""

DIAGNOSTICS_FILENAME = "diagnostics.jsonl"
"""Instance-wide audit log of self-check runs."""

MAX_HISTORY_LIMIT = 500
"""Upper bound on ``read_task_history(limit=...)``; the API takes a query param."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL, image_count INTEGER NOT NULL DEFAULT 0,
  ai_label_count INTEGER NOT NULL DEFAULT 0, candidate_label_count INTEGER NOT NULL DEFAULT 0,
  has_plan INTEGER NOT NULL DEFAULT 0, has_report INTEGER NOT NULL DEFAULT 0,
  has_dataset INTEGER NOT NULL DEFAULT 0, indexed_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, step TEXT NOT NULL,
  status TEXT NOT NULL, started_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
  items INTEGER, message TEXT NOT NULL DEFAULT '',
  UNIQUE(task, step, started_at));
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task, started_at DESC);

CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, role TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, http_status INTEGER, duration_ms INTEGER NOT NULL,
  started_at TEXT NOT NULL, UNIQUE(task, role, started_at));

CREATE TABLE IF NOT EXISTS diagnostics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL,
  status TEXT NOT NULL, report_json TEXT NOT NULL, UNIQUE(generated_at));

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

SCHEMA_VERSION = "1"
"""Stored in ``schema_meta`` so a future migration can detect old files."""

_EntryT = TypeVar("_EntryT", "RunEntry", "CallEntry")
"""Either audit-log entry model; :func:`_read_entries` is generic over them."""

_REQUIRED_RUN_FIELDS = ("step", "started_at")
"""Fields a ``run_history.jsonl`` line needs to be more than noise."""

_REQUIRED_CALL_FIELDS = ("role", "started_at")
"""Fields a ``model_calls.jsonl`` line needs to be more than noise."""

ReplayFn = Callable[[str, dict[str, Any]], bool]
"""Signature of a per-record replay handler: ``(task, record) -> written``."""


class RunEntry(BaseModel):
    """One ``run_step`` execution, as read from ``run_history.jsonl``.

    Attributes:
        step: Pipeline step name.
        status: ``ok`` or ``error``.
        started_at: ISO 8601 UTC start timestamp.
        finished_at: ISO 8601 UTC finish timestamp.
        duration_ms: Wall-clock duration.
        items: Step-dependent count (labels written, issues found), or ``None``.
        message: Error text for a failed step; empty on success.
    """

    step: str
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    items: int | None = None
    message: str = ""


class CallEntry(BaseModel):
    """One model call, as read from ``model_calls.jsonl``.

    Attributes:
        role: ``llm`` or ``vl``.
        provider: Provider that served the call (``stub`` / ``remote``).
        model: Model name; empty for the stub.
        host: ``scheme://host:port`` with any userinfo stripped.
        status: ``ok``, ``error``, or ``stub`` when no model was involved.
        http_status: Response status, when a response was received.
        duration_ms: Wall-clock duration.
        started_at: ISO 8601 UTC timestamp.
    """

    role: str
    provider: str
    model: str = ""
    host: str = ""
    status: str
    http_status: int | None = None
    duration_ms: int = 0
    started_at: str


class TaskHistory(BaseModel):
    """A task's execution history, read from disk rather than from the index.

    Attributes:
        task: Task name.
        runs: Step executions, newest first.
        calls: Model calls, newest first.
        source: Always ``"jsonl"`` — records that this came from the audit log.
    """

    task: str
    runs: list[RunEntry] = Field(default_factory=list)
    calls: list[CallEntry] = Field(default_factory=list)
    source: str = "jsonl"


class TaskCounts(BaseModel):
    """Counts and flags describing a task directory.

    Attributes:
        image_count: Number of input images.
        ai_label_count: Number of label files under ``ai_labels/``.
        candidate_label_count: Number of label files under ``candidate_labels/``.
        has_plan: Whether ``plan.yaml`` exists.
        has_report: Whether ``inspection_report.yaml`` exists.
        has_dataset: Whether the exported ``dataset/`` directory exists.
    """

    image_count: int = 0
    ai_label_count: int = 0
    candidate_label_count: int = 0
    has_plan: bool = False
    has_report: bool = False
    has_dataset: bool = False


class IndexStats(BaseModel):
    """Index state and row counts.

    Attributes:
        enabled: Whether indexing is configured on.
        available: Whether the database could actually be opened and queried.
        db_path: Database location.
        db_size_bytes: File size, or ``0`` when absent.
        last_indexed_at: Timestamp of the newest indexed task, or ``""``.
        tasks: Row count of ``tasks``.
        runs: Row count of ``runs``.
        calls: Row count of ``model_calls``.
        diagnostics: Row count of ``diagnostics``.
    """

    enabled: bool = True
    available: bool = False
    db_path: str = ""
    db_size_bytes: int = 0
    last_indexed_at: str = ""
    tasks: int = 0
    runs: int = 0
    calls: int = 0
    diagnostics: int = 0


class RebuildResult(BaseModel):
    """Rows indexed by a :meth:`MetadataStore.rebuild` call.

    Attributes:
        tasks: Tasks indexed.
        runs: Run-history rows written.
        calls: Model-call rows written.
        diagnostics: Diagnostic rows written.
    """

    tasks: int = 0
    runs: int = 0
    calls: int = 0
    diagnostics: int = 0


def read_task_history(task_dir: Path, *, task: str, limit: int = 50) -> TaskHistory:
    """Read a task's execution history from the on-disk audit logs.

    Deliberately independent of SQLite: the truth is in ``run_history.jsonl`` and
    ``model_calls.jsonl``, so history keeps working when the index is missing,
    stale, or unreadable. A missing or damaged log yields an empty list rather
    than an error — an old task that never ran is not a failure.

    Args:
        task_dir: The task's directory.
        task: Task name, echoed into the result.
        limit: Maximum entries per list, clamped to
            :data:`MAX_HISTORY_LIMIT`.

    Returns:
        A :class:`TaskHistory` with both lists newest first.
    """
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))
    runs = _read_entries(task_dir / RUN_HISTORY_FILENAME, RunEntry, limit, required=_REQUIRED_RUN_FIELDS)
    calls = _read_entries(task_dir / MODEL_CALLS_FILENAME, CallEntry, limit, required=_REQUIRED_CALL_FIELDS)
    return TaskHistory(task=task, runs=runs, calls=calls)


def _missing_fields(record: dict[str, Any], required: tuple[str, ...]) -> bool:
    """Report whether a record lacks any field needed to identify it.

    Used by both the disk reader and the index replay so the two views of a log
    agree on what counts as a record: an index that skips a line the history
    shows (or the reverse) would be worse than either alone.

    Args:
        record: Parsed JSONL record.
        required: Field names that must be present and non-empty.

    Returns:
        ``True`` when at least one required field is missing or empty.
    """
    return any(not record.get(field) for field in required)


def _read_entries(
    path: Path, model: type[_EntryT], limit: int, *, required: tuple[str, ...]
) -> list[_EntryT]:
    """Parse and validate audit-log entries, newest first.

    Ties on ``started_at`` are broken by file position: a fast run stamps all
    its steps within the same second, and the log is chronological, so falling
    back to the line order keeps "newest first" true when the clock is too
    coarse to say it.

    Args:
        path: JSONL file to read.
        model: Pydantic model to validate each line into.
        limit: Maximum entries to return.
        required: Field names an entry must carry to be usable.

    Returns:
        Validated entries, newest first; malformed lines are skipped.
    """
    parsed: list[tuple[str, int, _EntryT]] = []
    for position, record in enumerate(_iter_jsonl(path)):
        if not isinstance(record, dict):
            continue
        if _missing_fields(record, required):
            logger.warning("Skipping incomplete entry in %s: %s", path.name, record)
            continue
        try:
            entry = model.model_validate(record)
        except ValueError as exc:
            logger.warning("Skipping malformed entry in %s: %s", path, exc)
            continue
        parsed.append((str(record.get("started_at", "")), position, entry))
    parsed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [entry for _started, _position, entry in parsed[:limit]]


def _iter_jsonl(path: Path) -> Iterator[Any]:
    """Yield parsed JSON values from a JSONL file.

    Args:
        path: File to read.

    Yields:
        One parsed value per well-formed line; unparseable lines are logged and
        skipped.
    """
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read audit log %s: %s", path, exc)
        return
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("Skipping malformed JSONL line %s:%d: %s", path, number, exc)


class MetadataStore:
    """Rebuildable SQLite index over the on-disk audit logs.

    Attributes:
        _db_path: Database location.
        _enabled: Whether indexing is configured on.
    """

    def __init__(self, db_path: Path, *, enabled: bool = True) -> None:
        """Initialize the store handle.

        Nothing is created here: an unconfigured or unused deployment must not
        leave a stray database file behind.

        Args:
            db_path: Location of ``index.db``.
            enabled: ``False`` disables indexing entirely; every method then
                degrades to a no-op or an empty result.
        """
        self._db_path = db_path
        self._enabled = enabled

    @property
    def db_path(self) -> Path:
        """Return the database location.

        Returns:
            Path of ``index.db``.
        """
        return self._db_path

    @property
    def enabled(self) -> bool:
        """Report whether indexing is configured on.

        Returns:
            ``True`` unless the deployment disabled the index.
        """
        return self._enabled

    def available(self) -> bool:
        """Report whether the index can be used.

        An absent file counts as available when it could still be created: it is
        an empty index, not a broken one, and the first write creates it. Read
        paths must not create files, so this reports state rather than
        establishing it. A path that cannot exist (its parent is a file, or the
        data directory is gone) is *not* an empty index — reporting zeros there
        would hide a misconfiguration the caller can fix.

        Returns:
            ``False`` when indexing is disabled, the path is unusable, or the
            database exists but cannot be opened and prepared.
        """
        if not self._enabled:
            return False
        if not self._db_path.is_file():
            return self._db_path.parent.is_dir()
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Metadata index unavailable at %s: %s", self._db_path, exc)
            return False
        return True

    def record_run(
        self,
        task: str,
        step: str,
        status: str,
        started_at: str,
        duration_ms: int,
        items: int | None,
        message: str,
    ) -> None:
        """Index one ``run_step`` execution.

        Args:
            task: Task name.
            step: Pipeline step.
            status: ``ok`` or ``error``.
            started_at: ISO 8601 UTC start timestamp.
            duration_ms: Wall-clock duration.
            items: Step-dependent count.
            message: Error text for a failed step.
        """
        self._execute(
            "INSERT OR IGNORE INTO runs (task, step, status, started_at, duration_ms, items, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task, step, status, started_at, duration_ms, items, message),
        )

    def record_model_call(
        self,
        task: str,
        role: str,
        provider: str,
        model: str,
        base_url: str,
        status: str,
        http_status: int | None,
        duration_ms: int,
        started_at: str,
    ) -> None:
        """Index one model call.

        Args:
            task: Task name.
            role: ``llm`` or ``vl``.
            provider: Provider that served the call.
            model: Model name.
            base_url: Endpoint URL; reduced with :func:`host_of` before storage.
            status: ``ok``, ``error``, or ``stub``.
            http_status: Response status, when one was received.
            duration_ms: Wall-clock duration.
            started_at: ISO 8601 UTC timestamp.
        """
        self._execute(
            "INSERT OR IGNORE INTO model_calls "
            "(task, role, provider, model, host, status, http_status, duration_ms, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task, role, provider, model, host_of(base_url), status, http_status, duration_ms, started_at),
        )

    def record_diagnostic(self, report: DiagnosticReport) -> None:
        """Index one self-check report.

        Args:
            report: The report to index; stored as JSON so the full check list
                survives in the database.
        """
        payload = report.model_dump_json()
        self._execute(
            "INSERT OR IGNORE INTO diagnostics (generated_at, status, report_json) VALUES (?, ?, ?)",
            (report.generated_at, report.status, payload),
        )

    def upsert_task(self, task: str, description: str, counts: TaskCounts) -> None:
        """Insert or refresh a task's index row.

        Args:
            task: Task name.
            description: Task description from ``task.yaml``.
            counts: Counts gathered from the task directory.
        """
        self._execute(
            "INSERT OR REPLACE INTO tasks "
            "(name, description, updated_at, image_count, ai_label_count, candidate_label_count, "
            " has_plan, has_report, has_dataset, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task,
                description,
                _utc_now(),
                counts.image_count,
                counts.ai_label_count,
                counts.candidate_label_count,
                int(counts.has_plan),
                int(counts.has_report),
                int(counts.has_dataset),
                _utc_now(),
            ),
        )

    def index_task(self, task_manager: TaskManager, task: str) -> None:
        """Refresh one task's row from disk.

        Args:
            task_manager: Manager providing the task directory and config.
            task: Task name.
        """
        task_dir = task_manager.task_dir(task)
        description = ""
        try:
            config = task_manager.load_task(task)
        except (OSError, ValueError):
            config = {}
        section = config.get("task")
        if isinstance(section, dict):
            description = str(section.get("description", ""))
        self.upsert_task(task, description, count_task_dir(task_dir))

    def rebuild(self, task_manager: TaskManager, logs_dir: Path) -> RebuildResult:
        """Rebuild the whole index from disk.

        Every data table is emptied and rewritten from the JSONL logs, so the
        result is a function of the disk alone: a rebuild over a stale database
        converges to the same rows as a rebuild over a deleted one. Relying on
        the ``UNIQUE`` constraints alone would not — rows whose log lines have
        since disappeared (task removed, log rotated, JSONL truncated) would
        survive as orphans and the index would drift from its source. Idempotent:
        running it twice leaves the row counts and the result identical. Only
        ``index.db`` is touched — ``config/secrets.db`` is a different file and
        is never deleted or read here.

        Args:
            task_manager: Manager whose ``tasks_root`` is walked.
            logs_dir: Directory holding ``diagnostics.jsonl``.

        Returns:
            Row counts indexed, all zeros when indexing is disabled.
        """
        if not self._enabled:
            logger.info("Metadata index disabled; rebuild skipped")
            return RebuildResult()
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.execute("DELETE FROM tasks")
                conn.execute("DELETE FROM runs")
                conn.execute("DELETE FROM model_calls")
                conn.execute("DELETE FROM diagnostics")
                conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('runs', 'model_calls', 'diagnostics')")
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                    (SCHEMA_VERSION,),
                )
                conn.commit()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Rebuild failed for %s: %s", self._db_path, exc)
            return RebuildResult()

        tasks = 0
        runs = 0
        calls = 0
        for name in task_manager.list_tasks():
            task_dir = task_manager.task_dir(name)
            self.index_task(task_manager, name)
            tasks += 1
            runs += self._replay(task_dir / RUN_HISTORY_FILENAME, name, self._replay_run)
            calls += self._replay(task_dir / MODEL_CALLS_FILENAME, name, self._replay_call)
        diagnostics = self._replay_diagnostics(logs_dir / DIAGNOSTICS_FILENAME)
        return RebuildResult(tasks=tasks, runs=runs, calls=calls, diagnostics=diagnostics)

    def stats(self) -> IndexStats:
        """Report index state and row counts.

        Never raises: an unreadable or disabled index is reported as
        ``available=False`` with zero counts, which the API maps to 503. Making
        failure a return value rather than an exception means no caller can
        forget to catch it.

        Returns:
            An :class:`IndexStats`.
        """
        stats = IndexStats(enabled=self._enabled, db_path=str(self._db_path))
        if not self._enabled:
            return stats
        if not self._db_path.is_file():
            # No index yet: zeros are the accurate answer, and a read path must
            # not create the file. A path that could never be created is a
            # different story — see ``available()``.
            stats.available = self._db_path.parent.is_dir()
            return stats
        try:
            stats.db_size_bytes = self._db_path.stat().st_size
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                stats.tasks = _scalar(conn, "SELECT COUNT(*) FROM tasks")
                stats.runs = _scalar(conn, "SELECT COUNT(*) FROM runs")
                stats.calls = _scalar(conn, "SELECT COUNT(*) FROM model_calls")
                stats.diagnostics = _scalar(conn, "SELECT COUNT(*) FROM diagnostics")
                row = conn.execute("SELECT MAX(indexed_at) FROM tasks").fetchone()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Could not read index stats from %s: %s", self._db_path, exc)
            return stats
        stats.available = True
        stats.last_indexed_at = str(row[0]) if row and row[0] is not None else ""
        return stats

    def close(self) -> None:
        """Release resources.

        Connections are short-lived and already closed, so this exists only to
        make the intended lifecycle explicit for callers that manage the store
        in a context manager.
        """

    def _replay(self, path: Path, task: str, replay: ReplayFn) -> int:
        """Replay one task's audit log into the index.

        The task name is supplied by the caller rather than read from the
        record: the log lives inside ``tasks/<name>/``, so duplicating the name
        in every line would let the two disagree.

        Args:
            path: JSONL file to replay.
            task: Task the log belongs to.
            replay: Callable taking the task and one parsed record, returning
                ``True`` when the record was written.

        Returns:
            Number of rows written.
        """
        written = 0
        for record in _iter_jsonl(path):
            if isinstance(record, dict) and replay(task, record):
                written += 1
        return written

    def _replay_run(self, task: str, record: dict[str, Any]) -> bool:
        """Index one ``run_history.jsonl`` record.

        Args:
            task: Task the log belongs to.
            record: Parsed record.

        Returns:
            ``True`` when the record was complete enough to index.
        """
        if _missing_fields(record, _REQUIRED_RUN_FIELDS):
            logger.warning("Skipping incomplete run record for task %s: %s", task, record)
            return False
        self.record_run(
            task=task,
            step=str(record["step"]),
            status=str(record.get("status", "")),
            started_at=str(record["started_at"]),
            duration_ms=int(record.get("duration_ms") or 0),
            items=record.get("items") if isinstance(record.get("items"), int) else None,
            message=str(record.get("message", "")),
        )
        return True

    def _replay_call(self, task: str, record: dict[str, Any]) -> bool:
        """Index one ``model_calls.jsonl`` record.

        Args:
            task: Task the log belongs to.
            record: Parsed record.

        Returns:
            ``True`` when the record was complete enough to index.
        """
        if _missing_fields(record, _REQUIRED_CALL_FIELDS):
            logger.warning("Skipping incomplete call record for task %s: %s", task, record)
            return False
        self.record_model_call(
            task=task,
            role=str(record["role"]),
            provider=str(record.get("provider", "")),
            model=str(record.get("model", "")),
            base_url=str(record.get("host", "")),
            status=str(record.get("status", "")),
            http_status=record.get("http_status") if isinstance(record.get("http_status"), int) else None,
            duration_ms=int(record.get("duration_ms") or 0),
            started_at=str(record["started_at"]),
        )
        return True

    def _replay_diagnostics(self, path: Path) -> int:
        """Replay the diagnostics log into the index.

        Args:
            path: Path of ``diagnostics.jsonl``.

        Returns:
            Number of rows written.
        """
        written = 0
        for record in _iter_jsonl(path):
            if not isinstance(record, dict):
                continue
            generated_at = str(record.get("generated_at", ""))
            if not generated_at:
                continue
            self._execute(
                "INSERT OR IGNORE INTO diagnostics (generated_at, status, report_json) VALUES (?, ?, ?)",
                (generated_at, str(record.get("status", "")), json.dumps(record, ensure_ascii=False)),
            )
            written += 1
        return written

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        """Run one write statement, best-effort.

        A read-only directory, a corrupt file, or a locked database must never
        fail a pipeline step, so every failure is a warning.

        Args:
            sql: Statement to execute.
            params: Statement parameters.
        """
        if not self._enabled:
            return
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.execute(sql, params)
                conn.commit()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Metadata write to %s failed: %s", self._db_path, exc)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived, configured connection.

        A connection per operation keeps the store usable from the API's thread
        pool without sharing a handle across threads. WAL keeps a reader (the
        stats endpoint) from blocking a writer (a running pipeline).

        Args:
            (no arguments)

        Yields:
            An open connection with the pragmas applied.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()


def count_task_dir(task_dir: Path) -> TaskCounts:
    """Gather the indexed counts for one task directory.

    Shared by :meth:`MetadataStore.index_task` and :meth:`MetadataStore.rebuild`
    so the two cannot report different numbers for the same directory.

    Args:
        task_dir: The task's directory.

    Returns:
        A :class:`TaskCounts`; missing subdirectories count as zero.
    """
    return TaskCounts(
        image_count=len(list_images(task_dir / "images")),
        ai_label_count=_count_labels(task_dir / "ai_labels"),
        candidate_label_count=_count_labels(task_dir / "candidate_labels"),
        has_plan=(task_dir / "plan.yaml").is_file(),
        has_report=(task_dir / "inspection_report.yaml").is_file(),
        has_dataset=(task_dir / "dataset").is_dir(),
    )


def _count_labels(directory: Path) -> int:
    """Count YOLO label files in a directory.

    Empty files are counted: an image the agent found nothing in still produces
    a (zero-byte) label file, and that is a result worth indexing.

    Args:
        directory: Directory to scan.

    Returns:
        Number of ``*.txt`` files.
    """
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.glob("*.txt") if path.is_file())


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    """Run a ``COUNT(*)``-style query.

    Args:
        conn: Open connection.
        sql: Query returning a single integer.

    Returns:
        The value, or ``0`` when the row is missing.
    """
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _utc_now() -> str:
    """Return the current UTC timestamp in ISO 8601 form.

    Returns:
        Timestamp string with a ``Z`` suffix.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
