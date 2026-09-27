"""Smoke tests for the run.py CLI entry point (exit codes, task root)."""

from __future__ import annotations

from pathlib import Path

import pytest
from run import build_parser, main


def test_build_parser_commands() -> None:
    """The parser exposes exactly the documented subcommands."""
    parser = build_parser()
    args = parser.parse_args(["full", "--task", "demo"])
    assert args.command == "full" and args.task == "demo"
    assert args.tasks_root == "tasks" and args.log_level == "INFO"
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus", "--task", "demo"])


def test_create_then_plan_via_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """'create' scaffolds under --tasks-root; 'plan' prints the generated plan."""
    root = str(tmp_path / "tasks")
    assert main(["create", "--task", "demo", "--description", "crack", "--tasks-root", root]) == 0
    assert (tmp_path / "tasks" / "demo" / "task.yaml").is_file()
    assert main(["plan", "--task", "demo", "--tasks-root", root]) == 0
    out = capsys.readouterr().out
    assert "== plan ==" in out and "classes" in out


def test_create_duplicate_returns_1(tmp_path: Path) -> None:
    """Creating an existing task through the CLI exits 1 with an error log."""
    root = str(tmp_path / "tasks")
    assert main(["create", "--task", "demo", "--tasks-root", root]) == 0
    assert main(["create", "--task", "demo", "--tasks-root", root]) == 1


def test_full_on_missing_task_returns_1(tmp_path: Path) -> None:
    """Running 'full' on an unknown task exits 1 (FileNotFoundError handled)."""
    assert main(["full", "--task", "ghost", "--tasks-root", str(tmp_path / "tasks")]) == 1
