"""Oriented bounding box (OBB) geometry utilities for YOLO-OBB datasets.

All OBB boxes are represented as four points in clockwise order,
``[x1, y1, x2, y2, x3, y3, x4, y4]``. Coordinates may be normalized
(range ``[0, 1]``) or absolute pixels depending on the helper used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class OBBBox:
    """A single oriented bounding box annotation.

    Attributes:
        points: Four corner points as ``[x1, y1, x2, y2, x3, y3, x4, y4]``.
        cls: Class id (0-based).
        conf: Confidence score in ``[0, 1]``.
    """

    points: list[float] = field(default_factory=list)
    cls: int = 0
    conf: float = 1.0

    @property
    def corners(self) -> np.ndarray:
        """Return the eight coordinates reshaped to a ``(4, 2)`` array.

        Returns:
            Array of shape ``(4, 2)`` with one row per corner point.
        """
        return np.asarray(self.points, dtype=np.float64).reshape(4, 2)


def four_points_to_rotated_box(points: list[float]) -> tuple[float, float, float, float, float]:
    """Convert four corner points to a rotated box via minimum-area rectangle.

    Args:
        points: Flat list ``[x1, y1, x2, y2, x3, y3, x4, y4]`` (pixel coords).

    Returns:
        Tuple ``(cx, cy, w, h, angle)`` as returned by ``cv2.minAreaRect``.
        Angle is in degrees, in ``(-90, 0]``.
    """
    arr = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    (cx, cy), (w, h), angle = cv2.minAreaRect(arr)
    return float(cx), float(cy), float(w), float(h), float(angle)


def rotated_box_to_four_points(cx: float, cy: float, w: float, h: float, angle: float) -> list[float]:
    """Convert a rotated box back to four clockwise corner points.

    Args:
        cx: Center x.
        cy: Center y.
        w: Width.
        h: Height.
        angle: Rotation angle in degrees.

    Returns:
        Flat list of 8 floats ``[x1, y1, ..., x4, y4]`` in clockwise order.
    """
    rect = ((cx, cy), (w, h), angle)
    pts = cv2.boxPoints(rect)
    return [float(v) for v in pts.reshape(-1)]


def obb_iou(a: OBBBox, b: OBBBox) -> float:
    """Compute the IoU of two OBBs using convex-polygon intersection.

    Args:
        a: First box.
        b: Second box.

    Returns:
        Intersection-over-union in ``[0.0, 1.0]``.
    """
    poly_a = a.corners.astype(np.float32)
    poly_b = b.corners.astype(np.float32)
    area_a = float(cv2.contourArea(poly_a))
    area_b = float(cv2.contourArea(poly_b))
    if area_a <= 0.0 or area_b <= 0.0:
        return 0.0
    intersect_area, _ = cv2.intersectConvexConvex(poly_a, poly_b)
    union = area_a + area_b - float(intersect_area)
    if union <= 0.0:
        return 0.0
    return float(intersect_area) / union


def normalize_points(points: list[float], w: int, h: int) -> list[float]:
    """Normalize absolute pixel coordinates to ``[0, 1]``.

    Args:
        points: Flat list of coordinates ``[x1, y1, x2, y2, ...]``.
        w: Image width in pixels.
        h: Image height in pixels.

    Returns:
        Flat list of normalized floats.

    Raises:
        ValueError: If image width or height is not positive.
    """
    if w <= 0 or h <= 0:
        raise ValueError(f"Image dimensions must be positive, got w={w}, h={h}.")
    out: list[float] = []
    for i, v in enumerate(points):
        out.append(min(max(float(v) / w, 0.0), 1.0) if i % 2 == 0 else min(max(float(v) / h, 0.0), 1.0))
    return out


def denormalize_points(points: list[float], w: int, h: int) -> list[float]:
    """Convert normalized coordinates in ``[0, 1]`` to absolute pixels.

    Args:
        points: Flat list of normalized coordinates ``[x1, y1, ...]``.
        w: Image width in pixels.
        h: Image height in pixels.

    Returns:
        Flat list of pixel-coordinate floats.

    Raises:
        ValueError: If image width or height is not positive.
    """
    if w <= 0 or h <= 0:
        raise ValueError(f"Image dimensions must be positive, got w={w}, h={h}.")
    out: list[float] = []
    for i, v in enumerate(points):
        out.append(float(v) * w if i % 2 == 0 else float(v) * h)
    return out


def is_coords_in_range(points: list[float], eps: float = 1e-6) -> bool:
    """Check that all coordinates are within ``[0, 1]`` (normalized space).

    Args:
        points: Flat list of coordinates.
        eps: Tolerance for floating-point comparison.

    Returns:
        True if every coordinate is within ``[0 - eps, 1 + eps]``.
    """
    return all(-eps <= float(v) <= 1.0 + eps for v in points)
