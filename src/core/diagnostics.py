"""Configuration self-check behind ``run.py doctor`` and ``GET /api/diagnostics``.

The report answers one question a user cannot otherwise answer: *is this
deployment actually going to produce real labels?* The default configuration
runs the offline stub, which emits deterministic fake boxes and looks exactly
like a working system from the outside, so the check that flags it has to be
loud in the UI while staying non-fatal on the command line — if the default
configuration failed ``doctor``, nobody would keep running it.

Two properties are deliberate:

* ``deep=False`` (the default) touches nothing but local files: no network
  calls, no log writes. The GUI polls this.
* Probe results never abort the run; a dead endpoint is a ``fail`` check, not an
  exception.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from src.agents.model_client import ProbeResult, probe_endpoint
from src.config import Role, Settings, find_plaintext_secrets
from src.core.config_store import ROLES, ConfigStore, ConfigValue
from src.core.task_manager import TaskManager
from src.utils.audit_log import append_record
from src.utils.secrets import SecretStoreState
from src.utils.yaml_utils import load_yaml

__all__ = [
    "CheckResult",
    "CheckStatus",
    "DiagnosticReport",
    "Doctor",
    "ProbeResult",
]

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "warn", "fail", "skip"]
"""Severity of one check; the report's status is the worst of them."""

_STATUS_RANK: dict[str, int] = {"skip": 0, "ok": 1, "warn": 2, "fail": 3}
"""Ordering used to aggregate checks into a single status."""

REQUIRED_PROMPTS: tuple[str, ...] = ("plan_agent.yaml", "annotate_agent.yaml", "inspect_agent.yaml")
"""Prompt files every agent needs; a missing one stops the pipeline outright."""

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


class CheckResult(BaseModel):
    """One diagnostic check.

    Attributes:
        id: Stable identifier; part of the API contract.
        status: Severity.
        summary: One-line conclusion.
        detail: Supporting detail, e.g. the offending path.
        hint: Actionable Chinese fix suggestion; empty when nothing is wrong.
    """

    id: str
    status: CheckStatus
    summary: str
    detail: str = ""
    hint: str = ""


class DiagnosticReport(BaseModel):
    """Aggregated result of a self-check run.

    Attributes:
        status: Worst check status, by ``fail > warn > ok > skip``.
        generated_at: ISO 8601 UTC timestamp.
        checks: All checks, in a stable order.
        probes: Connectivity results, present only for a deep run.
    """

    status: CheckStatus
    generated_at: str
    checks: list[CheckResult]
    probes: list[ProbeResult] = []


