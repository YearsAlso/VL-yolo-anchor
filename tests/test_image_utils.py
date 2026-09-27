"""Tests for src.utils.image_utils."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from src.utils.image_utils import image_size, load_image, validate_image


def test_load_image_missing_file(tmp_path: Path) -> None:
    """load_image on a nonexistent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Image not found"):
        load_image(tmp_path / "nope.png")


def test_load_image_undecodable_bytes(tmp_path: Path) -> None:
    """A file that is not a decodable image raises ValueError."""
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"this is definitely not a png")
    with pytest.raises(ValueError, match="Failed to decode"):
        load_image(junk)


def test_load_image_channels(make_image) -> None:
    """Color load yields 3 channels; grayscale yields a 2D array."""
    path = make_image("img.png", 40, 30)
    color = load_image(path)
    gray = load_image(path, grayscale=True)
    assert color.shape == (30, 40, 3)
    assert gray.shape == (30, 40)


def test_image_size_returns_width_height(make_image) -> None:
    """image_size returns (width, height), not the numpy (h, w) order."""
    path = make_image("img.png", 40, 30)
    assert image_size(path) == (40, 30)


def test_validate_image_ok(make_image) -> None:
    """An image at or above the minimum size validates as True."""
    assert validate_image(make_image("ok.png", 16, 16)) is True


def test_validate_image_too_small(make_image) -> None:
    """Images below min_size in either dimension fail validation."""
    assert validate_image(make_image("tiny.png", 8, 40), min_size=16) is False
    assert validate_image(make_image("tiny2.png", 40, 8), min_size=16) is False


def test_validate_image_missing_or_junk(tmp_path: Path, make_image) -> None:
    """Missing files and undecodable content validate as False (no raise)."""
    assert validate_image(tmp_path / "nope.png") is False
    junk = make_image("junk.png")
    junk.write_bytes(b"not an image")
    assert validate_image(junk) is False


def test_load_image_non_ascii_path(tmp_path: Path) -> None:
    """Paths with CJK/space characters load (the np.fromfile reason)."""
    img = np.full((20, 25, 3), 128, dtype=np.uint8)
    path = tmp_path / "中文 测试 文件.png"
    ok, buf = cv2.imencode(".png", img)
    assert ok
    buf.tofile(str(path))
    assert load_image(path).shape == (20, 25, 3)
    assert validate_image(path) is True
