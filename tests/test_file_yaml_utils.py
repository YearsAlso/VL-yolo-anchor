"""Tests for src.utils.file_utils and src.utils.yaml_utils."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.utils.file_utils import ensure_dir, list_images
from src.utils.yaml_utils import load_yaml, save_yaml


def test_list_images_filters_extensions(tmp_path: Path) -> None:
    """Only supported image extensions are listed, sorted by name."""
    for name in ("b.png", "a.jpg", "c.txt", "d.tif", "e.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    names = [p.name for p in list_images(tmp_path)]
    assert names == ["a.jpg", "b.png", "d.tif"]


def test_list_images_missing_dir(tmp_path: Path) -> None:
    """A missing directory yields an empty list."""
    assert list_images(tmp_path / "nope") == []


def test_ensure_dir_idempotent(tmp_path: Path) -> None:
    """ensure_dir creates nested dirs and is safe to call twice."""
    target = tmp_path / "a" / "b"
    assert ensure_dir(target) == target
    assert ensure_dir(target).is_dir()


def test_yaml_round_trip(tmp_path: Path) -> None:
    """save_yaml -> load_yaml preserves mapping content and key order."""
    data = {"b": 1, "a": {"nested": [1, 2]}, "name": "demo"}
    path = tmp_path / "sub" / "out.yaml"
    save_yaml(data, path)
    assert load_yaml(path) == data


def test_load_yaml_empty_file(tmp_path: Path) -> None:
    """An empty YAML file loads as an empty dict."""
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert load_yaml(path) == {}


def test_load_yaml_non_mapping(tmp_path: Path) -> None:
    """A YAML root that is not a mapping raises ValueError."""
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_yaml(path)


def test_load_yaml_missing(tmp_path: Path) -> None:
    """A missing YAML file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_yaml(tmp_path / "nope.yaml")
