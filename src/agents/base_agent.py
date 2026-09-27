"""Abstract base class shared by all agents."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.utils.yaml_utils import load_yaml


class BaseAgent(ABC):
    """Base class for all agents.

    Provides prompt loading from external YAML files and a configured logger.
    Subclasses implement :meth:`run`.

    Attributes:
        config: Agent/task configuration mapping.
        prompt_dir: Directory containing the agent prompt YAML files.
        logger: Logger named after the concrete agent class.
    """

    def __init__(self, config: dict[str, Any], prompt_dir: Path) -> None:
        """Initialize the agent.

        Args:
            config: Configuration mapping (task config merged over globals).
            prompt_dir: Directory that holds the ``*.yaml`` prompt files.
        """
        self.config: dict[str, Any] = config
        self.prompt_dir: Path = prompt_dir
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)

    def load_prompt(self, name: str) -> dict[str, Any]:
        """Load a prompt definition from the prompt directory.

        Args:
            name: Prompt file stem, e.g. ``"plan_agent"`` for
                ``<prompt_dir>/plan_agent.yaml``.

        Returns:
            Parsed prompt mapping with keys such as ``system_prompt`` and
            ``user_prompt_template``.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
            ValueError: If the YAML root is not a mapping.
        """
        prompt = load_yaml(self.prompt_dir / f"{name}.yaml")
        self.logger.debug("Loaded prompt '%s' with keys: %s", name, sorted(prompt))
        return prompt

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the agent's task.

        Concrete agents narrow the signature: PlanAgent returns a plan dict,
        AnnotateAgent a list of label paths, InspectAgent a report dict.

        Args:
            *args: Positional inputs specific to the concrete agent.
            **kwargs: Keyword inputs specific to the concrete agent.

        Returns:
            Structured result whose type depends on the concrete agent.
        """
        raise NotImplementedError
