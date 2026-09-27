"""OpenAI-compatible HTTP client for remote LLM / VL inference.

The platform talks to any endpoint exposing the OpenAI ``/chat/completions``
schema (vLLM, Ollama's OpenAI shim, llama.cpp server, hosted APIs, ...). This
keeps the Docker image GPU-free and weight-weight-free: the heavy model lives behind
the configured ``base_url``.

Also home to two things the rest of the platform builds on:

* :func:`probe_endpoint` — the connectivity check behind ``POST /api/config/test``
  and ``run.py doctor --deep``. It lives here so the diagnostics layer does not
  need to know httpx request shapes.
* :class:`RecordingClient` — a transparent wrapper that reports every
  ``generate`` call to a hook, so ``tasks/<name>/model_calls.jsonl`` can answer
  "were these labels produced by a model, or by the stub?" long after the run.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from src.config import ModelSettings, Role

logger = logging.getLogger(__name__)

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

PROBE_TIMEOUT_S = 15.0
"""Upper bound on a probe request; the configured timeout may be far longer."""

ProbeStatus = Literal["ok", "fail", "skip"]
"""Outcome of one connectivity probe."""


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
        last_http_status: Status code of the most recent attempt, or ``None``
            when the request never reached the server (DNS/connect/timeout).
            Read by :class:`RecordingClient` to fill the audit record: only this
            class knows about HTTP, and the recorder only knows about
            attribution, so the fact is published here instead of duplicated.
    """

    def __init__(self, settings: ModelSettings) -> None:
        """Initialize the client.

        Args:
            settings: Model configuration with a non-empty ``base_url``.
        """
        self.settings: ModelSettings = settings
        self.last_http_status: int | None = None

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

        self.last_http_status = None
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.timeout_s) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    self.last_http_status = resp.status_code
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


class ProbeResult(BaseModel):
    """Outcome of a single endpoint connectivity probe.

    Defined here rather than in :mod:`src.core.diagnostics` because it is this
    function's return type; diagnostics re-exports it. The reverse arrangement
    would make the two modules import each other.

    Attributes:
        role: Which model role was probed.
        status: ``ok`` when the endpoint answered, ``fail`` otherwise,
            ``skip`` when nothing was probed.
        latency_ms: Round-trip time, when a request was actually attempted.
        http_status: HTTP status code, when a response was received.
        message: Human-readable outcome.
        hint: Actionable Chinese fix suggestion; empty on success.
    """

    role: Role
    status: ProbeStatus
    latency_ms: int | None = None
    http_status: int | None = None
    message: str = ""
    hint: str = ""


