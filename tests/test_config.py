"""Tests for src/config.py LLM/VL role separation and pipeline wiring."""

from __future__ import annotations

from pathlib import Path

from src.agents.model_client import OpenAICompatClient, RecordingClient
from src.agents.plan_agent import StubLLMClient
from src.config import ModelSettings, load_settings
from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager

# Env vars that influence model role resolution; cleared per test for isolation.
_MODEL_ENV = [
    "VL_MODEL_PROVIDER",
    "VL_MODEL_BASE_URL",
    "VL_MODEL_API_KEY",
    "VL_MODEL_NAME",
    "VL_LLM_PROVIDER",
    "VL_LLM_BASE_URL",
    "VL_LLM_API_KEY",
    "VL_LLM_NAME",
    "VL_VL_PROVIDER",
    "VL_VL_BASE_URL",
    "VL_VL_API_KEY",
    "VL_VL_NAME",
]


def _clean(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Remove all model-role env vars so a test starts from the yaml defaults.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    for name in _MODEL_ENV:
        monkeypatch.delenv(name, raising=False)


def test_models_default_to_stub(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """With no env overrides both roles stay on the offline stub."""
    _clean(monkeypatch)
    settings = load_settings()
    assert settings.llm.provider == "stub"
    assert settings.vl.provider == "stub"


def test_shared_env_applies_to_both_roles(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """VL_MODEL_* is inherited by llm and vl when no role override exists."""
    _clean(monkeypatch)
    monkeypatch.setenv("VL_MODEL_PROVIDER", "remote")
    monkeypatch.setenv("VL_MODEL_BASE_URL", "http://shared/v1")
    monkeypatch.setenv("VL_MODEL_NAME", "shared-model")
    settings = load_settings()
    assert settings.llm.provider == "remote" and settings.llm.model == "shared-model"
    assert settings.vl.provider == "remote" and settings.vl.model == "shared-model"


def test_role_override_wins_over_shared(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """VL_LLM_NAME overrides only the plan role; vl keeps the shared model."""
    _clean(monkeypatch)
    monkeypatch.setenv("VL_MODEL_PROVIDER", "remote")
    monkeypatch.setenv("VL_MODEL_BASE_URL", "http://shared/v1")
    monkeypatch.setenv("VL_MODEL_NAME", "shared-model")
    monkeypatch.setenv("VL_LLM_NAME", "text-model")
    settings = load_settings()
    assert settings.llm.model == "text-model"
    assert settings.vl.model == "shared-model"


def test_vl_only_remote_llm_stays_stub(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A role can be remote while the other stays on the stub."""
    _clean(monkeypatch)
    monkeypatch.setenv("VL_VL_PROVIDER", "remote")
    monkeypatch.setenv("VL_VL_BASE_URL", "http://vl/v1")
    monkeypatch.setenv("VL_VL_NAME", "qwen2.5vl")
    settings = load_settings()
    assert settings.vl.provider == "remote"
    assert settings.llm.provider == "stub"


def test_pipeline_wires_llm_and_vl_separately(tmp_path: Path) -> None:
    """Pipeline injects the text client into plan and the vision client into annotate."""
    tm = TaskManager(tmp_path / "tasks")
    llm = ModelSettings(provider="stub")
    vl = ModelSettings(provider="remote", base_url="http://vl/v1", model="qwen2.5vl")
    pipeline = Pipeline(tm, prompts_dir=Path("prompts"), llm_settings=llm, vl_settings=vl)

    annotate = pipeline.agents["annotate"]
    plan = pipeline.agents["plan"]
    # Annotate got a real remote VL client (non-None); plan fell back to the stub.
    # Both arrive wrapped in a RecordingClient, which is what fills the audit log.
    vl_client = annotate.vl_client  # type: ignore[attr-defined]
    llm_client = plan.llm_client  # type: ignore[attr-defined]
    assert isinstance(vl_client, RecordingClient)
    assert isinstance(llm_client, RecordingClient)
    assert isinstance(llm_client.inner, StubLLMClient)
    assert isinstance(vl_client.inner, OpenAICompatClient)
    assert vl_client.inner.settings.model == "qwen2.5vl"
