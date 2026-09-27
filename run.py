"""CLI entry point for the VL-YOLO-Anchor pipeline.

Usage::

    uv run python run.py plan --task demo
    uv run python run.py full --task demo
    uv run python run.py doctor --deep
    uv run python run.py index --rebuild
    uv run python run.py secret-keygen
    uv run python run.py secret-set VL_VL_API_KEY < key.txt
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import get_settings, secret_store_path
from src.core.diagnostics import DiagnosticReport, Doctor
from src.core.metadata_store import MetadataStore
from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager
from src.utils.secrets import SecretStore, generate_master_key

_TASK_COMMANDS = ("plan", "annotate", "inspect", "split", "full", "create")
"""Commands that operate on one task and therefore require ``--task``."""

SECRET_NAMES = ("VL_ANCHOR_AUTH_TOKEN", "VL_LLM_API_KEY", "VL_VL_API_KEY", "VL_MODEL_API_KEY")
"""Names ``secret-set`` accepts.

A whitelist rather than "any name": the store is a credential file, and
``secret-set`` reading from stdin makes it easy to type a key under the wrong
name and then wonder why the platform still reports "no api_key".
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured ``argparse.ArgumentParser``.
    """
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="VL-YOLO-Anchor: YOLO-OBB training plan generation & intelligent annotation pipeline.",
    )
    parser.add_argument(
        "command",
        choices=[
            *_TASK_COMMANDS,
            "doctor",
            "index",
            "secret-keygen",
            "secret-set",
        ],
        help="Pipeline step to run ('full' runs all steps; 'create' makes a new task).",
    )
    parser.add_argument("--task", default="", help="Task name (required for the pipeline commands).")
    parser.add_argument("--description", default="", help="Task description (used with 'create').")
    parser.add_argument("--tasks-root", default="tasks", help="Root directory for tasks (default: tasks).")
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO).")
    parser.add_argument("--deep", action="store_true", help="doctor: also probe the model endpoints.")
    parser.add_argument("--json", action="store_true", help="doctor: print the report as JSON.")
    parser.add_argument("--rebuild", action="store_true", help="index: rebuild the whole index from disk.")
    parser.add_argument(
        "name",
        nargs="?",
        default="",
        help="secret-set: name of the secret to write (the value is read from stdin).",
    )
    return parser


def _print_report(report: DiagnosticReport) -> None:
    """Print a diagnostic report for humans.

    Args:
        report: Report to print.
    """
    print(f"[{report.status.upper()}] {report.generated_at}")
    for check in report.checks:
        print(f"  [{check.status.upper():4}] {check.id}: {check.summary}")
        if check.hint:
            print(f"         -> {check.hint}")
    for probe in report.probes:
        print(f"  [probe] {probe.role}: {probe.status} {probe.message}")


def _run_doctor(args: argparse.Namespace, tasks_root: Path) -> int:
    """Run the self-check and print its report.

    Args:
        args: Parsed CLI arguments.
        tasks_root: Task root the doctor verifies.

    Returns:
        Exit code: 1 when any check failed, else 0. Warnings deliberately do not
        fail — the default stub deployment must stay usable from a script.
    """
    settings = get_settings()
    doctor = Doctor(settings, TaskManager(tasks_root), settings.config_dir)
    report = doctor.run(deep=args.deep)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        _print_report(report)
    return 1 if report.status == "fail" else 0


def _run_index(args: argparse.Namespace, tasks_root: Path) -> int:
    """Rebuild the metadata index from the on-disk audit logs.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code: 1 on failure, 2 when ``--rebuild`` is missing.
    """
    if not args.rebuild:
        print("index requires --rebuild (the index is derived; there is nothing else to do).")
        return 2
    settings = get_settings()
    task_manager = TaskManager(tasks_root)
    store = MetadataStore(settings.db_path, enabled=settings.db_enabled)
    if not settings.db_enabled:
        print("Metadata index is disabled (database.enabled=false); nothing to rebuild.")
        return 0
    result = store.rebuild(task_manager, settings.logs_dir)
    print(
        f"Rebuilt {settings.db_path}: tasks={result.tasks} runs={result.runs} "
        f"calls={result.calls} diagnostics={result.diagnostics}"
    )
    return 0


def _run_secret_keygen() -> int:
    """Print a fresh master key.

    Deliberately writes nothing: a master key stored next to the ciphertext it
    protects (``config/secrets.db``) is obfuscation, not encryption. The next
    step is the user's — hand the value to a Docker secret or to the file named
    by ``VL_ANCHOR_SECRET_KEY_FILE``.

    Returns:
        Exit code 0.
    """
    key = generate_master_key()
    print(key)
    print("把上面的值交给 Docker secret，或写入 VL_ANCHOR_SECRET_KEY_FILE 指向的文件（勿放在 config/ 下）。")
    return 0


def _run_secret_set(args: argparse.Namespace) -> int:
    """Store or delete one secret from the encrypted store.

    The value is read from stdin, never from argv: command lines are visible to
    other users via ``ps``, and a key that leaks into shell history or a process
    listing is a key that has to be rotated.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code: 2 for a name outside the whitelist, 1 when the store cannot be
        written (no master key, wrong master key, corrupt file), 0 otherwise.
    """
    name = args.name
    if name not in SECRET_NAMES:
        print(f"Unknown secret name: {name or '(empty)'}")
        print(f"Allowed names: {', '.join(SECRET_NAMES)}")
        return 2

    value = sys.stdin.readline().strip()
    settings = get_settings()
    store = SecretStore(secret_store_path(settings.config_dir), index_db_path=settings.db_path)
    if not store.set(name, value):
        print(f"Could not write {name}: {store.describe_state()}")
        print(f"主密钥需经 {store.master_key_source() or 'VL_ANCHOR_SECRET_KEY_FILE'} 提供，可先用 secret-keygen 生成。")
        return 1
    print(f"{'Deleted' if not value else 'Stored'} {name} in {store.path()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code. ``2`` signals a usage error, ``1`` a runtime failure.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Handled before any settings or task-root setup: this command must have zero
    # filesystem side effects, and TaskManager would create the tasks directory.
    if args.command == "secret-keygen":
        return _run_secret_keygen()
    if args.command == "secret-set":
        return _run_secret_set(args)

    if args.command in _TASK_COMMANDS and not args.task:
        parser.error(f"argument --task is required for command '{args.command}'")

    settings = get_settings()
    tasks_root = Path(args.tasks_root) if args.tasks_root != "tasks" else settings.tasks_root

    if args.command == "doctor":
        return _run_doctor(args, tasks_root)
    if args.command == "index":
        return _run_index(args, tasks_root)

    task_manager = TaskManager(tasks_root)
    pipeline = Pipeline(
        task_manager,
        prompts_dir=settings.prompts_dir,
        llm_settings=settings.llm,
        vl_settings=settings.vl,
        metadata_store=MetadataStore(settings.db_path, enabled=settings.db_enabled),
    )

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
