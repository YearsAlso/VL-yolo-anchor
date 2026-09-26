"""Tests for src.core.task_manager."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.core.task_manager import TaskManager


def test_create_task_scaffolds_directories(tmp_path: Path) -> None:
    """create_task makes the task dir, subdirs, and a seeded task.yaml."""
    tm = TaskManager(tmp_path)
    task_dir = tm.create_task("demo", "desc")
    assert task_dir == tmp_path / "demo"
    for sub in ("images", "ai_labels", "candidate_labels"):
        assert (task_dir / sub).is_dir()
    config = tm.load_task("demo")
    assert config["task"]["name"] == "demo"
    assert config["task"]["description"] == "desc"


def test_create_duplicate_task_raises(tmp_path: Path) -> None:
    """Creating an existing task raises FileExistsError."""
    tm = TaskManager(tmp_path)
    tm.create_task("demo")
    with pytest.raises(FileExistsError):
        tm.create_task("demo")


def test_list_tasks_sorted(tmp_path: Path) -> None:
    """list_tasks returns sorted task names only (files ignored)."""
    tm = TaskManager(tmp_path)
    tm.create_task("b")
    tm.create_task("a")
    (tmp_path / "not_a_dir.txt").write_text("x", encoding="utf-8")
    assert tm.list_tasks() == ["a", "b"]


def test_save_and_load_task_config(tmp_path: Path) -> None:
    """save_task_config persists and load_task reads back the mapping."""
    tm = TaskManager(tmp_path)
    tm.create_task("demo")
    config = tm.load_task("demo")
    config["extra"] = {"v": 1}
    tm.save_task_config("demo", config)
    assert tm.load_task("demo")["extra"] == {"v": 1}


def test_load_task_missing(tmp_path: Path) -> None:
    """load_task on an unknown task raises FileNotFoundError."""
    tm = TaskManager(tmp_path)
    with pytest.raises(FileNotFoundError):
        tm.load_task("ghost")


def test_delete_task(tmp_path: Path) -> None:
    """delete_task removes the task tree and it disappears from listings."""
    tm = TaskManager(tmp_path)
    tm.create_task("demo")
    tm.delete_task("demo")
    assert tm.list_tasks() == []
    with pytest.raises(FileNotFoundError):
        tm.delete_task("demo")
