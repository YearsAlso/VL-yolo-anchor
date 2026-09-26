"""File-system helpers: image listing and directory creation."""

from __future__ import annotations

from pathlib import Path

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"})


def list_images(directory: Path) -> list[Path]:
    """List image files in a directory (non-recursive).

    Args:
        directory: Directory to scan.

    Returns:
        Sorted list of image paths with extensions jpg/jpeg/png/bmp/tif/tiff.
        Returns an empty list if the directory does not exist.
    """
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS)


def ensure_dir(path: Path) -> Path:
    """Create a directory (and parents) if it does not exist.

    Args:
        path: Directory path to create.

    Returns:
        The same path, for chaining.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path
