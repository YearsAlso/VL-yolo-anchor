"""Tests for src.core.pipeline (stub agents, isolated task root)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from src.core.pipeline import Pipeline, normalize_plan
from src.core.task_manager import TaskManager


@pytest.fixture
def pipeline(tmp_path: Path) -> Pipeline:
    """Pipeline bound to an isolated tasks root under tmp_path.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Pipeline using TaskManager(tmp_path).
    """
    return Pipeline(TaskManager(tmp_path / "tasks"))


def _make_task(pipeline: Pipeline, name: str, image: Path) -> Path:
    """Create a task and copy one image into its images/ directory.

    Args:
        pipeline: Pipeline fixture.
        name: Task name.
        image: Generated test image to copy.

    Returns:
        The task directory path.
    """
    task_dir = pipeline.task_manager.create_task(name)
    shutil.copy2(image, task_dir / "images" / image.name)
    return task_dir


def test_run_unknown_step_raises(pipeline: Pipeline) -> None:
    """run_step rejects step names outside plan/annotate/inspect/split."""
    with pytest.raises(ValueError, match="Unknown step"):
        pipeline.run_step("any", "bogus")


def test_run_step_missing_task_raises(pipeline: Pipeline) -> None:
    """run_step on a nonexistent task raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        pipeline.run_step("ghost", "plan")


def test_run_full_pipeline(tmp_path: Path, pipeline: Pipeline, make_image) -> None:
    """The full stub pipeline produces labels, report, and a trainable export."""
    image = make_image("img_01.png")
    task_dir = _make_task(pipeline, "demo", image)

    results: dict[str, Any] = pipeline.run_full("demo")

    assert set(results) == {"plan", "annotate", "inspect", "split"}

    # plan: normalized schema written to plan.yaml and merged into config
    plan = results["plan"]
    for key in ("classes", "conf_threshold", "obb_rule", "split", "hyperparameters", "inspection"):
        assert key in plan
    saved_plan = yaml.safe_load((task_dir / "plan.yaml").read_text(encoding="utf-8"))
    assert saved_plan["classes"] == plan["classes"]

    # annotate: one label file per image in ai_labels/
    labels = list((task_dir / "ai_labels").glob("*.txt"))
    assert len(labels) == 1
    lines = labels[0].read_text(encoding="utf-8").split()
    assert len(lines) == 9  # cls + 8 normalized coords

    # inspect: report written with its finding sections
    report_path = task_dir / "inspection_report.yaml"
    assert report_path.is_file()
    report = results["inspect"]
    assert isinstance(report, dict) and report

    # split: dataset package with data.yaml, train_command.txt, summary
    dataset = task_dir / "dataset"
    counts = results["split"]["splits"]
    assert sum(counts.values()) == 1
    assert any(counts.values())
    exported = list(dataset.glob("*/images/img_01.png"))
    assert len(exported) == 1
    assert (dataset / "data.yaml").is_file()
    names = yaml.safe_load((dataset / "data.yaml").read_text(encoding="utf-8"))["names"]
    assert set(names.values()) == set(plan["classes"].values())
    command = (dataset / "train_command.txt").read_text(encoding="utf-8")
    assert "yolo obb train" in command
    assert "data=" in command


def test_split_without_images_returns_empty_summary(pipeline: Pipeline) -> None:
    """split on an image-less task returns an empty summary without crashing."""
    pipeline.task_manager.create_task("empty")
    summary = pipeline.run_step("empty", "split")
    assert summary == {"task": "empty", "splits": {}, "dataset_yaml": "", "train_command": ""}


def test_normalize_plan_defaults() -> None:
    """normalize_plan fills defaults for a minimal raw plan."""
    plan = normalize_plan({"classes": {"0": "crack"}, "annotation_rules": {}})
    assert plan["classes"] == {0: "crack"}
    assert plan["min_defect_pixels"] == 16
    assert plan["conf_threshold"] == 0.25
    assert plan["obb_rule"] == "clockwise_normalized"
    assert plan["split"] == {
        "train_ratio": 0.7,
        "val_ratio": 0.2,
        "test_ratio": 0.1,
        "stratified": True,
    }
