"""InspectAgent: validates annotation files and writes candidate corrections.

All findings are reported as-is; fixes are never applied to the original
labels — candidate corrections go to ``task_dir/candidate_labels/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from src.agents.base_agent import BaseAgent
from src.utils.file_utils import ensure_dir, list_images
from src.utils.image_utils import load_image
from src.utils.obb_utils import OBBBox, is_coords_in_range, obb_iou


def _polygon_area(points: list[float] | np.ndarray) -> float:
    """Compute polygon area via the shoelace formula.

    Args:
        points: Flat coordinate list or ``(N, 2)`` array of corner points.

    Returns:
        Absolute polygon area in pixels squared.
    """
    arr = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    x, y = arr[:, 0], arr[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))


class InspectAgent(BaseAgent):
    """Agent that checks AI labels for common annotation errors."""

    def run(self, task_dir: Path, task_config: dict[str, Any]) -> dict[str, Any]:
        """Inspect all AI label files in ``task_dir/ai_labels``.

        Checks performed (driven by ``training_plan.inspection`` flags):
        missing labels, duplicate boxes (OBB IoU above threshold), class
        errors (unknown class ids), out-of-range coordinates, and size
        anomalies. Corrected label files are written to
        ``task_dir/candidate_labels/`` — originals are never modified.

        Args:
            task_dir: Root directory of the task.
            task_config: Task configuration dict with ``training_plan``.

        Returns:
            Report dict with ``num_images``, ``num_boxes``, ``errors`` keyed
            by error type, ``quality_score``, and ``candidate_dir``.

        Raises:
            FileNotFoundError: If the task directory does not exist.
        """
        if not task_dir.is_dir():
            raise FileNotFoundError(f"Task directory not found: {task_dir}")

        plan = task_config.get("training_plan", {})
        inspection = plan.get("inspection", {})
        allowed_classes = {int(k) for k in plan.get("classes", {})}
        iou_threshold = float(inspection.get("iou_duplicate_threshold", 0.7))

        images_dir = task_dir / "images"
        labels_dir = task_dir / "ai_labels"
        candidate_dir = ensure_dir(task_dir / "candidate_labels")

        report: dict[str, Any] = {
            "task": task_dir.name,
            "num_images": 0,
            "num_boxes": 0,
            "errors": {
                "missing_labels": [],
                "duplicate_boxes": [],
                "class_error": [],
                "coords_out_of_range": [],
                "size_anomaly": [],
            },
            "quality_score": 1.0,
            "candidate_dir": str(candidate_dir),
        }
        all_areas: list[tuple[str, int, float]] = []

        for image_path in list_images(images_dir):
            report["num_images"] += 1
            label_path = labels_dir / f"{image_path.stem}.txt"
            if not label_path.is_file():
                report["errors"]["missing_labels"].append({"image": image_path.name, "detail": "no label file"})
                continue
            h, w = load_image(image_path).shape[:2]
            boxes, parse_errors = self._parse_label_file(label_path)
            report["num_boxes"] += len(boxes)
            report["errors"]["class_error"].extend({"image": image_path.name, **err} for err in parse_errors)
            self._check_boxes(image_path.name, boxes, (w, h), allowed_classes, iou_threshold, report, all_areas)
            self._write_candidates(label_path, candidate_dir, boxes, iou_threshold)

        self._flag_size_outliers(report, all_areas)
        total_errors = sum(len(v) for v in report["errors"].values())
        report["quality_score"] = round(max(0.0, 1.0 - total_errors / max(report["num_boxes"], 1)), 4)
        self.logger.info(
            "Inspected %d images, %d boxes, %d errors, score %.3f",
            report["num_images"],
            report["num_boxes"],
            total_errors,
            report["quality_score"],
        )
        return report

    def _check_boxes(
        self,
        image_name: str,
        boxes: list[OBBBox],
        image_size: tuple[int, int],
        allowed_classes: set[int],
        iou_threshold: float,
        report: dict[str, Any],
        all_areas: list[tuple[str, int, float]],
    ) -> None:
        """Run all box-level checks for one image and record findings.

        Args:
            image_name: Image file name for error entries.
            boxes: Parsed boxes from the label file (normalized coordinates).
            image_size: Image ``(width, height)`` in pixels.
            allowed_classes: Valid class ids for this task.
            iou_threshold: OBB IoU above which boxes count as duplicates.
            report: Report dict to append errors to.
            all_areas: Accumulator of ``(image, index, pixel area)`` tuples
                for the dataset-wide size-anomaly check.
        """
        w, h = image_size
        for idx, box in enumerate(boxes):
            if box.cls not in allowed_classes:
                report["errors"]["class_error"].append(
                    {"image": image_name, "bbox_index": idx, "detail": f"class {box.cls} not in allowed set"}
                )
            if not is_coords_in_range(box.points):
                report["errors"]["coords_out_of_range"].append(
                    {"image": image_name, "bbox_index": idx, "detail": f"points {box.points}"}
                )
            all_areas.append((image_name, idx, _polygon_area(box.points) * float(w) * float(h)))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                iou = obb_iou(boxes[i], boxes[j])
                if iou > iou_threshold:
                    report["errors"]["duplicate_boxes"].append(
                        {
                            "image": image_name,
                            "bbox_index": [i, j],
                            "detail": f"IoU={iou:.3f} > {iou_threshold}",
                            "suggestion": f"remove box {j}",
                        }
                    )

    def _flag_size_outliers(self, report: dict[str, Any], all_areas: list[tuple[str, int, float]]) -> None:
        """Flag boxes whose area deviates strongly from the dataset mean.

        Args:
            report: Report dict to append errors to.
            all_areas: ``(image, index, pixel area)`` tuples across the dataset.
        """
        if len(all_areas) < 2:
            return
        areas = np.asarray([a for _, _, a in all_areas], dtype=np.float64)
        mean_area = float(areas.mean())
        std_area = float(areas.std())
        if std_area <= 0.0:
            return
        outliers = [
            {"image": img, "bbox_index": idx, "detail": f"area {area:.0f} px^2, mean {mean_area:.0f}"}
            for img, idx, area in all_areas
            if abs(area - mean_area) > 3.0 * std_area
        ]
        report["errors"]["size_anomaly"].extend(outliers)

    def _parse_label_file(self, label_path: Path) -> tuple[list[OBBBox], list[dict[str, Any]]]:
        """Parse a YOLO-OBB label file into boxes.

        Args:
            label_path: Path to the ``.txt`` label file.

        Returns:
            Tuple of (valid boxes, per-line parse errors).
        """
        boxes: list[OBBBox] = []
        errors: list[dict[str, Any]] = []
        for line_no, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
            parts = line.split()
            if not parts:
                continue
            try:
                cls = int(parts[0])
                points = [float(v) for v in parts[1:9]]
                if len(points) != 8:
                    raise ValueError(f"expected 8 coordinates, got {len(parts) - 1}")
            except ValueError as exc:
                errors.append({"line": line_no, "detail": str(exc)})
                continue
            boxes.append(OBBBox(points=points, cls=cls, conf=1.0))
        return boxes, errors

    def _write_candidates(
        self, label_path: Path, candidate_dir: Path, boxes: list[OBBBox], iou_threshold: float
    ) -> None:
        """Write a candidate correction file for one image.

        Candidates drop duplicate and out-of-range boxes; the original label
        files are never modified.

        Args:
            label_path: Original label file path (read-only reference).
            candidate_dir: Directory to write candidate labels into.
            boxes: Parsed boxes for the image.
            iou_threshold: OBB IoU above which a box is considered a duplicate.
        """
        kept: list[OBBBox] = []
        for box in boxes:
            if not is_coords_in_range(box.points):
                continue
            if any(obb_iou(box, other) > iou_threshold for other in kept):
                continue
            kept.append(box)
        candidate_path = candidate_dir / label_path.name
        text = "\n".join(f"{b.cls} " + " ".join(f"{v:.6f}" for v in b.points) for b in kept)
        candidate_path.write_text(text + ("\n" if kept else ""), encoding="utf-8")
