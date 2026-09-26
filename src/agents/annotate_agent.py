"""AnnotateAgent: auto-labels images with YOLO-OBB boxes using a local VL model.

The current implementation ships a deterministic stub VL inference so the
pipeline runs end-to-end offline; the real Qwen2.5-VL backend plugs in behind
the same prompt-rendering + JSON-parsing interface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from src.agents.base_agent import BaseAgent
from src.utils.file_utils import ensure_dir, list_images
from src.utils.image_utils import load_image
from src.utils.obb_utils import denormalize_points, normalize_points, rotated_box_to_four_points


class AnnotateAgent(BaseAgent):
    """Agent that produces YOLO-OBB label files for all images in a task."""

    def run(self, task_dir: Path, task_config: dict[str, Any]) -> list[Path]:
        """Annotate every image in ``task_dir/images``.

        For each image the agent renders the external prompt
        (``prompts/annotate_agent.yaml``) with the task's class mapping and
        rules, runs VL inference (stubbed), filters detections by
        ``conf_threshold`` and ``min_defect_pixels``, and writes a YOLO-OBB
        ``.txt`` label file to ``task_dir/ai_labels/``.

        Args:
            task_dir: Root directory of the task (contains ``images/``).
            task_config: Task configuration dict with ``training_plan``.

        Returns:
            Sorted list of written label file paths (one per annotated image).

        Raises:
            FileNotFoundError: If the task directory does not exist.
        """
        if not task_dir.is_dir():
            raise FileNotFoundError(f"Task directory not found: {task_dir}")

        plan = task_config.get("training_plan", {})
        classes: dict[str, Any] = plan.get("classes", {})
        conf_threshold = float(plan.get("conf_threshold", 0.25))
        min_pixels = int(plan.get("min_defect_pixels", 16))
        exclude_items = set(plan.get("exclude_items", []))

        prompt = self.load_prompt("annotate_agent")
        class_mapping_str = json.dumps({int(k): v for k, v in classes.items()}, ensure_ascii=False)
        annotation_rules = f"obb_rule={plan.get('obb_rule', 'clockwise_normalized')}, min_defect_pixels={min_pixels}"

        images_dir = task_dir / "images"
        labels_dir = ensure_dir(task_dir / "ai_labels")
        written: list[Path] = []

        for image_path in list_images(images_dir):
            h, w = load_image(image_path).shape[:2]
            self.logger.info("Annotating %s (%dx%d)", image_path.name, w, h)
            user_prompt = self._render_user_prompt(prompt["user_prompt_template"], image_path, w, h, min_pixels)
            detections = self._infer(
                prompt["system_prompt"].format(
                    class_mapping=class_mapping_str,
                    annotation_rules=annotation_rules,
                    exclude_items=", ".join(sorted(exclude_items)) or "none",
                ),
                user_prompt,
            )
            lines = self._detections_to_yolo_lines(detections, w, h, conf_threshold, min_pixels)
            label_path = labels_dir / f"{image_path.stem}.txt"
            label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            written.append(label_path)
            self.logger.info("Wrote %d boxes -> %s", len(lines), label_path.name)

        self.logger.info("Annotated %d images in %s", len(written), task_dir.name)
        return written

    def _render_user_prompt(self, template: str, image_path: Path, w: int, h: int, min_pixel: int) -> str:
        """Render the annotate user prompt for one image.

        Args:
            template: ``user_prompt_template`` from the external prompt file.
            image_path: Image being annotated.
            w: Image width in pixels.
            h: Image height in pixels.
            min_pixel: Minimum defect pixel threshold.

        Returns:
            Rendered prompt string.
        """
        return template.format(image_name=image_path.name, w=w, h=h, min_pixel=min_pixel)

    def _infer(self, system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
        """Run VL inference for one image (stubbed).

        The stub returns one deterministic mid-image box so downstream steps
        have data. A real backend would run the local Qwen2.5-VL model here
        (4-bit quantized on GPU, CPU fallback) and parse its JSON output.

        Args:
            system_prompt: Rendered system prompt with class mapping/rules.
            user_prompt: Rendered user prompt for the image.

        Returns:
            List of detections, each ``{"cls": int, "obb": [8 floats], "conf": float}``.
        """
        del system_prompt, user_prompt  # stub: no actual model call
        obb = rotated_box_to_four_points(0.5, 0.5, 0.3, 0.2, 0.0)
        return [{"cls": 0, "obb": [round(v, 6) for v in obb], "conf": 0.92}]

    def _detections_to_yolo_lines(
        self,
        detections: list[dict[str, Any]],
        w: int,
        h: int,
        conf_threshold: float,
        min_pixels: int,
    ) -> list[str]:
        """Convert raw VL detections to YOLO-OBB label lines.

        Each detection is filtered by confidence, class validity, pixel area,
        and coordinate range, then normalized to ``[0, 1]`` clockwise points.

        Args:
            detections: Raw detection dicts from ``_infer``.
            w: Image width in pixels.
            h: Image height in pixels.
            conf_threshold: Minimum confidence to keep a detection.
            min_pixels: Minimum defect area in pixels (contour area).

        Returns:
            Lines formatted as ``cls x1 y1 x2 y2 x3 y3 x4 y4``.
        """
        allowed_classes = set(self._class_ids())
        lines: list[str] = []
        for det in detections:
            try:
                cls = int(det["cls"])
                obb = [float(v) for v in det["obb"]]
                conf = float(det.get("conf", 1.0))
            except (KeyError, TypeError, ValueError):
                self.logger.warning("Skipping malformed detection: %r", det)
                continue
            if cls not in allowed_classes:
                self.logger.warning("Skipping detection with unknown class id %d", cls)
                continue
            if conf < conf_threshold:
                continue
            # VL output is normalized [0, 1] per the OBB contract; values > 1
            # are treated as absolute pixel coordinates.
            pixel_points = denormalize_points(obb, w, h) if max(obb) <= 1.0 + 1e-6 else obb
            area = self._polygon_area(pixel_points)
            if area < min_pixels:
                continue
            normalized = normalize_points(pixel_points, w, h)
            lines.append(f"{cls} " + " ".join(f"{v:.6f}" for v in normalized))
        return lines

    def _class_ids(self) -> list[int]:
        """Return the valid class ids for this task.

        Returns:
            Sorted list of integer class ids from the task plan.
        """
        classes = self.config.get("training_plan", {}).get("classes", {})
        return sorted(int(k) for k in classes)

    @staticmethod
    def _polygon_area(points: list[float]) -> float:
        """Compute polygon area via the shoelace formula.

        Args:
            points: Flat list ``[x1, y1, ..., x4, y4]`` in pixel coords.

        Returns:
            Absolute polygon area in pixels squared.
        """
        arr = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        x, y = arr[:, 0], arr[:, 1]
        return float(0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))
