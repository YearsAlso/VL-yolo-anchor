"""Pipeline orchestration: plan -> annotate -> inspect -> split/export."""

from __future__ import annotations

import logging
import random
import shutil
from pathlib import Path
from typing import Any

from src.agents.annotate_agent import AnnotateAgent
from src.agents.base_agent import BaseAgent
from src.agents.inspect_agent import InspectAgent
from src.agents.model_client import build_model_client
from src.agents.plan_agent import PlanAgent, StubLLMClient
from src.config import ModelSettings
from src.core.task_manager import TaskManager
from src.utils.file_utils import ensure_dir, list_images
from src.utils.yaml_utils import save_yaml

_STEPS = ("plan", "annotate", "inspect", "split")


class Pipeline:
    """Runs the full or per-step annotation/training-prep pipeline for a task.

    Attributes:
        task_manager: TaskManager used for task storage and config IO.
        agents: Mapping of step name to agent instance.
        logger: Pipeline logger.
    """

    def __init__(
        self,
        task_manager: TaskManager,
        agents: dict[str, BaseAgent] | None = None,
        prompts_dir: Path = Path("prompts"),
        llm_settings: ModelSettings | None = None,
        vl_settings: ModelSettings | None = None,
    ) -> None:
        """Initialize the pipeline.

        Args:
            task_manager: Manager providing task directories and configs.
            agents: Optional explicit agent mapping with keys ``plan``,
                ``annotate``, ``inspect``. Defaults to agents wired from
                ``prompts_dir`` and the two model settings.
            prompts_dir: Directory holding the agent prompt YAML files.
            llm_settings: Text-LLM config for the plan step; when its provider
                is ``"remote"`` an OpenAI-compatible client is injected into
                PlanAgent, else a deterministic stub is used.
            vl_settings: Vision-language config for the annotate step; when its
                provider is ``"remote"`` a client is injected into AnnotateAgent,
                else the offline stub runs (no network/model downloads).
        """
        self.task_manager: TaskManager = task_manager
        self.agents: dict[str, BaseAgent] = agents or self._default_agents(
            prompts_dir, llm_settings, vl_settings
        )
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def _default_agents(
        prompts_dir: Path,
        llm_settings: ModelSettings | None,
        vl_settings: ModelSettings | None,
    ) -> dict[str, BaseAgent]:
        """Build the default plan/annotate/inspect agents.

        Plan uses the text ``llm_settings``; annotate uses the multimodal
        ``vl_settings``; inspect is pure rule-based and takes no model.

        Args:
            prompts_dir: Directory holding the prompt YAML files.
            llm_settings: Text-LLM configuration (``None`` => stub).
            vl_settings: VL configuration (``None`` => stub).

        Returns:
            Mapping of step name to agent instance.
        """
        llm_client = build_model_client(llm_settings) if llm_settings is not None else None
        vl_client = build_model_client(vl_settings) if vl_settings is not None else None
        return {
            "plan": PlanAgent({}, prompts_dir, llm_client=llm_client or StubLLMClient()),
            "annotate": AnnotateAgent({}, prompts_dir, vl_client=vl_client),
            "inspect": InspectAgent({}, prompts_dir),
        }

    def run_full(self, task_name: str) -> dict[str, Any]:
        """Run all steps for a task: plan, annotate, inspect, split.

        Args:
            task_name: Name of an existing task.

        Returns:
            Mapping of step name to that step's result dict.
        """
        results: dict[str, Any] = {}
        for step in _STEPS:
            results[step] = self.run_step(task_name, step)
        return results

    def run_step(self, task_name: str, step: str) -> Any:
        """Run a single pipeline step for a task.

        Args:
            task_name: Name of an existing task.
            step: One of ``plan``, ``annotate``, ``inspect``, ``split``.

        Returns:
            The step's result: plan dict, label path list, inspection report
            dict, or export summary dict.

        Raises:
            ValueError: If the step name is unknown.
            FileNotFoundError: If the task does not exist.
        """
        if step not in _STEPS:
            raise ValueError(f"Unknown step '{step}'. Expected one of {_STEPS}.")
        config = self.task_manager.load_task(task_name)
        task_dir = self.task_manager.task_dir(task_name)
        self.logger.info("Running step '%s' for task '%s'", step, task_name)

        result: Any
        if step == "plan":
            result = self._run_plan(task_dir, config)
        elif step == "annotate":
            result = self._run_annotate(task_dir, config)
        elif step == "inspect":
            result = self._run_inspect(task_dir, config)
        else:
            result = self._run_split(task_dir, config)

        self.task_manager.save_task_config(task_name, config)
        return result

    def _run_plan(self, task_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
        """Generate (or regenerate) the training plan and merge it into config.

        The plan agent's output is normalized to the ``task_template.yaml``
        schema and stored under ``training_plan``. Existing per-task rules
        are never silently dropped: plan output overwrites defaults, but the
        task's ``task`` section (name/description) is preserved.

        Args:
            task_dir: Task directory.
            config: Mutable task config dict, updated in place.

        Returns:
            The merged ``training_plan`` dict.
        """
        task_info = config.get("task", {})
        plan_agent = self.agents["plan"]
        plan_agent.config = config  # per-task rule isolation
        plan = plan_agent.run(
            task_info.get("description", ""), str(config.get("dataset_size", "unknown"))
        )
        training_plan: dict[str, Any] = normalize_plan(plan)
        training_plan["task_overview"] = plan.get("task_overview", {})
        config["training_plan"] = training_plan
        save_yaml(training_plan, task_dir / "plan.yaml")
        self.logger.info("Plan generated with classes: %s", list(training_plan.get("classes", {})))
        return training_plan

    def _run_annotate(self, task_dir: Path, config: dict[str, Any]) -> list[Path]:
        """Run annotation for the task.

        Args:
            task_dir: Task directory containing ``images/``.
            config: Task config dict.

        Returns:
            List of written label file paths.
        """
        annotate_agent = self.agents["annotate"]
        annotate_agent.config = config  # per-task rule isolation
        written: list[Path] = annotate_agent.run(task_dir, config)
        return written

    def _run_inspect(self, task_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
        """Run inspection for the task.

        Args:
            task_dir: Task directory containing ``ai_labels/``.
            config: Task config dict.

        Returns:
            Inspection report dict.
        """
        inspect_agent = self.agents["inspect"]
        inspect_agent.config = config  # per-task rule isolation
        report: dict[str, Any] = inspect_agent.run(task_dir, config)
        save_yaml(report, task_dir / "inspection_report.yaml")
        return report

    def _run_split(self, task_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
        """Split the dataset and export a ready-to-train YOLO-OBB package.

        Copies images and candidate labels (falling back to ``ai_labels``)
        into ``dataset/{train,val,test}`` using the plan's split ratios, then
        writes ``dataset/data.yaml`` and ``train_command.txt``.

        Args:
            task_dir: Task directory.
            config: Task config dict.

        Returns:
            Export summary dict with split counts and output paths.
        """
        plan = config.get("training_plan", {})
        split_cfg = plan.get("split", {})
        train_ratio = float(split_cfg.get("train_ratio", 0.7))
        val_ratio = float(split_cfg.get("val_ratio", 0.2))

        images = list_images(task_dir / "images")
        if not images:
            self.logger.warning("No images to split for task '%s'", task_dir.name)
            return {"task": task_dir.name, "splits": {}, "dataset_yaml": "", "train_command": ""}

        random.seed(42)
        shuffled = list(images)
        random.shuffle(shuffled)
        n = len(shuffled)
        n_train = int(n * train_ratio)
        n_val = int(n * val_ratio)
        splits = {
            "train": shuffled[:n_train],
            "val": shuffled[n_train : n_train + n_val],
            "test": shuffled[n_train + n_val :],
        }

        dataset_dir = ensure_dir(task_dir / "dataset")
        counts: dict[str, int] = {}
        for split_name, split_images in splits.items():
            img_out = ensure_dir(dataset_dir / split_name / "images")
            lbl_out = ensure_dir(dataset_dir / split_name / "labels")
            for image_path in split_images:
                shutil.copy2(image_path, img_out / image_path.name)
                label = self._pick_label(task_dir, image_path)
                if label is not None:
                    shutil.copy2(label, lbl_out / label.name)
            counts[split_name] = len(split_images)

        dataset_yaml = self._write_dataset_yaml(dataset_dir, plan)
        train_command = self._write_train_command(dataset_dir, plan, config)
        summary = {
            "task": task_dir.name,
            "splits": counts,
            "dataset_yaml": str(dataset_yaml),
            "train_command": train_command,
        }
        save_yaml(summary, dataset_dir / "export_summary.yaml")
        self.logger.info("Dataset exported: %s", counts)
        return summary

    @staticmethod
    def _pick_label(task_dir: Path, image_path: Path) -> Path | None:
        """Pick the best available label for an image.

        Prefers candidate (inspected) labels; falls back to raw AI labels.

        Args:
            task_dir: Task directory.
            image_path: Image to find a label for.

        Returns:
            Label path, or None when no label exists.
        """
        for sub in ("candidate_labels", "ai_labels"):
            candidate = task_dir / sub / f"{image_path.stem}.txt"
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _write_dataset_yaml(dataset_dir: Path, plan: dict[str, Any]) -> Path:
        """Write the YOLO-OBB ``data.yaml`` for the exported dataset.

        Args:
            dataset_dir: Exported dataset root.
            plan: Training plan dict with ``classes``.

        Returns:
            Path of the written ``data.yaml``.
        """
        classes = plan.get("classes", {})
        names = {int(k): str(v) for k, v in classes.items()}
        data = {
            "path": ".",
            "train": "train/images",
            "val": "val/images",
            "test": "test/images",
            "names": names,
        }
        out = dataset_dir / "data.yaml"
        save_yaml(data, out)
        return out

    @staticmethod
    def _write_train_command(dataset_dir: Path, plan: dict[str, Any], config: dict[str, Any]) -> str:
        """Render the ready-to-run YOLO-OBB training command.

        Args:
            dataset_dir: Exported dataset root.
            plan: Training plan dict with model and hyperparameters.
            config: Task config dict (for the task name in output paths).

        Returns:
            Shell command string; also written to ``train_command.txt``.
        """
        model = plan.get("model", {})
        hp = plan.get("hyperparameters", {})
        cmd = (
            f"yolo obb train model={model.get('yolo_version', 'yolo11n-obb')} "
            f"data={dataset_dir / 'data.yaml'} "
            f"epochs={hp.get('epochs', 100)} batch={hp.get('batch', 8)} "
            f"imgsz={hp.get('imgsz', 1024)} optimizer={hp.get('optimizer', 'AdamW')} "
            f"lr0={hp.get('lr0', 0.001)} weight_decay={hp.get('weight_decay', 0.0005)} "
            f"device={hp.get('device', '0')} project=runs/{config.get('task', {}).get('name', 'task')} name=train"
        )
        (dataset_dir / "train_command.txt").write_text(cmd + "\n", encoding="utf-8")
        return cmd


def normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize an agent-generated plan to the ``task_template`` schema.

    Args:
        plan: Raw plan dict from :class:`PlanAgent` (or any LLM output).

    Returns:
        Dict with keys ``classes``, ``min_defect_pixels``, ``conf_threshold``,
        ``exclude_items``, ``obb_rule``, ``split``, ``model``,
        ``hyperparameters``, ``inspection``, and ``metrics``.
    """
    rules = plan.get("annotation_rules", {})
    strategy = plan.get("dataset_strategy", {})
    split_in = strategy.get("split", {}) if isinstance(strategy, dict) else {}
    inspection_in = plan.get("inspection_rules", {})
    classes = plan.get("classes", {})
    return {
        "classes": {int(k): str(v) for k, v in classes.items()} if isinstance(classes, dict) else {},
        "min_defect_pixels": int(rules.get("min_defect_pixels", 16)),
        "conf_threshold": float(rules.get("conf_threshold", 0.25)),
        "exclude_items": list(rules.get("exclude_items", [])),
        "obb_rule": str(rules.get("obb_rule", "clockwise_normalized")),
        "split": {
            "train_ratio": float(split_in.get("train", split_in.get("train_ratio", 0.7))),
            "val_ratio": float(split_in.get("val", split_in.get("val_ratio", 0.2))),
            "test_ratio": float(split_in.get("test", split_in.get("test_ratio", 0.1))),
            "stratified": bool(strategy.get("stratified", True)) if isinstance(strategy, dict) else True,
        },
        "model": dict(plan.get("model_selection", {})) or {"yolo_version": "yolo11n-obb", "imgsz": 1024},
        "hyperparameters": dict(plan.get("hyperparameters", {})),
        "inspection": {
            "iou_duplicate_threshold": float(inspection_in.get("iou_duplicate_threshold", 0.7)),
            "size_outlier_ratio": float(inspection_in.get("size_outlier_ratio", 0.1)),
            "check_missing_labels": True,
            "check_duplicate_boxes": True,
            "check_class_errors": True,
            "check_coords_out_of_range": True,
            "check_modality_rule_conflict": True,
        },
        "metrics": list(plan.get("evaluation_metrics", ["mAP50", "mAP50-95", "precision", "recall"])),
    }
