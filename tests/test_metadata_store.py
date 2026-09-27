"""Tests for src.core.metadata_store and the pipeline audit wiring.

The theme of these tests is the spec's central claim: the on-disk JSONL logs are
the truth and SQLite is a rebuildable projection, so every failure mode of the
database is required to be survivable.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from src.agents.model_client import host_of
from src.core.metadata_store import (
    DIAGNOSTICS_FILENAME,
    MODEL_CALLS_FILENAME,
    RUN_HISTORY_FILENAME,
    MetadataStore,
    RebuildResult,
    count_task_dir,
    read_task_history,
)
from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager

_RUN_LINE: dict[str, Any] = {
    "step": "plan",
    "status": "ok",
    "started_at": "2026-01-01T00:00:00+00:00",
    "finished_at": "2026-01-01T00:00:01+00:00",
    "duration_ms": 1000,
    "items": None,
    "message": "",
}

_CALL_LINE: dict[str, Any] = {
    "role": "llm",
    "provider": "remote",
    "model": "qwen2.5",
    "host": "http://127.0.0.1:8000",
    "status": "ok",
    "http_status": 200,
    "duration_ms": 250,
    "started_at": "2026-01-01T00:00:00+00:00",
}


def _append(path: Path, *records: dict[str, Any]) -> None:
    """Append records to a JSONL file, creating parents.

    Args:
        path: Target JSONL path.
        records: Records to serialize, one per line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file into a list of dicts.

    Args:
        path: JSONL file to read.

    Returns:
        Parsed lines in file order.
    """
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
def task_manager(tmp_path: Path) -> TaskManager:
    """TaskManager rooted in an isolated temp directory.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        A TaskManager over ``tmp_path/tasks``.
    """
    return TaskManager(tmp_path / "tasks")


@pytest.fixture
def store(tmp_path: Path) -> MetadataStore:
    """MetadataStore writing to an isolated index.db.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        A store whose database does not exist yet.
    """
    return MetadataStore(tmp_path / "index.db")


# --- host reduction -------------------------------------------------------


def test_host_of_strips_userinfo_port_and_path() -> None:
    """Credentials and paths never reach an audit log or an index row."""
    assert host_of("http://user:pass@host:11434/v1") == "http://host:11434"
    assert host_of("http://127.0.0.1:8000/v1/") == "http://127.0.0.1:8000"
    assert host_of("https://api.example.com/v1") == "https://api.example.com"


def test_host_of_is_idempotent_and_tolerant() -> None:
    """An already-reduced host survives a second pass; junk yields an empty string."""
    assert host_of("http://host:11434") == "http://host:11434"
    assert host_of("") == ""
    assert host_of("   ") == ""
    assert host_of("not-a-url") == ""
    assert host_of("http://host:abc") == ""


# --- reads never create state ---------------------------------------------


def test_absent_db_reads_as_an_empty_index(store: MetadataStore, tmp_path: Path) -> None:
    """A missing index.db is an empty index, and reading does not create it."""
    assert store.available() is True

    stats = store.stats()

    assert stats.available is True
    assert (stats.tasks, stats.runs, stats.calls, stats.diagnostics) == (0, 0, 0, 0)
    assert stats.db_size_bytes == 0
    assert not (tmp_path / "index.db").exists()


def test_disabled_store_writes_nothing(tmp_path: Path) -> None:
    """enabled=false disables the index entirely rather than degrading it."""
    db_path = tmp_path / "index.db"
    store = MetadataStore(db_path, enabled=False)

    assert store.available() is False
    store.record_run("demo", "plan", "ok", "2026-01-01T00:00:00+00:00", 1, None, "")
    store.upsert_task("demo", "desc", count_task_dir(tmp_path / "missing"))

    assert store.stats().available is False
    assert store.rebuild(TaskManager(tmp_path / "tasks"), tmp_path / "logs") == RebuildResult()
    assert not db_path.exists()


# --- writing --------------------------------------------------------------


def test_writes_are_indexed_and_counted(store: MetadataStore) -> None:
    """Steps, model calls, and task rows all land in the index."""
    store.record_run("demo", "plan", "ok", "2026-01-01T00:00:00+00:00", 12, None, "")
    store.record_model_call(
        "demo", "vl", "remote", "qwen2.5vl", "http://user:pw@host:11434/v1", "ok", 200, 800, "2026-01-01T00:00:00+00:00"
    )
    store.upsert_task("demo", "crack detection", count_task_dir(Path("/nonexistent")))

    stats = store.stats()

    assert (stats.tasks, stats.runs, stats.calls) == (1, 1, 1)
    assert stats.last_indexed_at


def test_duplicate_records_are_ignored(store: MetadataStore) -> None:
    """The UNIQUE keys make re-indexing the same record a no-op."""
    for _ in range(3):
        store.record_run("demo", "plan", "ok", "2026-01-01T00:00:00+00:00", 12, None, "")
        store.record_model_call(
            "demo", "llm", "stub", "", "", "stub", None, 1, "2026-01-01T00:00:00+00:00"
        )

    stats = store.stats()

    assert (stats.runs, stats.calls) == (1, 1)


def test_stored_host_has_no_credentials(store: MetadataStore, tmp_path: Path) -> None:
    """A credentialed base_url is reduced before storage."""
    store.record_model_call(
        "demo", "vl", "remote", "m", "http://user:pw@host:11434/v1", "ok", 200, 5, "2026-01-01T00:00:00+00:00"
    )

    with sqlite3.connect(tmp_path / "index.db") as conn:
        host = conn.execute("SELECT host FROM model_calls").fetchone()[0]

    assert host == "http://host:11434"
    assert "pw" not in host


# --- failure modes --------------------------------------------------------


def test_writes_degrade_when_db_cannot_be_opened(tmp_path: Path) -> None:
    """An unopenable database warns instead of failing the caller."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")
    store = MetadataStore(blocker / "index.db")

    store.record_run("demo", "plan", "ok", "2026-01-01T00:00:00+00:00", 1, None, "")
    store.upsert_task("demo", "", count_task_dir(tmp_path))

    assert store.stats().available is False


