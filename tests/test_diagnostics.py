"""Tests for src/core/diagnostics.py: the doctor's checks and its guarantees.

The two contracts worth defending are ``deep=False`` staying offline and silent,
and the report's status not being red for the default stub deployment. Probe
tests inject an ``httpx.MockTransport`` through the doctor's test seam, so the
suite never opens a socket.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from src.config import load_settings
from src.core.diagnostics import Doctor
from src.core.task_manager import TaskManager
from src.utils.secrets import MASTER_KEY_VAR, SECRETS_FILENAME, SecretStore, generate_master_key
from src.utils.yaml_utils import load_yaml, save_yaml_atomic

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
    "VL_ANCHOR_DATA_DIR",
    "VL_ANCHOR_TASKS_ROOT",
    "VL_ANCHOR_HOST",
    "VL_ANCHOR_AUTH_TOKEN",
    "VL_ANCHOR_AUTH_TOKEN_FILE",
    "VL_ANCHOR_DB_PATH",
    MASTER_KEY_VAR,
    f"{MASTER_KEY_VAR}_FILE",
]

_REMOTE_VL = {"provider": "remote", "base_url": "http://vl.test/v1", "model": "qwen2.5-vl"}

_PROMPTS = ("plan_agent.yaml", "annotate_agent.yaml", "inspect_agent.yaml")


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated, complete configuration directory.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The ``config/`` directory, alongside a populated ``prompts/`` dir.
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    directory = tmp_path / "config"
    directory.mkdir()
    (tmp_path / "tasks").mkdir()
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for filename in _PROMPTS:
        save_yaml_atomic({"system_prompt": "you are an agent", "user_prompt_template": "{{ task }}"}, prompts / filename)
    save_yaml_atomic(
        {
            "model": {"provider": "stub", "timeout_s": 5},
            "paths": {"data_dir": ".", "tasks_root": "tasks", "prompts_dir": "prompts"},
            "server": {"host": "127.0.0.1", "port": 8765},
        },
        directory / "global.yaml",
    )
    monkeypatch.setenv("VL_ANCHOR_CONFIG_DIR", str(directory))
    monkeypatch.setenv("VL_ANCHOR_DATA_DIR", str(tmp_path / "data"))
    return directory


def _doctor(config_dir: Path, transport: httpx.BaseTransport | None = None) -> Doctor:
    """Build a doctor over the isolated configuration.

    Args:
        config_dir: Isolated configuration directory.
        transport: Optional mock transport for deep probes.

    Returns:
        A configured :class:`Doctor`.
    """
    settings = load_settings()
    return Doctor(settings, TaskManager(settings.tasks_root), config_dir, transport=transport)


def _by_id(doctor: Doctor, **run_kwargs: Any) -> dict[str, Any]:
    """Run the doctor and index its checks by id.

    Args:
        doctor: Doctor under test.
        **run_kwargs: Forwarded to :meth:`Doctor.run`.

    Returns:
        Mapping of check id to result.
    """
    report = doctor.run(**run_kwargs)
    return {check.id: check for check in report.checks}


def test_default_stub_config_warns_but_never_fails(config_dir: Path) -> None:
    """The shipped default is a warn, not a failure: doctor must stay usable."""
    report = _doctor(config_dir).run()
    assert report.status == "warn"
    assert {c.id for c in report.checks} >= {
        "config.llm.provider",
        "config.vl.provider",
        "config.paths.config_writable",
        "config.server.auth_token",
        "storage.tasks_root",
        "storage.prompts",
        "storage.db",
        "model.vl.connectivity",
    }
    assert report.probes == []


def test_stub_checks_explain_the_fake_boxes(config_dir: Path) -> None:
    """The stub warning says the boxes are fake, so it cannot be mistaken for OK."""
    checks = _by_id(_doctor(config_dir))
    provider = checks["config.vl.provider"]
    assert provider.status == "warn"
    assert "假框" in provider.summary
    assert provider.hint


def test_remote_without_endpoint_fails(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote role with no base_url/model fails loudly instead of falling back."""
    monkeypatch.setenv("VL_VL_PROVIDER", "remote")
    checks = _by_id(_doctor(config_dir))
    assert checks["config.vl.base_url"].status == "fail"
    assert checks["config.vl.model"].status == "fail"
    assert checks["config.llm.provider"].status == "warn"


