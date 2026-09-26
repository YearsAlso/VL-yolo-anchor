"""Image loading and validation helpers."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

_MIN_SIZE = 16


def load_image(path: Path, grayscale: bool = False) -> np.ndarray:
    """Load an image from disk.

    Args:
        path: Path to the image file.
        grayscale: If True, load as a single-channel grayscale image.

    Returns:
        Decoded image as a numpy array (BGR by default, gray if requested).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the image cannot be decoded.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    flags = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), flags)
    if img is None:
        raise ValueError(f"Failed to decode image: {path}")
    return img


def image_size(path: Path) -> tuple[int, int]:
    """Return the ``(width, height)`` of an image without full decode.

    Args:
        path: Path to the image file.

    Returns:
        Tuple ``(width, height)`` in pixels.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the image cannot be decoded.
    """
    img = load_image(path)
    h, w = img.shape[:2]
    return w, h


def validate_image(path: Path, min_size: int = _MIN_SIZE) -> bool:
    """Validate that an image file exists, decodes, and meets a minimum size.

    Args:
        path: Path to the image file.
        min_size: Minimum width and height in pixels.

    Returns:
        True if the image is valid; False otherwise.
    """
    try:
        img = load_image(path)
    except (FileNotFoundError, ValueError):
        return False
    h, w = img.shape[:2]
    return bool(w >= min_size and h >= min_size)