def test_corrupt_db_reports_unavailable(store: MetadataStore, tmp_path: Path) -> None:
    """A file that is not a SQLite database is reported, not raised."""
    (tmp_path / "index.db").write_bytes(b"this is not a database")

    assert store.available() is False
    assert store.stats().available is False


# --- rebuild --------------------------------------------------------------


def test_rebuild_restores_counts_from_jsonl(task_manager: TaskManager, tmp_path: Path) -> None:
    """Deleting the index loses nothing: rebuild reproduces every count."""
    task_dir = task_manager.create_task("demo", "crack detection")
    _append(task_dir / RUN_HISTORY_FILENAME, _RUN_LINE, {**_RUN_LINE, "step": "annotate"})
    _append(task_dir / MODEL_CALLS_FILENAME, _CALL_LINE)
    _append(tmp_path / "logs" / DIAGNOSTICS_FILENAME, {"generated_at": "2026-01-01T00:00:00+00:00", "status": "warn"})

    store = MetadataStore(tmp_path / "index.db")
    result = store.rebuild(task_manager, tmp_path / "logs")

    assert result == RebuildResult(tasks=1, runs=2, calls=1, diagnostics=1)
    stats = store.stats()
    assert (stats.tasks, stats.runs, stats.calls, stats.diagnostics) == (1, 2, 1, 1)
    assert stats.last_indexed_at

    (tmp_path / "index.db").unlink()
    assert store.rebuild(task_manager, tmp_path / "logs") == result
    assert store.stats().runs == 2


def test_rebuild_is_idempotent(task_manager: TaskManager, tmp_path: Path) -> None:
    """Running rebuild twice neither duplicates nor drops rows."""
    task_dir = task_manager.create_task("demo")
    _append(task_dir / RUN_HISTORY_FILENAME, _RUN_LINE)
    _append(task_dir / MODEL_CALLS_FILENAME, _CALL_LINE)
    store = MetadataStore(tmp_path / "index.db")

    first = store.rebuild(task_manager, tmp_path / "logs")
    second = store.rebuild(task_manager, tmp_path / "logs")

    assert first == second
    assert store.stats().runs == 1


