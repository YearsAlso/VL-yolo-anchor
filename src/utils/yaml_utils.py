"""YAML load/save helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dict.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed mapping. Returns an empty dict for empty files.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML root is not a mapping.
    """
    if not path.is_file():
        raise FileNotFoundError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping at {path}, got {type(data).__name__}.")
    return data


def save_yaml(data: dict[str, Any], path: Path) -> None:
    """Save a dict to a YAML file, creating parent directories as needed.

    Args:
        data: Mapping to serialize.
        path: Destination path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def save_yaml_atomic(data: dict[str, Any], path: Path) -> None:
    """Save a dict to YAML by writing a temporary file and replacing the target.

    A crash or full disk mid-write leaves the previous file intact instead of a
    truncated one, which matters for the settings UI: the user's existing
    overrides must survive a failed save.

    Args:
        data: Mapping to serialize.
        path: Destination path.

    Raises:
        OSError: If the temporary file cannot be created, written, or moved into
            place. The temporary file is removed first when possible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
