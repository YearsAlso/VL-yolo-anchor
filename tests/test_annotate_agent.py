"""Tests for src.agents.annotate_agent detection filtering (stub VL off-path)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from src.agents.annotate_agent import AnnotateAgent
from src.utils.obb_utils import rotated_box_to_four_points

_CONFIG: dict[str, Any] = {"training_plan": {"classes": {0: "crack", 1: "scratch"}}}


@pytest.fixture
def agent() -> AnnotateAgent:
    """AnnotateAgent with a two-class plan; prompts are unused by the converter."""
    a = AnnotateAgent(_CONFIG, Path("prompts"))
    a.config = _CONFIG
    return a


def _det(cls: int = 0, obb: list[float] | None = None, conf: float = 0.9) -> dict[str, Any]:
    """Build a detection dict with a normalized mid-image box by default."""
    return {"cls": cls, "obb": obb or rotated_box_to_four_points(0.5, 0.5, 0.3, 0.2, 0.0), "conf": conf}


def test_valid_detection_normalized(agent: AnnotateAgent) -> None:
    """A passing detection becomes one 9-token line with 6-decimal coords."""
    lines = agent._detections_to_yolo_lines([_det()], 100, 80, conf_threshold=0.25, min_pixels=16)
    assert len(lines) == 1
    parts = lines[0].split()
    assert parts[0] == "0" and len(parts) == 9
    assert all(len(v.split(".")[1]) == 6 for v in parts[1:])


def test_malformed_detection_skipped(agent: AnnotateAgent) -> None:
    """Detections missing keys or with non-numeric fields are dropped."""
    bad = [{"cls": 0}, {"obb": [0.0] * 8, "conf": "high"}, 42]
    lines = agent._detections_to_yolo_lines(bad + [_det()], 100, 80, 0.25, 16)  # type: ignore[list-item]
    assert len(lines) == 1


def test_unknown_class_skipped(agent: AnnotateAgent) -> None:
    """Class ids outside the task plan are rejected (hard constraint 3)."""
    lines = agent._detections_to_yolo_lines([_det(cls=7)], 100, 80, 0.25, 16)
    assert lines == []


def test_low_confidence_skipped(agent: AnnotateAgent) -> None:
    """Detections below conf_threshold never reach the label file."""
    assert agent._detections_to_yolo_lines([_det(conf=0.2)], 100, 80, 0.25, 16) == []
    assert len(agent._detections_to_yolo_lines([_det(conf=0.25)], 100, 80, 0.25, 16)) == 1


def test_tiny_defect_below_min_pixels_skipped(agent: AnnotateAgent) -> None:
    """Normalized boxes whose pixel area is under min_defect_pixels are dropped."""
    tiny = rotated_box_to_four_points(0.5, 0.5, 0.05, 0.04, 0.0)  # 5x3.2 px -> 16 px^2
    assert agent._detections_to_yolo_lines([_det(obb=tiny)], 100, 80, 0.25, min_pixels=20) == []
    assert len(agent._detections_to_yolo_lines([_det(obb=tiny)], 100, 80, 0.25, min_pixels=15)) == 1


def test_absolute_pixel_output_supported(agent: AnnotateAgent) -> None:
    """Coords with max > 1 are treated as pixels and re-normalized."""
    pixel_obb = rotated_box_to_four_points(50.0, 40.0, 30.0, 20.0, 0.0)
    lines = agent._detections_to_yolo_lines([_det(obb=pixel_obb)], 100, 80, 0.25, 16)
    assert len(lines) == 1
    coords = [float(v) for v in lines[0].split()[1:]]
    assert max(coords) <= 1.0
    assert min(coords) >= 0.0


def test_run_missing_task_dir_raises(agent: AnnotateAgent, tmp_path: Path) -> None:
    """run() on a nonexistent task directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Task directory not found"):
        agent.run(tmp_path / "ghost", _CONFIG)
