"""Smoke tests for the run.py CLI entry point (exit codes, task root, doctor, secrets)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from run import build_parser, main
from src.agents.model_client import ProbeResult
from src.config import get_settings
from src.utils.secrets import MASTER_KEY_VAR, SecretStore, generate_master_key, is_valid_master_key
from src.utils.yaml_utils import save_yaml_atomic

_ENV_VARS = [
    "VL_MODEL_PROVIDER",
    "VL_MODEL_BASE_URL",
    "VL_MODEL_NAME",
    "VL_LLM_PROVIDER",
    "VL_LLM_BASE_URL",
    "VL_LLM_NAME",
    "VL_VL_PROVIDER",
    "VL_VL_BASE_URL",
    "VL_VL_NAME",
    "VL_VL_API_KEY",
    "VL_MODEL_API_KEY",
    "VL_LLM_API_KEY",
    "VL_ANCHOR_AUTH_TOKEN",
    "VL_ANCHOR_AUTH_TOKEN_FILE",
    "VL_ANCHOR_HOST",
    "VL_ANCHOR_DB_PATH",
    MASTER_KEY_VAR,
    f"{MASTER_KEY_VAR}_FILE",
]

_PROMPTS = ("plan_agent.yaml", "annotate_agent.yaml", "inspect_agent.yaml")


def test_build_parser_commands() -> None:
    """The parser exposes exactly the documented subcommands and flags."""
    parser = build_parser()
    args = parser.parse_args(["full", "--task", "demo"])
    assert args.command == "full" and args.task == "demo"
    assert args.tasks_root == "tasks" and args.log_level == "INFO"
    assert (args.deep, args.json, args.rebuild) == (False, False, False)
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus", "--task", "demo"])

    secret = parser.parse_args(["secret-set", "VL_VL_API_KEY"])
    assert secret.name == "VL_VL_API_KEY" and secret.command == "secret-set"
    assert parser.parse_args(["doctor", "--deep", "--json"]).deep is True
    assert parser.parse_args(["index", "--rebuild"]).rebuild is True


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate a CLI run: tmp config, data dir, tasks root, and prompts.

    ``main`` resolves its settings through the process-wide cache, so the cache is
    cleared on the way in and on the way out — otherwise the first test to run
    would freeze its environment for every test after it.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        The temporary workspace root.
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (tmp_path / "data" / "tasks").mkdir(parents=True)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for filename in _PROMPTS:
        save_yaml_atomic(
            {"system_prompt": "you are an agent", "user_prompt_template": "{{ task }}"},
            prompts / filename,
        )
    save_yaml_atomic(
        {
            "model": {"provider": "stub", "timeout_s": 5},
            "paths": {"data_dir": ".", "tasks_root": "tasks", "prompts_dir": "prompts"},
            "server": {"host": "127.0.0.1", "port": 8765},
        },
        config_dir / "global.yaml",
    )
    monkeypatch.setenv("VL_ANCHOR_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("VL_ANCHOR_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _feed_stdin(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Point ``sys.stdin`` at a single line of input.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        value: Line to feed the prompt (no trailing newline needed).
    """
    monkeypatch.setattr("sys.stdin", io.StringIO(value + "\n"))


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    """Record every file under ``root`` with its size and contents.

    Args:
        root: Directory to walk.

    Returns:
        ``relative path -> (size, bytes)``; also usable to detect modifications,
        not just additions.
    """
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


def test_task_commands_require_task(workspace: Path) -> None:
    """`--task` stays mandatory for the pipeline commands, and only for them."""
    for command in ("plan", "annotate", "inspect", "split", "full", "create"):
        with pytest.raises(SystemExit) as excinfo:
            main([command])
        assert excinfo.value.code == 2, command