def test_rebuild_drops_rows_whose_log_lines_are_gone(task_manager: TaskManager, tmp_path: Path) -> None:
    """Rebuild converges on the disk state even when the logs shrank.

    Without deleting the existing database first (acceptance 3 does, which is
    exactly what hid this), ``INSERT OR IGNORE`` keeps rows whose JSONL lines
    have since disappeared, so the index would report history the disk no longer
    has.
    """
    task_dir = task_manager.create_task("demo")
    run_log = task_dir / RUN_HISTORY_FILENAME
    _append(run_log, _RUN_LINE, {**_RUN_LINE, "step": "annotate"}, {**_RUN_LINE, "step": "inspect"})
    _append(task_dir / MODEL_CALLS_FILENAME, _CALL_LINE)
    store = MetadataStore(tmp_path / "index.db")
    assert store.rebuild(task_manager, tmp_path / "logs").runs == 3

    run_log.write_text(json.dumps(_RUN_LINE) + "\n", encoding="utf-8")
    (task_dir / MODEL_CALLS_FILENAME).unlink()

    result = store.rebuild(task_manager, tmp_path / "logs")

    assert result.runs == 1
    assert result.calls == 0
    stats = store.stats()
    assert (stats.runs, stats.calls) == (1, 0)
    assert len(read_task_history(task_dir, task="demo").runs) == 1


def test_rebuild_with_empty_tasks_root(task_manager: TaskManager, tmp_path: Path) -> None:
    """No tasks at all is a valid index, not an error."""
    store = MetadataStore(tmp_path / "index.db")

    assert store.rebuild(task_manager, tmp_path / "logs") == RebuildResult()
    assert store.stats().available is True


def test_rebuild_skips_malformed_and_incomplete_lines(task_manager: TaskManager, tmp_path: Path) -> None:
    """One corrupt line cannot hide the rest of the file."""
    task_dir = task_manager.create_task("demo")
    run_log = task_dir / RUN_HISTORY_FILENAME
    _append(run_log, _RUN_LINE)
    with run_log.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps({**_RUN_LINE, "step": ""}) + "\n")  # incomplete: no step

    store = MetadataStore(tmp_path / "index.db")
    result = store.rebuild(task_manager, tmp_path / "logs")

    assert result.runs == 1
    assert [entry.step for entry in read_task_history(task_dir, task="demo").runs] == ["plan"]


# --- disk history ---------------------------------------------------------


def test_history_of_task_without_logs_is_empty(task_manager: TaskManager) -> None:
    """An old task that never ran has an empty history, not a 404."""
    task_dir = task_manager.create_task("demo")

    history = read_task_history(task_dir, task="demo")

    assert history.task == "demo"
    assert history.runs == [] and history.calls == []
    assert history.source == "jsonl"


def test_history_is_newest_first_and_limited(task_manager: TaskManager) -> None:
    """Entries come back newest first, honoring the limit."""
    task_dir = task_manager.create_task("demo")
    _append(
        task_dir / RUN_HISTORY_FILENAME,
        *[{**_RUN_LINE, "step": f"s{i}", "started_at": f"2026-01-0{i + 1}T00:00:00+00:00"} for i in range(5)],
    )

    history = read_task_history(task_dir, task="demo", limit=2)

    assert [entry.step for entry in history.runs] == ["s4", "s3"]


def test_count_task_dir_reports_disk_state(task_manager: TaskManager, make_image) -> None:
    """Counts and flags come from what is on disk."""
    task_dir = task_manager.create_task("demo")
    shutil.copy2(make_image("a.png"), task_dir / "images" / "a.png")
    (task_dir / "ai_labels" / "a.txt").write_text("0 0.5 0.5 0.6 0.5 0.6 0.4 0.5 0.4\n", encoding="utf-8")
    (task_dir / "candidate_labels" / "a.txt").write_text("0 0.5 0.5 0.6 0.5 0.6 0.4 0.5 0.4\n", encoding="utf-8")
    (task_dir / "plan.yaml").write_text("classes: {0: crack}\n", encoding="utf-8")

    counts = count_task_dir(task_dir)

    assert counts.image_count == 1
    assert counts.ai_label_count == 1
    assert counts.candidate_label_count == 1
    assert counts.has_plan is True
    assert counts.has_report is False
    assert counts.has_dataset is False


