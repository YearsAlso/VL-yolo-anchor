"""Tests for src.utils.obb_utils."""

from __future__ import annotations

import pytest
from src.utils.obb_utils import (
    OBBBox,
    denormalize_points,
    four_points_to_rotated_box,
    is_coords_in_range,
    normalize_points,
    obb_iou,
    rotated_box_to_four_points,
)


def test_rotated_box_round_trip() -> None:
    """rotated_box_to_four_points -> four_points_to_rotated_box preserves the box.

    ``cv2.minAreaRect`` may swap width/height and shift the angle by -90 for
    the same rectangle, so corners are compared as an order-insensitive set.
    """
    pts = rotated_box_to_four_points(50.0, 40.0, 30.0, 20.0, 30.0)
    cx, cy, w, h, angle = four_points_to_rotated_box(pts)
    assert (cx, cy) == pytest.approx((50.0, 40.0), abs=1e-3)
    assert sorted((w, h)) == pytest.approx([20.0, 30.0], abs=1e-3)
    assert -90.0 <= angle <= 0.0
    rebuilt = rotated_box_to_four_points(cx, cy, w, h, angle)
    assert {(round(x, 3), round(y, 3)) for x, y in zip(rebuilt[0::2], rebuilt[1::2], strict=True)} == {
        (round(x, 3), round(y, 3)) for x, y in zip(pts[0::2], pts[1::2], strict=True)
    }


def test_obb_iou_identical_is_one() -> None:
    """Identical boxes have IoU 1.0."""
    pts = rotated_box_to_four_points(50.0, 50.0, 20.0, 10.0, 0.0)
    a = OBBBox(points=pts)
    b = OBBBox(points=pts)
    assert obb_iou(a, b) == pytest.approx(1.0)


def test_obb_iou_disjoint_is_zero() -> None:
    """Non-overlapping boxes have IoU 0.0."""
    a = OBBBox(points=rotated_box_to_four_points(10.0, 10.0, 5.0, 5.0, 0.0))
    b = OBBBox(points=rotated_box_to_four_points(90.0, 90.0, 5.0, 5.0, 0.0))
    assert obb_iou(a, b) == pytest.approx(0.0)


def test_obb_iou_degenerate_is_zero() -> None:
    """Zero-area boxes yield IoU 0.0 instead of dividing by zero."""
    degenerate = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    a = OBBBox(points=degenerate)
    b = OBBBox(points=rotated_box_to_four_points(50.0, 50.0, 20.0, 10.0, 0.0))
    assert obb_iou(a, b) == pytest.approx(0.0)


def test_normalize_denormalize_round_trip() -> None:
    """normalize -> denormalize restores pixel coordinates."""
    pts = [10.0, 5.0, 90.0, 5.0, 90.0, 75.0, 10.0, 75.0]
    normalized = normalize_points(pts, 100, 80)
    assert denormalize_points(normalized, 100, 80) == pytest.approx(pts)


def test_normalize_clamps_to_unit_range() -> None:
    """Out-of-range pixels are clamped into [0, 1]."""
    normalized = normalize_points([-10.0, -5.0, 200.0, 120.0], 100, 80)
    assert is_coords_in_range(normalized)
    assert normalized == [0.0, 0.0, 1.0, 1.0]


def test_is_coords_in_range_tolerance() -> None:
    """Values within eps outside [0, 1] are still accepted."""
    assert is_coords_in_range([1.0 + 1e-9, -1e-9])
    assert not is_coords_in_range([1.1])


@pytest.mark.parametrize("w,h", [(0, 80), (100, 0), (-1, -1)])
def test_normalize_rejects_invalid_dimensions(w: int, h: int) -> None:
    """Non-positive image dimensions raise ValueError."""
    with pytest.raises(ValueError, match="positive"):
        normalize_points([0.0] * 8, w, h)
    with pytest.raises(ValueError, match="positive"):
        denormalize_points([0.0] * 8, w, h)
