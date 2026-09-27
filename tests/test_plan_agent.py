"""Tests for src.agents.plan_agent (parsing fallbacks, stub backend)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.agents.plan_agent import PlanAgent, StubLLMClient

_PROMPT_DIR = Path("prompts")


@pytest.fixture
def agent() -> PlanAgent:
    """PlanAgent wired to the repo's real prompt files and the stub LLM."""
    return PlanAgent({}, _PROMPT_DIR)


class _FakeClient:
    """LLM client returning a canned raw string (implements LLMClient)."""

    def __init__(self, raw: str) -> None:
        self.raw = raw

    def generate(self, system_prompt: str, user_prompt: str, *, max_new_tokens: int = 2048) -> str:
        return self.raw


def test_run_stub_produces_guessed_classes(agent: PlanAgent) -> None:
    """The stub backend extracts defect keywords from the description."""
    plan = agent.run("detect crack and scratch on solar cells", "500 images")
    assert set(plan["classes"].values()) >= {"crack", "scratch"}
    assert plan["dataset_strategy"]["split"]["train"] == 0.7


def test_parse_plan_strips_json_code_fence(agent: PlanAgent) -> None:
    """A ```json fenced payload parses into the plan dict."""
    payload = json.dumps({"classes": {"0": "crack"}})
    plan = agent._parse_plan(f"```json\n{payload}\n```")
    assert plan == {"classes": {"0": "crack"}}


def test_parse_plan_accepts_yaml(agent: PlanAgent) -> None:
    """Plain YAML (non-JSON) output is parsed by the YAML fallback."""
    plan = agent._parse_plan("classes:\n  0: crack\n")
    assert plan["classes"] == {0: "crack"}  # YAML turns the id key into an int


def test_parse_plan_unparseable_falls_back_to_stub(agent: PlanAgent) -> None:
    """Text that is neither JSON nor YAML yields the deterministic stub plan."""
    plan = agent._parse_plan("Sorry, I cannot produce a plan: {[broken yaml (")
    assert "classes" in plan and "hyperparameters" in plan
    assert plan["task_overview"]["target_precision"] == 0.9


def test_run_with_broken_backend_falls_back(agent: PlanAgent) -> None:
    """A real backend returning junk must not break plan generation."""
    agent.llm_client = _FakeClient("```json\n{ broken\n```")
    plan = agent.run("bubble defects")
    assert 0 in {int(k) for k in plan["classes"]}


def test_stub_guess_fallback_defect() -> None:
    """Without any known keyword the stub falls back to a single 'defect' class."""
    raw = json.loads(StubLLMClient().generate("", "nothing recognizable"))
    assert raw["classes"] == {"0": "defect"}
