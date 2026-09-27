"""Tests for src.agents.inspect_agent (error branches + candidate corrections)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from src.agents.inspect_agent import InspectAgent
from src.utils.obb_utils import rotated_box_to_four_points

_CONFIG: dict[str, Any] = {
    "training_plan": {
        "classes": {0: "crack"},
        "inspection": {"iou_duplicate_threshold": 0.7, "size_outlier_ratio": 0.1},
    }
}


def _fmt(cls: int, points: list[float]) -> str:
    """Format one YOLO-OBB label line with 6-decimal coords."""
    return f"{cls} " + " ".join(f"{v:.6f}" for v in points)


def _rect(cx: float, cy: float, w: float, h: float, angle: float = 0.0) -> str:
    """Build a label line from a normalized rotated rectangle."""
    return _fmt(0, rotated_box_to_four_points(cx, cy, w, h, angle))


def _make_task(tmp_path: Path, name: str, labels: dict[str, list[str]]) -> Path:
    """Create a bare task dir with 100x80 images and the given ai_labels.

    Args:
        tmp_path: Pytest temporary directory root.
        name: Task directory name.
        labels: Mapping of image stem to label lines written verbatim.

    Returns:
        The task directory path.
    """
    task_dir = tmp_path / name
    images_dir = task_dir / "images"
    labels_dir = task_dir / "ai_labels"
    images_dir.mkdir(parents=True)
    labels_dir.mkdir(parents=True)
    img = np.zeros((80, 100, 3), dtype=np.uint8)
    for stem, lines in labels.items():
        cv2.imwrite(str(images_dir / f"{stem}.png"), img)
        (labels_dir / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return task_dir


@pytest.fixture
def agent() -> InspectAgent:
    """InspectAgent with a dummy prompt dir (run() never loads prompts)."""
    return InspectAgent(_CONFIG, Path("prompts"))


def test_run_missing_task_dir_raises(agent: InspectAgent, tmp_path: Path) -> None:
    """run() on a nonexistent task directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Task directory not found"):
        agent.run(tmp_path / "ghost", _CONFIG)


def test_missing_label_reported(agent: InspectAgent, tmp_path: Path) -> None:
    """An image without a label file lands in errors.missing_labels."""
    task_dir = tmp_path / "t"
    (task_dir / "images").mkdir(parents=True)
    (task_dir / "ai_labels").mkdir()
    cv2.imwrite(str(task_dir / "images" / "solo.png"), np.zeros((80, 100, 3), dtype=np.uint8))
    report = agent.run(task_dir, _CONFIG)
    assert report["errors"]["missing_labels"] == [{"image": "solo.png", "detail": "no label file"}]
    assert report["num_boxes"] == 0
    assert report["quality_score"] == 0.0  # 1 - 1/max(0,1)


def test_duplicate_boxes_reported_and_dropped(agent: InspectAgent, tmp_path: Path) -> None:
    """Two overlapping boxes are reported; the candidate file keeps one."""
    task_dir = _make_task(tmp_path, "dup", {"img": [_rect(0.5, 0.5, 0.4, 0.2), _rect(0.5, 0.5, 0.4, 0.2)]})
    report = agent.run(task_dir, _CONFIG)
    dups = report["errors"]["duplicate_boxes"]
    assert len(dups) == 1
    assert dups[0]["bbox_index"] == [0, 1]
    assert "remove box 1" in dups[0]["suggestion"]
    candidate = (task_dir / "candidate_labels" / "img.txt").read_text(encoding="utf-8").splitlines()
    assert len(candidate) == 1


def test_class_error_reported(agent: InspectAgent, tmp_path: Path) -> None:
    """A box whose class is outside the plan is reported as class_error."""
    task_dir = _make_task(tmp_path, "cls", {"img": [_fmt(7, rotated_box_to_four_points(0.5, 0.5, 0.4, 0.2, 0.0))]})
    report = agent.run(task_dir, _CONFIG)
    errors = report["errors"]["class_error"]
    assert any(e.get("bbox_index") == 0 and "class 7" in str(e.get("detail")) for e in errors)


def test_out_of_range_coords_reported_and_dropped(agent: InspectAgent, tmp_path: Path) -> None:
    """Normalized coords > 1 are reported and excluded from candidates."""
    bad = [0.5, 0.5, 1.5, 0.5, 1.5, 0.9, 0.5, 0.9]
    task_dir = _make_task(tmp_path, "oor", {"img": [_fmt(0, bad), _rect(0.25, 0.25, 0.2, 0.1)]})
    report = agent.run(task_dir, _CONFIG)
    oor = report["errors"]["coords_out_of_range"]
    assert len(oor) == 1
    assert oor[0]["bbox_index"] == 0
    candidate = (task_dir / "candidate_labels" / "img.txt").read_text(encoding="utf-8").splitlines()
    assert len(candidate) == 1
    assert all(v <= 1.0 for v in (float(x) for x in candidate[0].split()[1:]))


def test_size_anomaly_flags_area_outlier(agent: InspectAgent, tmp_path: Path) -> None:
    """A box >3 sigma from the mean area is flagged as size_anomaly.

    With N uniform boxes plus one outlier the z-score caps at (N-1)/sqrt(N),
    so 12 uniform small boxes are needed before 3 sigma is reachable.
    """
    lines = [_rect(0.1 + 0.08 * (i % 4), 0.15 + 0.2 * (i // 4), 0.05, 0.05) for i in range(12)]
    lines.append(_rect(0.5, 0.5, 0.7, 0.7))
    task_dir = _make_task(tmp_path, "size", {"img": lines})
    report = agent.run(task_dir, _CONFIG)
    anomalies = report["errors"]["size_anomaly"]
    assert [a["bbox_index"] for a in anomalies] == [12]
    assert report["errors"]["duplicate_boxes"] == []


def test_malformed_lines_reported(agent: InspectAgent, tmp_path: Path) -> None:
    """Unparseable label lines are recorded as per-line parse errors."""
    task_dir = _make_task(tmp_path, "mfs", {"img": ["0 0.1 0.2", _rect(0.5, 0.5, 0.4, 0.2)]})
    report = agent.run(task_dir, _CONFIG)
    errors = report["errors"]["class_error"]
    assert any(e.get("line") == 1 for e in errors)
    assert report["num_boxes"] == 1


def test_original_labels_never_modified(agent: InspectAgent, tmp_path: Path) -> None:
    """Hard constraint: inspect writes only candidate_labels/, ai_labels stays intact."""
    lines = [_rect(0.5, 0.5, 0.4, 0.2), _rect(0.5, 0.5, 0.4, 0.2)]
    task_dir = _make_task(tmp_path, "ro", {"img": lines})
    original = task_dir / "ai_labels" / "img.txt"
    before = original.read_bytes()
    agent.run(task_dir, _CONFIG)
    assert original.read_bytes() == before
    assert (task_dir / "candidate_labels" / "img.txt").is_file()