def test_doctor_stub_exits_zero(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance 2: the default stub deployment is a warning, not a failure."""
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "config.vl.provider" in out and "config.llm.provider" in out
    assert "stub" in out
    # Shallow runs are local and silent: no probe, no history file.
    assert "probe" not in out
    assert not (workspace / "data" / "logs" / "diagnostics.jsonl").exists()


def test_doctor_json_is_parseable(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` prints the DiagnosticReport itself, so a script can consume it."""
    assert main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "warn"
    assert {check["id"] for check in report["checks"]} >= {
        "config.vl.provider",
        "storage.tasks_root",
        "storage.prompts",
        "storage.db",
    }


def test_doctor_deep_fails_on_unreachable_endpoint(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance 3: an unreachable remote endpoint makes the exit code 1.

    The probe itself is replaced: its HTTP behaviour is covered by
    ``tests/test_diagnostics.py`` through a mock transport, and what this test
    pins is the CLI's exit-code mapping.
    """
    monkeypatch.setenv("VL_VL_PROVIDER", "remote")
    monkeypatch.setenv("VL_VL_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("VL_VL_NAME", "qwen2.5-vl")

    def _fail_probe(*args: object, **kwargs: object) -> ProbeResult:
        return ProbeResult(
            role="vl",
            status="fail",
            latency_ms=1000,
            http_status=None,
            message="连接失败",
            hint="确认端点已启动",
        )

    monkeypatch.setattr("src.core.diagnostics.probe_endpoint", _fail_probe)
    assert main(["doctor", "--deep"]) == 1
    out = capsys.readouterr().out
    assert "model.vl.connectivity" in out
    assert "确认端点已启动" in out


def test_index_requires_rebuild(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`index` alone is a usage error: the index is derived, there is nothing else to do."""
    assert main(["index"]) == 2
    assert "--rebuild" in capsys.readouterr().out


def test_index_rebuild_is_idempotent(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`index --rebuild` indexes the audit logs and prints the same counts twice."""
    task_dir = workspace / "data" / "tasks" / "demo"
    task_dir.mkdir()
    save_yaml_atomic({"task": {"name": "demo", "description": "crack"}}, task_dir / "task.yaml")
    (task_dir / "run_history.jsonl").write_text(
        json.dumps(
            {
                "step": "plan",
                "status": "ok",
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:00:01Z",
                "duration_ms": 3,
                "items": None,
                "message": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["index", "--rebuild"]) == 0
    first = capsys.readouterr().out.strip()
    assert first.endswith("tasks=1 runs=1 calls=0 diagnostics=0")
    assert "Rebuilt" in first

    assert main(["index", "--rebuild"]) == 0
    assert capsys.readouterr().out.strip() == first


def test_secret_keygen_prints_a_usable_key_and_writes_nothing(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance 12: the key is stdout-only — the workspace is untouched."""
    before = _snapshot(workspace)
    assert main(["secret-keygen"]) == 0
    out = capsys.readouterr().out
    key = out.splitlines()[0]
    assert is_valid_master_key(key)
    assert "VL_ANCHOR_SECRET_KEY_FILE" in out
    assert _snapshot(workspace) == before


def test_secret_set_rejects_a_name_outside_the_whitelist(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown name is a usage error, and never reaches the store."""
    assert main(["secret-set", "PATH"]) == 2
    out = capsys.readouterr().out
    assert "Unknown secret name: PATH" in out
    assert "VL_ANCHOR_AUTH_TOKEN" in out
    assert not (workspace / "config" / "secrets.db").exists()


def test_secret_set_stores_then_deletes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A value from stdin is stored encrypted; an empty line deletes it."""
    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    secret = "tok-abc-123"
    _feed_stdin(monkeypatch, secret)

    assert main(["secret-set", "VL_ANCHOR_AUTH_TOKEN"]) == 0
    out = capsys.readouterr().out
    assert "Stored VL_ANCHOR_AUTH_TOKEN" in out
    assert secret not in out

    store_path = workspace / "config" / "secrets.db"
    assert SecretStore(store_path).get("VL_ANCHOR_AUTH_TOKEN") == secret
    assert secret.encode() not in store_path.read_bytes()

    _feed_stdin(monkeypatch, "")
    assert main(["secret-set", "VL_ANCHOR_AUTH_TOKEN"]) == 0
    assert "Deleted VL_ANCHOR_AUTH_TOKEN" in capsys.readouterr().out
    assert SecretStore(store_path).get("VL_ANCHOR_AUTH_TOKEN") == ""


def test_secret_set_without_master_key_exits_1(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance 17: no master key → exit 1, a hint, and no store file created."""
    secret = "never-written"
    _feed_stdin(monkeypatch, secret)

    assert main(["secret-set", "VL_VL_API_KEY"]) == 1
    out = capsys.readouterr().out
    assert "Could not write VL_VL_API_KEY" in out
    assert "secret-keygen" in out
    assert secret not in out
    assert not (workspace / "config" / "secrets.db").exists()
