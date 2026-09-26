"""Shared pytest fixtures: isolated task roots and generated test images."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture
def make_image(tmp_path: Path):
    """Factory fixture writing a small BGR image and returning its path.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Callable ``(name, w, h) -> Path`` that writes the image file.
    """

    def _make(name: str, w: int = 100, h: int = 80) -> Path:
        img = np.zeros((h, w, 3), dtype=np.uint8)
        path = tmp_path / name
        cv2.imwrite(str(path), img)
        return path

    return _make
