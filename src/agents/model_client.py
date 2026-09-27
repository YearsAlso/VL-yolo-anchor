"""OpenAI-compatible HTTP client for remote LLM / VL inference.

The platform talks to any endpoint exposing the OpenAI ``/chat/completions``
schema (vLLM, Ollama's OpenAI shim, llama.cpp server, hosted APIs, ...). This
keeps the Docker image GPU-free and weight-free: the heavy model lives behind
the configured ``base_url``.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from src.config import ModelSettings

logger = logging.getLogger(__name__)

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


class VLClient(Protocol):
    """Structural interface shared by the stub and the remote client."""

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        image_path: Path | None = None,
        max_new_tokens: int = 2048,
    ) -> str:
        """Generate a completion for the given prompts.

        Args:
            system_prompt: System-level instructions.
            user_prompt: User request text.
            image_path: Optional image for vision requests.
            max_new_tokens: Generation budget.

        Returns:
            Raw generated text.
        """
        ...


class OpenAICompatClient:
    """Minimal OpenAI-compatible chat client using httpx.

    Attributes:
        settings: The resolved :class:`ModelSettings` (base_url, api_key, ...).
    """

    def __init__(self, settings: ModelSettings) -> None:
        """Initialize the client.

        Args:
            settings: Model configuration with a non-empty ``base_url``.
        """
        self.settings: ModelSettings = settings

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        image_path: Path | None = None,
        max_new_tokens: int = 2048,
    ) -> str:
        """Call ``/chat/completions`` and return the assistant text.

        Args:
            system_prompt: System message content.
            user_prompt: User message text.
            image_path: Optional local image attached as a data URL.
            max_new_tokens: Generation budget.

        Returns:
            Content of the first choice's assistant message.

        Raises:
            httpx.HTTPError: If the request fails after retries or returns a
                non-2xx status.
            KeyError: If the response body is not the expected ChatCompletion
                shape.
        """
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": self._messages(system_prompt, user_prompt, image_path),
            "max_tokens": max_new_tokens,
            "temperature": 0,
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.timeout_s) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data: dict[str, Any] = resp.json()
                return str(data["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                last_exc = exc
                logger.warning("Model call attempt %d failed: %s", attempt + 1, exc)
                if attempt < self.settings.max_retries:
                    time.sleep(0.5 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def _messages(self, system_prompt: str, user_prompt: str, image_path: Path | None) -> list[dict[str, Any]]:
        """Build the ChatML messages array (multimodal when an image is set).

        Args:
            system_prompt: System message content.
            user_prompt: User text.
            image_path: Optional image to embed as a base64 data URL.

        Returns:
            List of message dicts for the request payload.
        """
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if image_path is not None:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": self._data_url(image_path)}},
            ]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_prompt})
        return messages

    @staticmethod
    def _data_url(path: Path) -> str:
        """Encode a local image as a base64 data URL.

        Args:
            path: Image file path.

        Returns:
            ``data:<mime>;base64,<...>`` string.
        """
        mime = _IMAGE_MIME.get(path.suffix.lower(), "application/octet-stream")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"


def build_model_client(settings: ModelSettings) -> VLClient | None:
    """Construct a remote client when ``provider`` is ``"remote"``.

    Args:
        settings: Model configuration.

    Returns:
        An :class:`OpenAICompatClient`, or ``None`` for the offline stub path
        (provider not ``remote`` or a missing base_url/model).
    """
    if settings.provider != "remote":
        return None
    if not settings.base_url or not settings.model:
        logger.warning("provider=remote but base_url/model missing; falling back to stub")
        return None
    return OpenAICompatClient(settings)
