"""PlanAgent: generates a structured YOLO-OBB training plan from a task description."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from src.agents.base_agent import BaseAgent


class LLMClient(Protocol):
    """Minimal interface an LLM backend must satisfy.

    The platform ships with a stub implementation; a real backend (e.g. a
    local Qwen model via transformers) can be dropped in by providing any
    object with a ``generate`` method.
    """

    def generate(self, system_prompt: str, user_prompt: str, *, max_new_tokens: int = 2048) -> str:
        """Generate a completion for the given prompts.

        Args:
            system_prompt: System-level instructions.
            user_prompt: User request text.
            max_new_tokens: Maximum number of tokens to generate.

        Returns:
            Raw generated text.
        """
        ...


class StubLLMClient:
    """Deterministic offline LLM stub used when no real backend is configured.

    It produces a valid, conservative YOLO-OBB plan so the pipeline can run
    end-to-end without downloading any model.
    """

    def generate(self, system_prompt: str, user_prompt: str, *, max_new_tokens: int = 2048) -> str:
        """Return a deterministic structured plan.

        Args:
            system_prompt: System-level instructions (ignored by the stub).
            user_prompt: User request containing the task description.
            max_new_tokens: Maximum tokens (ignored by the stub).

        Returns:
            A JSON string with a minimal but schema-valid training plan.
        """
        classes = self._guess_classes(user_prompt)
        plan = {
            "task_overview": {
                "scenario": "user-defined industrial defect detection",
                "modality": "unspecified",
                "target_precision": 0.9,
            },
            "classes": {str(i): name for i, name in enumerate(classes)},
            "annotation_rules": {
                "obb_rule": "clockwise_normalized",
                "min_defect_pixels": 16,
                "exclude_items": [],
            },
            "dataset_strategy": {"split": {"train": 0.7, "val": 0.2, "test": 0.1}, "stratified": True},
            "model_selection": {"yolo_version": "yolo11n-obb", "imgsz": 1024},
            "hyperparameters": {
                "epochs": 100,
                "batch": 8,
                "optimizer": "AdamW",
                "lr0": 0.001,
                "weight_decay": 0.0005,
                "imgsz": 1024,
                "device": "0",
            },
            "inspection_rules": {
                "iou_duplicate_threshold": 0.7,
                "size_outlier_ratio": 0.1,
                "error_types": ["missing_labels", "duplicate_boxes", "class_error", "coords_out_of_range"],
            },
            "evaluation_metrics": ["mAP50", "mAP50-95", "precision", "recall"],
        }
        return json.dumps(plan, ensure_ascii=False, indent=2)

    @staticmethod
    def _guess_classes(user_prompt: str) -> list[str]:
        """Guess defect class names from free-form text.

        Args:
            user_prompt: Text containing the task description.

        Returns:
            At least one class name; falls back to a generic ``"defect"``.
        """
        keywords = ["crack", "break", "inclusion", "scratch", "stain", "defect", "bubble", "deformation"]
        found = [k for k in keywords if k in user_prompt.lower()]
        return found or ["defect"]


class PlanAgent(BaseAgent):
    """Agent that turns a natural-language task description into a training plan.

    The plan is parsed into a dict matching ``config/task_template.yaml``
    schema and returned to the caller (the pipeline persists it).
    """

    def __init__(self, config: dict[str, Any], prompt_dir: Path, llm_client: LLMClient | None = None) -> None:
        """Initialize the plan agent.

        Args:
            config: Configuration mapping.
            prompt_dir: Directory containing ``plan_agent.yaml``.
            llm_client: Optional LLM backend; defaults to :class:`StubLLMClient`.
        """
        super().__init__(config, prompt_dir)
        self.llm_client: LLMClient = llm_client or StubLLMClient()

    def run(self, task_description: str, dataset_size: str = "unknown") -> dict[str, Any]:
        """Generate a structured training plan for the described task.

        Args:
            task_description: Natural-language description of the scenario
                (industry, modality, defect types).
            dataset_size: Rough dataset size hint, e.g. ``"500 images"``.

        Returns:
            Parsed plan dict matching the ``task_template.yaml`` schema with
            an added ``task_overview`` section.
        """
        prompt = self.load_prompt("plan_agent")
        user_prompt = prompt["user_prompt_template"].format(
            task_description=task_description, dataset_size=dataset_size
        )
        raw = self.llm_client.generate(prompt["system_prompt"], user_prompt)
        plan = self._parse_plan(raw)
        self.logger.info("Generated plan with %d classes", len(plan.get("classes", {})))
        return plan

    def _parse_plan(self, raw: str) -> dict[str, Any]:
        """Parse the raw LLM output into a plan dict.

        Tries JSON first, then YAML, and finally falls back to the stub plan
        so the pipeline never breaks on malformed model output.

        Args:
            raw: Raw text returned by the LLM backend.

        Returns:
            Parsed plan dict.

        Raises:
            ValueError: If parsing fails and no fallback can be built.
        """
        import yaml

        text = self._strip_code_fence(raw)
        for parser in (json.loads, yaml.safe_load):
            try:
                data: Any = parser(text)
            except (json.JSONDecodeError, yaml.YAMLError):
                continue
            if isinstance(data, dict):
                return data
        self.logger.warning("Failed to parse plan output; using stub fallback plan.")
        fallback_raw = StubLLMClient().generate("", text)
        fallback: dict[str, Any] = json.loads(fallback_raw)
        return fallback

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Remove a surrounding Markdown code fence if present.

        Args:
            text: Raw LLM output.

        Returns:
            Text with any leading/trailing `````` ``` `````` fences removed.
        """
        match = re.search(r"```(?:json|yaml)?\s*(.*?)\s*```", text, re.DOTALL)
        return match.group(1) if match else text.strip()