class Doctor:
    """Run the platform's self-checks.

    Attributes:
        _settings: Effective settings under test.
        _tasks: Task manager, used to check the tasks root.
        _config_dir: Configuration directory under test.
        _config: Config store, for effective values and the encrypted store.
        _transport: httpx transport used by deep probes; ``None`` uses the real
            network. Tests inject a mock here.
    """

    def __init__(
        self,
        settings: Settings,
        task_manager: TaskManager,
        config_dir: Path,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the doctor to the deployment it inspects.

        Args:
            settings: Effective settings.
            task_manager: Task manager, for the tasks-root check.
            config_dir: Directory holding the configuration files.
            transport: httpx transport for deep probes; tests inject a mock so
                the suite never touches the network.
        """
        self._settings = settings
        self._tasks = task_manager
        self._config_dir = config_dir
        self._config = ConfigStore(settings, config_dir)
        self._transport = transport

    def run(
        self,
        *,
        deep: bool = False,
        roles: Sequence[Role] = ROLES,
    ) -> DiagnosticReport:
        """Run every check and aggregate the results.

        Args:
            deep: Also probe each role's endpoint. This is the only mode that
                performs network I/O and the only one that appends to
                ``<data_dir>/logs/diagnostics.jsonl``.
            roles: Roles to inspect.

        Returns:
            A :class:`DiagnosticReport`.
        """
        effective = self._config.effective()
        checks: list[CheckResult] = []

        for role in roles:
            view = effective.llm if role == "llm" else effective.vl
            checks.extend(_model_checks(role, view.provider.value, view.base_url.value, view.model.value, view.has_api_key))

        checks.extend(self._path_checks(effective.storage.config_writable, effective.storage.config_persistent))
        checks.append(self._secret_store_check())
        checks.append(self._auth_token_check())
        checks.append(self._tasks_root_check())
        checks.append(self._prompts_check())
        checks.append(self._database_check())

        probes: list[ProbeResult] = []
        for role in roles:
            probe = self._connectivity_check(role, deep)
            checks.append(probe[0])
            if probe[1] is not None:
                probes.append(probe[1])

        report = DiagnosticReport(
            status=_aggregate(checks),
            generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            checks=checks,
            probes=probes,
        )
        if deep:
            self._write_history(report)
        return report

    def _connectivity_check(self, role: Role, deep: bool) -> tuple[CheckResult, ProbeResult | None]:
        """Probe one role's endpoint, or explain why it was skipped.

        Probes run one after the other on purpose: launching both at once makes
        two 8GB-VRAM endpoints compete and turns ``latency_ms`` into noise.

        Args:
            role: Role to probe.
            deep: Whether probing is enabled.

        Returns:
            ``(check, probe)``; ``probe`` is ``None`` when nothing was probed.
        """
        check_id = f"model.{role}.connectivity"
        model = self._settings.llm if role == "llm" else self._settings.vl

        if model.provider != "remote":
            return (
                CheckResult(
                    id=check_id,
                    status="skip",
                    summary=f"{role} 使用离线 stub，未探测端点。",
                    detail=f"provider={model.provider}",
                ),
                None,
            )
        if not model.base_url:
            return (
                CheckResult(
                    id=check_id,
                    status="skip",
                    summary=f"{role} 未配置 base_url，无法探测。",
                ),
                None,
            )
        if not deep:
            return (
                CheckResult(
                    id=check_id,
                    status="skip",
                    summary=f"{role} 未执行探测（deep=false）。",
                    hint="需要连通性验证时使用 `run.py doctor --deep` 或 `GET /api/diagnostics?deep=true`。",
                ),
                None,
            )

        probe = probe_endpoint(model, role=role, transport=self._transport)
        check = CheckResult(
            id=check_id,
            status="ok" if probe.status == "ok" else "fail",
            summary=probe.message or f"{role} 端点探测完成。",
            detail=f"http_status={probe.http_status} latency_ms={probe.latency_ms}",
            hint=probe.hint,
        )
        return check, probe

    def _path_checks(self, writable: bool, persistent: bool) -> list[CheckResult]:
        """Check configuration-directory writability and persistence.

        Args:
            writable: Result of the write-access check.
            persistent: Result of the container persistence check.

        Returns:
            The path-related checks.
        """
        checks = [
            CheckResult(
                id="config.paths.config_writable",
                status="ok" if writable else "warn",
                summary="配置目录可写，设置页可保存。" if writable else "配置目录不可写，设置页无法保存。",
                detail=str(self._config_dir),
                hint="" if writable else "改用环境变量（VL_VL_PROVIDER / VL_VL_BASE_URL 等）配置，或给配置目录写权限。",
            ),
            CheckResult(
                id="config.paths.config_persistent",
                status="ok" if persistent else "warn",
                summary=(
                    "配置目录位于真实文件系统，写入可持久保存。"
                    if persistent
                    else "配置目录来自容器镜像层，写回的内容在容器重建后丢失。"
                ),
                detail=str(self._config_dir),
                hint=""
                if persistent
                else "在 docker-compose.yml 中挂载 ./config:/app/config，或改用环境变量配置。",
            ),
        ]
        plaintext = self._plaintext_secret_check()
        if plaintext is not None:
            checks.append(plaintext)
        return checks

    def _plaintext_secret_check(self) -> CheckResult | None:
        """Check for a plaintext key in a version-controlled YAML file.

        Returns:
            A ``warn`` check when one is present, otherwise ``None``.
        """
        flagged = find_plaintext_secrets(self._config_dir)
        if not flagged:
            return None
        return CheckResult(
            id="config.paths.overrides_plaintext_secret",
            status="warn",
            summary="配置文件中存在明文 api_key，该键被忽略。",
            detail=", ".join(flagged),
            hint=(
                "YAML 受版本控制，明文密钥等于准备提交一个活凭据。请删除该键，改用环境变量"
                "（含 *_FILE）或设置页保存到加密密文库。"
            ),
        )

    def _secret_store_check(self) -> CheckResult:
        """Check the encrypted store's usability.

        "No master key" is a ``skip``, not a failure: the platform is fully
        usable on environment secrets, and failing here would make ``doctor``
        red on every deployment that does not need the store.

        Returns:
            The secret-store check.
        """
        state = self._config.store.state()
        check_id = "config.paths.secret_store"
        if state is SecretStoreState.NO_MASTER_KEY:
            return CheckResult(
                id=check_id,
                status="skip",
                summary="未启用加密密文库，密钥仅可来自环境变量。",
                detail=self._config.store.describe_state(),
            )
        if state is SecretStoreState.OK:
            return CheckResult(
                id=check_id,
                status="ok",
                summary="加密密文库可用。",
                detail=f"{self._config.store.path()}（主密钥来自 {self._config.store.master_key_source()}）",
            )
        return CheckResult(
            id=check_id,
            status="fail",
            summary="加密密文库不可用。",
            detail=self._config.store.describe_state(),
            hint=(
                "主密钥不匹配时：恢复原主密钥，或删除 config/secrets.db 后重新录入密钥。"
                "路径冲突时：把 VL_ANCHOR_DB_PATH 指回 index.db（密文库与索引库不能是同一个文件）。"
            ),
        )

    def _auth_token_check(self) -> CheckResult:
        """Check whether the API is protected appropriately.

        Never returns ``fail``: on the default loopback bind, no token is the
        right answer, and a failing check would make ``doctor`` useless.

        Returns:
            The auth-token check.
        """
        check_id = "config.server.auth_token"
        host = self._settings.host
        if not self._settings.auth_token:
            if host in _LOOPBACK_HOSTS:
                return CheckResult(
                    id=check_id,
                    status="skip",
                    summary="未启用 API 鉴权（仅本机访问，可接受）。",
                    detail=f"host={host}",
                )
            return CheckResult(
                id=check_id,
                status="warn",
                summary="服务监听非回环地址但未启用 API 鉴权，同网段任何人可读写任务与配置。",
                detail=f"host={host}",
                hint="设置 VL_ANCHOR_AUTH_TOKEN（推荐用 *_FILE 指向挂载的密钥文件），或在 GUI 前置带鉴权的反向代理。",
            )
        if self._settings.auth_token_source == "VL_ANCHOR_AUTH_TOKEN" and not self._from_env("VL_ANCHOR_AUTH_TOKEN"):
            return CheckResult(
                id=check_id,
                status="warn",
                summary="API 鉴权 token 来自加密密文库：客户端丢失 token 后将无法访问设置页。",
                detail="source=secrets",
                hint=(
                    "请把 token 另行留存。若已被锁在外面：改用 VL_ANCHOR_AUTH_TOKEN[_FILE] 提供 token，"
                    "或删除 config/secrets.db 关闭鉴权后重新配置。"
                ),
            )
        return CheckResult(
            id=check_id,
            status="ok",
            summary="API 鉴权已启用（token 来自环境变量）。",
            detail=f"source={self._settings.auth_token_source}",
        )

    @staticmethod
    def _from_env(name: str) -> bool:
        """Report whether a secret came from the environment.

        Args:
            name: Base variable name.

        Returns:
            ``True`` when the variable or its ``_FILE`` twin is set.
        """
        return bool(os.environ.get(name) or os.environ.get(f"{name}_FILE"))

    def _tasks_root_check(self) -> CheckResult:
        """Check that the tasks root exists and accepts writes.

        Returns:
            The tasks-root check.
        """
        root = self._settings.tasks_root
        if not root.is_dir():
            return CheckResult(
                id="storage.tasks_root",
                status="fail",
                summary="任务目录不存在。",
                detail=str(root),
                hint="创建该目录（或设置 VL_ANCHOR_TASKS_ROOT / VL_ANCHOR_DATA_DIR 指向可写位置）。",
            )
        if not os.access(root, os.W_OK):
            return CheckResult(
                id="storage.tasks_root",
                status="fail",
                summary="任务目录不可写，流水线无法产出标注与数据集。",
                detail=str(root),
                hint="修正挂载目录权限；容器内常见原因是宿主机目录属主与容器用户不一致。",
            )
        count = len(self._tasks.list_tasks())
        return CheckResult(
            id="storage.tasks_root",
            status="ok",
            summary=f"任务目录可写，现有 {count} 个任务。",
            detail=str(root),
        )

    def _prompts_check(self) -> CheckResult:
        """Check that every agent prompt file exists and carries a system prompt.

        A missing prompt file aborts the pipeline mid-run, so this is a ``fail``
        rather than a warning.

        Returns:
            The prompts check.
        """
        check_id = "storage.prompts"
        prompts_dir = self._settings.prompts_dir
        missing: list[str] = []
        broken: list[str] = []
        for filename in REQUIRED_PROMPTS:
            path = prompts_dir / filename
            if not path.is_file():
                missing.append(filename)
                continue
            try:
                data = load_yaml(path)
            except (OSError, ValueError):
                broken.append(filename)
                continue
            if not str(data.get("system_prompt", "")).strip():
                broken.append(filename)

        if missing or broken:
            parts = []
            if missing:
                parts.append(f"缺失：{', '.join(missing)}")
            if broken:
                parts.append(f"缺少 system_prompt 或无法解析：{', '.join(broken)}")
            return CheckResult(
                id=check_id,
                status="fail",
                summary="Agent 提示词不完整，流水线无法运行。",
                detail="；".join(parts),
                hint=f"这些提示词不应被硬编码，请从版本库恢复 {prompts_dir} 下的 YAML 文件。",
            )
        return CheckResult(
            id=check_id,
            status="ok",
            summary=f"{len(REQUIRED_PROMPTS)} 个 Agent 提示词齐备。",
            detail=str(prompts_dir),
        )

    def _database_check(self) -> CheckResult:
        """Check the metadata index location.

        The index is best-effort by design, so an unwritable path is a ``warn``:
        the pipeline works without it, and failing would overstate its role.

        Returns:
            The database check.
        """
        check_id = "storage.db"
        if not self._settings.db_enabled:
            return CheckResult(
                id=check_id,
                status="skip",
                summary="元数据索引已关闭（VL_ANCHOR_DB_ENABLED=false）。",
            )
        db_path = self._settings.db_path
        target_dir = db_path.parent if str(db_path.parent) else Path(".")
        if not target_dir.is_dir() or not os.access(target_dir, os.W_OK):
            return CheckResult(
                id=check_id,
                status="warn",
                summary="索引库所在目录不可写，执行历史与统计将不可用。",
                detail=str(db_path),
                hint="把 VL_ANCHOR_DB_PATH 指向可写位置（容器内默认落在 vl-data 卷）；索引可随时删除重建，不影响任务数据。",
            )
        return CheckResult(
            id=check_id,
            status="ok",
            summary="索引库位置可写。",
            detail=str(db_path),
        )

    def _write_history(self, report: DiagnosticReport) -> None:
        """Append the full report to the diagnostics audit log.

        Best-effort: an unwritable log must not fail the self-check.

        Args:
            report: Report to record.
        """
        path = self._settings.logs_dir / "diagnostics.jsonl"
        record = report.model_dump(mode="json")
        if not append_record(path, record):
            logger.warning("Diagnostics history not written to %s", path)


def _model_checks(
    role: Role,
    provider: ConfigValue,
    base_url: ConfigValue,
    model: ConfigValue,
    has_api_key: bool,
) -> list[CheckResult]:
    """Check one role's model configuration for completeness.

    Args:
        role: Role under test.
        provider: Effective provider value.
        base_url: Effective base_url value.
        model: Effective model name.
        has_api_key: Whether a key is configured.

    Returns:
        That role's configuration checks.
    """
    provider_str = str(provider or "")
    base_url_str = str(base_url or "")
    model_str = str(model or "")

    if provider_str == "stub":
        provider_check = CheckResult(
            id=f"config.{role}.provider",
            status="warn",
            summary=f"{role} 使用离线 stub：产出的是确定性假框，禁止用于训练。",
            detail="provider=stub",
            hint=(
                "在设置页把 provider 改为 remote 并填写 base_url / model（VL_VL_* 用于标注，VL_LLM_* 用于规划），"
                "或保持 stub 仅用于跑通流程。"
            ),
        )
    else:
        provider_check = CheckResult(
            id=f"config.{role}.provider",
            status="ok",
            summary=f"{role} 使用远程模型端点。",
            detail=f"provider={provider_str}",
        )

    remote = provider_str == "remote"
    checks = [
        provider_check,
        CheckResult(
            id=f"config.{role}.base_url",
            status="fail" if remote and not base_url_str else "ok",
            summary=(
                "provider=remote 但未配置 base_url，运行时将回退到 stub 假框。"
                if remote and not base_url_str
                else f"base_url={base_url_str or '(不适用)'}"
            ),
            detail=f"base_url={base_url_str}",
            hint="填写 OpenAI 兼容端点根地址，例如 http://127.0.0.1:8000/v1。" if remote and not base_url_str else "",
        ),
        CheckResult(
            id=f"config.{role}.model",
            status="fail" if remote and not model_str else "ok",
            summary=(
                "provider=remote 但未配置 model 名，运行时将回退到 stub 假框。"
                if remote and not model_str
                else f"model={model_str or '(不适用)'}"
            ),
            detail=f"model={model_str}",
            hint="填写服务端实际加载的模型名（见 /v1/models）。" if remote and not model_str else "",
        ),
        CheckResult(
            id=f"config.{role}.api_key",
            status="warn" if remote and not has_api_key else "ok",
            summary=(
                "未配置 api_key；自建端点通常不需要，托管服务会返回 401。"
                if remote and not has_api_key
                else ("已配置 api_key。" if has_api_key else "未配置 api_key（当前 provider 不需要）。")
            ),
            hint="若端点返回 401/403，请在设置页填入 key（保存后进入加密密文库）。"
            if remote and not has_api_key
            else "",
        ),
    ]
    return checks


def _aggregate(checks: Sequence[CheckResult]) -> CheckStatus:
    """Reduce check statuses to the report's overall status.

    Args:
        checks: All checks.

    Returns:
        The worst status, by ``fail > warn > ok > skip``; ``skip`` when empty.
    """
    worst = max(checks, key=lambda check: _STATUS_RANK[check.status], default=None)
    return worst.status if worst is not None else "skip"
