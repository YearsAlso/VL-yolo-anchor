"""Task lifecycle management: create, list, load, and locate tasks."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from src.utils.file_utils import ensure_dir
from src.utils.yaml_utils import load_yaml, save_yaml


class TaskManager:
    """Manages per-task directories and configuration under a tasks root.

    Each task lives in ``<tasks_root>/<name>/`` with::

        task.yaml           task config (merged task_template + plan)
        images/             input images
        ai_labels/          agent-generated YOLO-OBB labels
        candidate_labels/   inspection candidate corrections
        dataset/            exported train-ready dataset (after split)

    Attributes:
        tasks_root: Root directory containing all task folders.
        logger: Logger for task lifecycle events.
    """

    def __init__(self, tasks_root: Path) -> None:
        """Initialize the manager.

        Args:
            tasks_root: Directory that contains (or will contain) task dirs.
        """
        self.tasks_root: Path = tasks_root
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)
        ensure_dir(tasks_root)

    def task_dir(self, name: str) -> Path:
        """Return the directory for a task.

        Args:
            name: Task name.

        Returns:
            Path ``<tasks_root>/<name>`` (created if missing).
        """
        return ensure_dir(self.tasks_root / name)

    def create_task(self, name: str, description: str = "") -> Path:
        """Create a new task directory with scaffolded subdirectories.

        The task config is seeded from ``config/task_template.yaml`` when it
        exists; otherwise a minimal default is used.

        Args:
            name: Unique task name (also used as directory name).
            description: Free-form natural-language task description.

        Returns:
            Path to the created task directory.

        Raises:
            FileExistsError: If a task with the same name already exists.
        """
        task_dir = self.tasks_root / name
        if task_dir.exists():
            raise FileExistsError(f"Task already exists: {name}")
        ensure_dir(task_dir)
        ensure_dir(task_dir / "images")
        ensure_dir(task_dir / "ai_labels")
        ensure_dir(task_dir / "candidate_labels")
        config = self._template_config()
        config.setdefault("task", {})
        config["task"]["name"] = name
        config["task"]["description"] = description
        save_yaml(config, task_dir / "task.yaml")
        self.logger.info("Created task '%s' at %s", name, task_dir)
        return task_dir

    def list_tasks(self) -> list[str]:
        """List all task names.

        Returns:
            Sorted list of directory names under ``tasks_root``.
        """
        if not self.tasks_root.is_dir():
            return []
        return sorted(p.name for p in self.tasks_root.iterdir() if p.is_dir())

    def load_task(self, name: str) -> dict[str, Any]:
        """Load a task's configuration.

        Args:
            name: Task name.

        Returns:
            Parsed ``task.yaml`` content.

        Raises:
            FileNotFoundError: If the task or its ``task.yaml`` is missing.
        """
        config_path = self.task_dir(name) / "task.yaml"
        if not config_path.is_file():
            raise FileNotFoundError(f"Task config not found: {config_path}")
        return load_yaml(config_path)

    def save_task_config(self, name: str, config: dict[str, Any]) -> Path:
        """Persist a task configuration.

        Args:
            name: Task name.
            config: Full task config mapping to write.

        Returns:
            Path of the written ``task.yaml``.
        """
        config_path = self.task_dir(name) / "task.yaml"
        save_yaml(config, config_path)
        self.logger.debug("Saved task config %s", config_path)
        return config_path

    def delete_task(self, name: str) -> None:
        """Delete a task directory tree.

        Args:
            name: Task name.

        Raises:
            FileNotFoundError: If the task does not exist.
        """
        task_dir = self.tasks_root / name
        if not task_dir.is_dir():
            raise FileNotFoundError(f"Task not found: {name}")
        shutil.rmtree(task_dir)
        self.logger.info("Deleted task '%s'", name)

    def _template_config(self) -> dict[str, Any]:
        """Load the task template configuration.

        Returns:
            Template mapping, or a minimal default when the template file is
            missing.
        """
        template_path = Path("config") / "task_template.yaml"
        if template_path.is_file():
            return load_yaml(template_path)
        return {"task": {}, "training_plan": {}}
