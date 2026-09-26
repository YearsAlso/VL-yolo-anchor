"""CLI entry point for the VL-YOLO-Anchor pipeline.

Usage::

    uv run python run.py plan --task demo
    uv run python run.py full --task demo
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured ``argparse.ArgumentParser``.
    """
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="VL-YOLO-Anchor: YOLO-OBB training plan generation & intelligent annotation pipeline.",
    )
    parser.add_argument("command", choices=["plan", "annotate", "inspect", "split", "full", "create"],
                        help="Pipeline step to run ('full' runs all steps; 'create' makes a new task).")
    parser.add_argument("--task", required=True, help="Task name.")
    parser.add_argument("--description", default="", help="Task description (used with 'create').")
    parser.add_argument("--tasks-root", default="tasks", help="Root directory for tasks (default: tasks).")
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO).")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 on success, 1 on failure).
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    task_manager = TaskManager(Path(args.tasks_root))
    pipeline = Pipeline(task_manager)

    if args.command == "create":
        try:
            task_dir = task_manager.create_task(args.task, args.description)
        except FileExistsError:
            logging.error("Task already exists: %s", args.task)
            return 1
        print(f"Created task '{args.task}' at {task_dir}")
        return 0

    try:
        if args.command == "full":
            results = pipeline.run_full(args.task)
            for step, result in results.items():
                print(f"== {step} ==")
                print(result)
        else:
            result = pipeline.run_step(args.task, args.command)
            print(f"== {args.command} ==")
            print(result)
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