def probe_endpoint(
    settings: ModelSettings,
    *,
    role: Role = "vl",
    api_key_override: str | None = None,
    base_url_override: str | None = None,
    model_override: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> ProbeResult:
    """Check that a model endpoint is reachable, authenticated, and correct.

    Sends one minimal text request — ``max_tokens=1``, a single ``"ping"``
    message, no image. A sample image is not needed to learn whether the
    endpoint answers, the key is accepted, and the model name exists, and
    uploading one would make the check slow on an 8GB machine.

    Retries are disabled and the timeout is capped at :data:`PROBE_TIMEOUT_S`:
    a diagnostic that hangs is worse than one that reports a timeout.

    Args:
        settings: Model configuration supplying timeout and defaults.
        role: Role being probed, echoed into the result.
        api_key_override: Key to try instead of the configured one, for the
            settings UI's "test before saving" flow. Not logged.
        base_url_override: Endpoint to try instead of the configured one.
        model_override: Model name to try instead of the configured one.
        transport: httpx transport to use; tests inject a mock here.

    Returns:
        A :class:`ProbeResult`; never raises.
    """
    base_url = (base_url_override if base_url_override is not None else settings.base_url).strip()
    model = (model_override if model_override is not None else settings.model).strip()
    api_key = api_key_override if api_key_override is not None else settings.api_key

    if not base_url:
        return ProbeResult(
            role=role,
            status="fail",
            message="未配置 base_url，无法探测端点。",
            hint="在设置页填写 base_url（如 http://127.0.0.1:8000/v1），或改用 provider=stub。",
        )

    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = max(1.0, min(settings.timeout_s, PROBE_TIMEOUT_S))
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        return ProbeResult(
            role=role,
            status="fail",
            latency_ms=_elapsed_ms(started),
            message=f"探测超时（{timeout:.0f}s）：{url}",
            hint="确认推理服务已启动并可从本机访问；服务冷启动时会较慢，可先手动 curl 一次预热。",
        )
    except httpx.HTTPError as exc:
        return ProbeResult(
            role=role,
            status="fail",
            latency_ms=_elapsed_ms(started),
            message=f"无法连接 {url}：{exc}",
            hint="检查 base_url 的 host/port 与网络连通性（容器内请确认用宿主机可达地址而非 localhost）。",
        )

    latency_ms = _elapsed_ms(started)
    status_code = response.status_code
    if 200 <= status_code < 300:
        return ProbeResult(
            role=role,
            status="ok",
            latency_ms=latency_ms,
            http_status=status_code,
            message=f"{url} 可用（模型 {model or '(未指定)'}）。",
        )
    if status_code in {401, 403}:
        hint = "检查 api_key：该端点拒绝了鉴权（注意自建端点常不需要 key，此时请清空 api_key 字段）。"
    elif status_code == 404:
        hint = "404 通常意味着 base_url 或 model 名不对：base_url 应指向 /v1 而非 /v1/chat/completions，model 需与服务的模型列表一致。"
    else:
        hint = "端点返回非 2xx：查看服务端日志，确认模型已加载且显存充足。"
    return ProbeResult(
        role=role,
        status="fail",
        latency_ms=latency_ms,
        http_status=status_code,
        message=f"{url} 返回 HTTP {status_code}。",
        hint=hint,
    )


def _elapsed_ms(started: float) -> int:
    """Return milliseconds elapsed since a ``perf_counter`` reading.

    Args:
        started: Value returned by :func:`time.perf_counter`.

    Returns:
        Elapsed milliseconds, rounded down.
    """
    return int((time.perf_counter() - started) * 1000)


ModelCallStatus = Literal["ok", "error", "stub"]
"""Outcome of one model call: real success, real failure, or the offline stub."""


class ModelCallRecord(BaseModel):
    """One entry of ``tasks/<name>/model_calls.jsonl``.

    Field names match the JSONL line and the ``model_calls`` index table, so
    ``model_dump()`` is the log line verbatim.

    Deliberately absent: prompts, image contents and ``api_key``. The audit log
    must be safe to copy into a bug report, so it records *that* a call
    happened, not what was asked.

    Attributes:
        role: ``llm`` (plan) or ``vl`` (annotate).
        provider: Provider that served the call (``stub`` or ``remote``).
        model: Model name, empty for the stub.
        host: ``scheme://host:port`` of the endpoint, credentials and path
            stripped; empty for the stub.
        status: ``ok``, ``error``, or ``stub``.
        http_status: Response status when one was received, else ``None``.
        duration_ms: Wall-clock duration of the call.
        started_at: ISO 8601 UTC start timestamp.
    """

    role: Role
    provider: str
    model: str = ""
    host: str = ""
    status: ModelCallStatus
    http_status: int | None = None
    duration_ms: int = 0
    started_at: str


RecordHook = Callable[[ModelCallRecord], None]
"""Sink for :class:`ModelCallRecord`; supplied by :class:`~src.core.pipeline.Pipeline`."""


def host_of(base_url: str) -> str:
    """Reduce a base URL to ``scheme://host[:port]``.

    Embedding credentials in a URL is legal and common
    (``http://user:pass@host:11434/v1``); writing that verbatim would put a
    secret into an audit log and an index row. Only scheme, host, and port
    survive.

    Idempotent, so a record that already holds a reduced host can be handed
    back through this function without changing.

    Args:
        base_url: Raw endpoint URL.

    Returns:
        ``scheme://host:port``, or ``""`` when the URL has no parseable host.
    """
    raw = base_url.strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:  # malformed port, e.g. http://host:abc
        return ""
    host = parts.hostname
    if not host:
        return ""
    if ":" in host:  # bare IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    return f"{parts.scheme}://{netloc}" if parts.scheme else netloc


class RecordingClient:
    """Transparent wrapper that reports each ``generate`` call to a hook.

    Wraps the client an agent actually holds — the stub or
    :class:`OpenAICompatClient` — so the record reflects calls that really
    happened. In particular a stub plan step is recorded with
    ``status="stub"``: the labels and plans it produced are synthetic, and the
    audit log has to be able to say so months later.

    The hook is best-effort: :mod:`src.core.metadata_store` never raises, and
    this class additionally guards the hook call itself, because a failed audit
    write must never turn a successful model call into a failed pipeline step.

    Attributes:
        role: Role being recorded, echoed into every record.
    """

    def __init__(
        self,
        inner: VLClient,
        *,
        role: Role,
        provider: str,
        model: str = "",
        base_url: str = "",
        stub: bool = False,
        hook: RecordHook | None = None,
    ) -> None:
        """Initialize the recorder.

        Args:
            inner: Client to forward calls to.
            role: ``llm`` or ``vl``.
            provider: Provider label for the record.
            model: Model name for the record.
            base_url: Endpoint URL; reduced with :func:`host_of` before logging.
            stub: Whether ``inner`` is the offline stub, which decides whether a
                successful call is recorded as ``stub`` or ``ok``.
            hook: Sink for records; ``None`` disables recording entirely and
                makes this wrapper a pure pass-through.
        """
        self._inner: VLClient = inner
        self.role: Role = role
        self._provider: str = provider
        self._model: str = model
        self._host: str = host_of(base_url)
        self._stub: bool = stub
        self._hook: RecordHook | None = hook

    @property
    def inner(self) -> VLClient:
        """Return the wrapped client.

        Returns:
            The client calls are forwarded to.
        """
        return self._inner

    @property
    def is_stub(self) -> bool:
        """Report whether the wrapped client is the offline stub.

        Returns:
            ``True`` when successful calls are recorded as ``stub``.
        """
        return self._stub

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        image_path: Path | None = None,
        max_new_tokens: int = 2048,
    ) -> str:
        """Forward a generation call and record its outcome.

        Args:
            system_prompt: System message content.
            user_prompt: User message text.
            image_path: Optional image for vision requests.
            max_new_tokens: Generation budget.

        Returns:
            Whatever the wrapped client returned.

        Raises:
            Exception: Whatever the wrapped client raised, re-raised unchanged.
        """
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        started = time.perf_counter()
        status: ModelCallStatus = "ok"
        try:
            text = self._inner.generate(
                system_prompt, user_prompt, image_path=image_path, max_new_tokens=max_new_tokens
            )
        except Exception:
            status = "error"
            raise
        else:
            if self._stub:
                status = "stub"
            return text
        finally:
            self._record(status, started_at, started)

    def _record(self, status: ModelCallStatus, started_at: str, started: float) -> None:
        """Emit one record, swallowing any failure of the hook itself.

        Args:
            status: Outcome of the call.
            started_at: ISO 8601 UTC start timestamp.
            started: ``perf_counter`` reading taken before the call.
        """
        if self._hook is None:
            return
        record = ModelCallRecord(
            role=self.role,
            provider=self._provider,
            model=self._model,
            host=self._host,
            status=status,
            http_status=getattr(self._inner, "last_http_status", None),
            duration_ms=_elapsed_ms(started),
            started_at=started_at,
        )
        try:
            self._hook(record)
        except Exception as exc:  # audit must never break a run
            logger.warning("Failed to record model call: %s", exc)