# --- pipeline wiring ------------------------------------------------------


def _task_with_image(task_manager: TaskManager, name: str, image: Path) -> Path:
    """Create a task containing one image.

    Args:
        task_manager: Manager to create the task in.
        name: Task name.
        image: Generated image to copy into ``images/``.

    Returns:
        The task directory.
    """
    task_dir = task_manager.create_task(name, "crack detection")
    shutil.copy2(image, task_dir / "images" / image.name)
    return task_dir


def test_run_full_audits_every_step(task_manager: TaskManager, make_image) -> None:
    """Each step appends one history line with its step-specific count."""
    task_dir = _task_with_image(task_manager, "demo", make_image("a.png"))
    Pipeline(task_manager).run_full("demo")

    lines = _read_lines(task_dir / RUN_HISTORY_FILENAME)

    assert [line["step"] for line in lines] == ["plan", "annotate", "inspect", "split"]
    assert {line["status"] for line in lines} == {"ok"}
    assert lines[1]["items"] == 1  # one label written
    assert lines[2]["items"] == 0  # no inspection issues found
    assert lines[0]["items"] is None  # plan has no meaningful count
    assert all(line["duration_ms"] >= 0 and line["finished_at"] for line in lines)


def test_stub_calls_are_recorded_as_stub(task_manager: TaskManager, make_image) -> None:
    """The audit log can tell synthetic output from model output."""
    task_dir = _task_with_image(task_manager, "demo", make_image("a.png"))
    Pipeline(task_manager).run_full("demo")

    calls = _read_lines(task_dir / MODEL_CALLS_FILENAME)

    # Only the plan step calls a client; annotate's inline stub never does, and
    # inventing a record for it would claim a call that never happened.
    assert len(calls) == 1
    assert calls[0]["role"] == "llm"
    assert calls[0]["provider"] == "stub"
    assert calls[0]["status"] == "stub"
    assert calls[0]["host"] == ""
    assert "api_key" not in calls[0]


def test_pipeline_mirrors_audit_into_index(task_manager: TaskManager, tmp_path: Path, make_image) -> None:
    """With a store attached, the same run also lands in SQLite."""
    task_dir = _task_with_image(task_manager, "demo", make_image("a.png"))
    store = MetadataStore(tmp_path / "index.db")

    Pipeline(task_manager, metadata_store=store).run_full("demo")

    stats = store.stats()
    assert (stats.tasks, stats.runs, stats.calls) == (1, 1 + 3, 1)
    assert stats.last_indexed_at
    with sqlite3.connect(tmp_path / "index.db") as conn:
        description = conn.execute("SELECT description FROM tasks").fetchone()[0]
    assert description == "crack detection"
    assert (task_dir / RUN_HISTORY_FILENAME).is_file()


def test_failed_step_is_audited_and_reraised(
    task_manager: TaskManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing step keeps its original exception and records an error line."""
    task_dir = task_manager.create_task("demo")
    pipeline = Pipeline(task_manager)

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(pipeline.agents["plan"], "run", _boom)

    with pytest.raises(RuntimeError, match="model exploded"):
        pipeline.run_step("demo", "plan")

    lines = _read_lines(task_dir / RUN_HISTORY_FILENAME)
    assert len(lines) == 1
    assert lines[0]["status"] == "error"
    assert "model exploded" in lines[0]["message"]


def test_audit_failure_does_not_fail_the_step(
    task_manager: TaskManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unwritable audit log degrades to a warning, never to a failed step."""
    task_manager.create_task("demo")
    monkeypatch.setattr("src.core.pipeline.append_record", lambda *args, **kwargs: False)
    pipeline = Pipeline(task_manager)

    result = pipeline.run_step("demo", "plan")

    assert result["classes"]


def test_history_reads_disk_without_any_index(task_manager: TaskManager, make_image) -> None:
    """History works with no store in the picture at all."""
    task_dir = _task_with_image(task_manager, "demo", make_image("a.png"))
    Pipeline(task_manager).run_full("demo")

    history = read_task_history(task_dir, task="demo")

    assert [entry.step for entry in history.runs][0] == "split"
    assert history.calls[0].status == "stub"