def test_deep_false_is_offline_and_silent(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shallow run must not call the network or write a log file."""
    monkeypatch.setenv("VL_VL_PROVIDER", "remote")
    monkeypatch.setenv("VL_VL_BASE_URL", "http://vl.test/v1")
    monkeypatch.setenv("VL_VL_NAME", "qwen2.5-vl")

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("shallow doctor run must not probe endpoints")

    monkeypatch.setattr("src.core.diagnostics.probe_endpoint", _explode)
    report = _doctor(config_dir).run()
    assert all(check.status == "skip" for check in report.checks if check.id.endswith("connectivity"))
    assert not (Path(load_settings().logs_dir) / "diagnostics.jsonl").exists()


def test_deep_probe_reports_success(config_dir: Path) -> None:
    """A reachable endpoint is ok, and the run is recorded in the audit log."""
    save_yaml_atomic({"model": {"vl": _REMOTE_VL}}, config_dir / "overrides.yaml")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
    doctor = _doctor(config_dir, transport)
    report = doctor.run(deep=True)

    check = next(c for c in report.checks if c.id == "model.vl.connectivity")
    assert check.status == "ok"
    assert check.hint == ""
    assert [probe.role for probe in report.probes] == ["vl"]
    assert report.probes[0].http_status == 200
    assert report.probes[0].latency_ms is not None

    history = Path(load_settings().logs_dir) / "diagnostics.jsonl"
    record = json.loads(history.read_text(encoding="utf-8").strip())
    assert record["status"] == report.status
    assert any(c["id"] == "model.vl.connectivity" for c in record["checks"])


def test_deep_probe_unauthorized_hints_at_api_key(config_dir: Path) -> None:
    """A 401 is a fail whose hint points at the key, not at the network."""
    save_yaml_atomic({"model": {"vl": _REMOTE_VL}}, config_dir / "overrides.yaml")
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "no key"}))
    report = _doctor(config_dir, transport).run(deep=True)
    check = next(c for c in report.checks if c.id == "model.vl.connectivity")
    assert check.status == "fail"
    assert report.status == "fail"
    assert "api_key" in check.hint


def test_deep_probe_timeout_does_not_raise(config_dir: Path) -> None:
    """An unreachable endpoint is reported, never propagated as an exception."""

    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out")

    save_yaml_atomic({"model": {"vl": _REMOTE_VL}}, config_dir / "overrides.yaml")
    report = _doctor(config_dir, httpx.MockTransport(_timeout)).run(deep=True)
    check = next(c for c in report.checks if c.id == "model.vl.connectivity")
    assert check.status == "fail"
    assert check.hint


def test_deep_run_probes_both_roles_serially(config_dir: Path) -> None:
    """Both roles are probed in a stable order when both are remote."""
    save_yaml_atomic({"model": {"provider": "remote", "base_url": "http://shared.test/v1", "model": "m"}},
                     config_dir / "overrides.yaml")
    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"choices": []})

    report = _doctor(config_dir, httpx.MockTransport(_handler)).run(deep=True)
    assert [probe.role for probe in report.probes] == ["llm", "vl"]
    assert len(seen) == 2


def test_missing_prompt_fails(config_dir: Path) -> None:
    """A missing agent prompt is a failure, not a warning: the pipeline would break."""
    (config_dir.parent / "prompts" / "annotate_agent.yaml").unlink()
    checks = _by_id(_doctor(config_dir))
    assert checks["storage.prompts"].status == "fail"
    assert "annotate_agent.yaml" in checks["storage.prompts"].detail


def test_missing_tasks_root_fails(config_dir: Path) -> None:
    """A tasks root that disappeared under a running server is a failure."""
    doctor = _doctor(config_dir)
    assert _by_id(doctor)["storage.tasks_root"].status == "ok"
    # TaskManager creates the root on construction, so the realistic failure is
    # the directory going away afterwards — an unmounted volume, a deleted
    # folder — while the long-lived server keeps its TaskManager.
    settings = load_settings()
    settings.tasks_root.rmdir()
    check = _by_id(doctor)["storage.tasks_root"]
    assert check.status == "fail"
    assert check.hint


def test_auth_token_check_severity_follows_binding(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No token is fine on loopback, a warning when the API is exposed."""
    assert _by_id(_doctor(config_dir))["config.server.auth_token"].status == "skip"
    monkeypatch.setenv("VL_ANCHOR_HOST", "0.0.0.0")
    exposed = _by_id(_doctor(config_dir))["config.server.auth_token"]
    assert exposed.status == "warn"
    assert "VL_ANCHOR_AUTH_TOKEN" in exposed.hint


def test_auth_token_from_store_warns_about_lockout(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token kept only in the encrypted store risks locking the operator out."""
    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    SecretStore(config_dir / SECRETS_FILENAME).set("VL_ANCHOR_AUTH_TOKEN", "tok-123456")
    assert load_settings().auth_token == "tok-123456"
    check = _by_id(_doctor(config_dir))["config.server.auth_token"]
    assert check.status == "warn"
    assert "留存" in check.hint


def test_secret_store_check_skips_without_master_key(config_dir: Path) -> None:
    """No master key is a skip with the state explanation attached."""
    check = _by_id(_doctor(config_dir))["config.paths.secret_store"]
    assert check.status == "skip"
    assert check.detail


def test_secret_store_check_ok_with_master_key(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a usable master key the check is green and names the key source."""
    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    check = _by_id(_doctor(config_dir))["config.paths.secret_store"]
    assert check.status == "ok"
    assert "VL_ANCHOR_SECRET_KEY" in check.detail


def test_secret_store_check_fails_on_wrong_master_key(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong master key is a failure with recovery steps, not a silent empty store."""
    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    SecretStore(config_dir / SECRETS_FILENAME).set("VL_VL_API_KEY", "sk-value")
    monkeypatch.setenv(MASTER_KEY_VAR, generate_master_key())
    check = _by_id(_doctor(config_dir))["config.paths.secret_store"]
    assert check.status == "fail"
    assert "主密钥" in check.hint


def test_plaintext_yaml_secret_is_reported(config_dir: Path) -> None:
    """A key committed to global.yaml shows up as a warning with a fix."""
    data = load_yaml(config_dir / "global.yaml")
    data["model"]["api_key"] = "sk-leaked"
    save_yaml_atomic(data, config_dir / "global.yaml")
    check = _by_id(_doctor(config_dir))["config.paths.overrides_plaintext_secret"]
    assert check.status == "warn"
    assert "global.yaml" in check.detail


def test_db_check_follows_enabled_flag(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A disabled index is a skip; an enabled one reports its location."""
    assert _by_id(_doctor(config_dir))["storage.db"].status == "ok"
    monkeypatch.setenv("VL_ANCHOR_DB_ENABLED", "false")
    assert _by_id(_doctor(config_dir))["storage.db"].status == "skip"


def test_report_aggregates_to_worst_status(config_dir: Path) -> None:
    """One failing check makes the whole report fail."""
    report = _doctor(config_dir).run()
    assert report.status == "warn"  # stub warnings only
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    save_yaml_atomic({"model": {"vl": _REMOTE_VL}}, config_dir / "overrides.yaml")
    assert _doctor(config_dir, transport).run(deep=True).status == "fail"


def test_history_is_appended_not_replaced(config_dir: Path) -> None:
    """Two deep runs leave two records, so the log stays an append-only history."""
    save_yaml_atomic({"model": {"vl": _REMOTE_VL}}, config_dir / "overrides.yaml")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
    doctor = _doctor(config_dir, transport)
    doctor.run(deep=True)
    doctor.run(deep=True)
    lines = (Path(load_settings().logs_dir) / "diagnostics.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["generated_at"] for line in lines)


def test_probe_helper_is_reused_not_reimplemented() -> None:
    """``ProbeResult`` is re-exported from the diagnostics module."""
    from src.agents import model_client
    from src.core import diagnostics

    assert diagnostics.ProbeResult is model_client.ProbeResult


def test_run_accepts_role_subset(config_dir: Path) -> None:
    """A caller can inspect a single role without losing the shared checks."""
    ids = _by_id(_doctor(config_dir), roles=["vl"])
    assert "config.vl.provider" in ids
    assert "config.llm.provider" not in ids
    assert "storage.prompts" in ids


def test_doctor_takes_no_network_transport_by_default() -> None:
    """The default transport is None, meaning real httpx behaviour."""
    signature_default: Callable[..., Any] | None = Doctor.__init__.__kwdefaults__ or {}
    assert signature_default.get("transport") is None
