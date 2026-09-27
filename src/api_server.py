"""FastAPI server exposing the pipeline over HTTP for the GUI.

Run with: ``uv run uvicorn src.api_server:app --port 8765``
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src.agents.model_client import ProbeResult, probe_endpoint
from src.config import Role, get_settings
from src.core.config_store import ROLES, ConfigPatch, ConfigStore, EffectiveConfig, WriteResult
from src.core.diagnostics import DiagnosticReport, Doctor
from src.core.metadata_store import IndexStats, MetadataStore, TaskHistory, read_task_history
from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager
from src.utils.file_utils import list_images
from src.utils.obb_utils import is_coords_in_range
from src.utils.yaml_utils import load_yaml

app = FastAPI(title="VL-YOLO-Anchor API", version="0.1.0")
logger = logging.getLogger("api_server")

_settings = get_settings()

# The GUI runs on the Vite dev server (localhost:5173), inside the Tauri
# WebView, or (in Docker) as a static site served by nginx. All of these are
# cross-origin relative to this backend, so CORS must allow them or the browser
# silently blocks every response. The allowed origins are configurable via
# VL_ANCHOR_CORS_ORIGINS so a deployment can list its own public URL.
# PUT is required by the settings page; without it the browser's preflight fails
# even though curl and TestClient would happily send the request.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)

_TASKS_ROOT = _settings.tasks_root
_task_manager = TaskManager(_TASKS_ROOT)
_config_store = ConfigStore(_settings, _settings.config_dir)
_doctor = Doctor(_settings, _task_manager, _settings.config_dir)
_metadata_store = MetadataStore(_settings.db_path, enabled=_settings.db_enabled)
_pipeline = Pipeline(
    _task_manager,
    prompts_dir=_settings.prompts_dir,
    llm_settings=_settings.llm,
    vl_settings=_settings.vl,
    metadata_store=_metadata_store,
)

# Optional bearer-token auth for network-exposed deployments. When
# VL_ANCHOR_AUTH_TOKEN is unset the middleware stays disabled so local desktop
# use and the test-suite keep working without a token.
_PUBLIC_PATHS = {"/api/health"}


def _is_preflight(request: Request) -> bool:
    """Report whether a request is a browser CORS preflight.

    A preflight is an ``OPTIONS`` request carrying ``Access-Control-Request-Method``,
    and browsers never attach the ``Authorization`` header to it. Rejecting it with
    401 makes the browser report a CORS failure for requests that would have been
    authenticated fine, so a deployment with a token configured could not use the
    settings page at all.

    Args:
        request: Incoming request.

    Returns:
        True for preflight requests, which bypass the auth check.
    """
    return request.method == "OPTIONS" and "access-control-request-method" in request.headers


@app.middleware("http")
async def auth_middleware(request: Request, call_next: Any) -> Any:
    """Reject unauthenticated writes/reads when an auth token is configured.

    Args:
        request: Incoming request.
        call_next: Downstream handler.

    Returns:
        The handler response, or a 401 JSON response when the token is missing
        or wrong.
    """
    token = _settings.auth_token
    path = request.url.path
    if token and path.startswith("/api") and path not in _PUBLIC_PATHS and not _is_preflight(request):
        provided = request.headers.get("authorization", "")
        if provided != f"Bearer {token}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)

_LABEL_SOURCE = Literal["candidate_labels", "ai_labels"]


class TaskCreateRequest(BaseModel):
    """Request body for creating a task."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]*$",
        description="Unique task name (directory-safe: letters, digits, _ and -)",
    )
    description: str = Field("", description="Natural-language task description")


class TaskCreatedResponse(BaseModel):
    """Response after task creation."""

    name: str
    path: str


class StepRequest(BaseModel):
    """Request body for running a pipeline step."""

    step: Literal["plan", "annotate", "inspect", "split"]


class StepResponse(BaseModel):
    """Response after running a pipeline step."""

    task: str
    step: str
    result: dict[str, Any]


class ImageItem(BaseModel):
    """A single image entry in a task."""

    name: str
    url: str


class InspectionReportResponse(BaseModel):
    """Inspection report payload."""

    report: dict[str, Any]


class OBBBoxModel(BaseModel):
    """A single oriented bounding box read from a label file."""

    cls: int
    points: list[float] = Field(..., min_length=8, max_length=8)
    conf: float = 1.0


class LabelBoxesResponse(BaseModel):
    """Per-image label payload served to the annotation review canvas."""

    image: str
    boxes: list[OBBBoxModel]
    source: _LABEL_SOURCE


class LabeledImagesResponse(BaseModel):
    """Names of images that have a label file (candidate or AI)."""

    images: list[str]


class ConfigTestRequest(BaseModel):
    """Request body for probing one model endpoint.

    The three overrides exist so the settings page can validate a key the user
    has typed but not yet saved. Anything omitted falls back to the currently
    effective value, so "test what is configured" is an empty body plus a role.
    """

    role: str = Field(..., description="Model role to probe: 'llm' or 'vl'")
    base_url: str | None = Field(None, description="Endpoint to try instead of the configured one")
    model: str | None = Field(None, description="Model name to try instead of the configured one")
    api_key: str | None = Field(None, description="Key to try instead of the configured one")


def _require_task(name: str) -> Path:
    """Resolve a task directory, rejecting unknown tasks without side effects.

    Unlike :meth:`TaskManager.task_dir`, this never creates a directory: a
    GET for a non-existent task must not leave a stray folder behind.

    Args:
        name: Task name from a path parameter.

    Returns:
        The existing task directory.

    Raises:
        HTTPException: 404 if the task does not exist (or its ``task.yaml``
            is missing, i.e. the directory is not a real task).
    """
    task_dir = _task_manager.task_dir(name)
    if not (task_dir / "task.yaml").is_file():
        raise HTTPException(status_code=404, detail=f"Task not found: {name}")
    return task_dir


def _safe_child(base_dir: Path, filename: str) -> Path:
    """Join a client-supplied file name under ``base_dir`` with traversal guard.

    Rejects any value that is not a bare file name (separators ``/`` or ``\\``,
    ``..``, absolute paths) and, as a belt-and-braces check, verifies the
    resolved target still lives under ``base_dir``. On Windows the Starlette
    path regex ``[^/]+`` still admits backslashes, so the ``Path(name).name``
    comparison is what actually blocks ``..\\..\\secret``.

    Args:
        base_dir: Directory the file must resolve within.
        filename: Client-supplied file name.

    Returns:
        The validated ``base_dir / filename`` path.

    Raises:
        HTTPException: 400 if the name attempts to escape ``base_dir``.
    """
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail=f"Invalid file name: {filename}")
    base = base_dir.resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail=f"Invalid file name: {filename}")
    return target


def _allowed_classes(task_dir: Path) -> set[int] | None:
    """Read the task's declared class ids from its plan.

    Args:
        task_dir: Task directory.

    Returns:
        Set of allowed integer class ids, or ``None`` when the plan is absent
        or declares no classes (in which case class filtering is skipped).
    """
    plan_path = task_dir / "plan.yaml"
    if not plan_path.is_file():
        return None
    try:
        classes = load_yaml(plan_path).get("classes", {})
    except Exception:  # noqa: BLE001 - a broken plan must not break label reads
        logger.warning("Could not read classes from %s", plan_path)
        return None
    if not isinstance(classes, dict) or not classes:
        return None
    return {int(k) for k in classes}


def _parse_label_file(path: Path, allowed_classes: set[int] | None = None) -> list[OBBBoxModel]:
    """Parse a YOLO-OBB label file into structured boxes.

    Lines have the format ``cls x1 y1 x2 y2 x3 y3 x4 y4`` with normalized
    coordinates. Malformed lines, lines whose coordinates fall outside
    ``[0, 1]``, and lines whose class is not in ``allowed_classes`` are skipped
    with a warning. The file is decoded as UTF-8 with BOM stripped and invalid
    bytes replaced, so a stray encoding never aborts the whole request.

    Args:
        path: Label file path.
        allowed_classes: Class ids accepted by the task plan, or ``None`` to
            skip class filtering.

    Returns:
        Parsed boxes; empty list for an empty file.

    Raises:
        OSError: If the file cannot be read at all (surfaced as HTTP 422).
    """
    boxes: list[OBBBoxModel] = []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if not parts:
            continue
        try:
            cls = int(parts[0])
            points = [float(v) for v in parts[1:]]
        except ValueError:
            logger.warning("Skipping malformed label line in %s:%d: %r", path.name, lineno, line)
            continue
        if len(points) != 8:
            logger.warning(
                "Skipping label line with %d coords in %s:%d: %r", len(points), path.name, lineno, line
            )
            continue
        if not is_coords_in_range(points):
            logger.warning("Skipping out-of-range coords in %s:%d: %r", path.name, lineno, line)
            continue
        if allowed_classes is not None and cls not in allowed_classes:
            logger.warning("Skipping class %d not in task plan in %s:%d", cls, path.name, lineno)
            continue
        boxes.append(OBBBoxModel(cls=cls, points=points, conf=1.0))
    return boxes


def _find_label_file(task_dir: Path, image_name: str) -> tuple[Path, _LABEL_SOURCE] | None:
    """Locate the best label file for an image (candidate labels first).

    Args:
        task_dir: Task directory.
        image_name: Image file name; its stem selects the label.

    Returns:
        ``(label_path, source)`` tuple, or None when no label exists.
    """
    stem = Path(image_name).stem
    for sub in ("candidate_labels", "ai_labels"):
        candidate = task_dir / sub / f"{stem}.txt"
        if candidate.is_file():
            source: _LABEL_SOURCE = sub
            return candidate, source
    return None


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness probe for Docker HEALTHCHECK and load balancers.

    Returns:
        A small static payload with status ``ok``.
    """
    return {"status": "ok"}


@app.get("/api/tasks")
def list_tasks() -> list[str]:
    """List all task names.

    Returns:
        Sorted list of task names.
    """
    return _task_manager.list_tasks()


@app.post("/api/tasks", response_model=TaskCreatedResponse)
def create_task(request: TaskCreateRequest) -> TaskCreatedResponse:
    """Create a new task.

    Args:
        request: Task name and description.

    Returns:
        Created task name and path.

    Raises:
        HTTPException: 409 if the task already exists.
    """
    try:
        task_dir = _task_manager.create_task(request.name, request.description)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TaskCreatedResponse(name=request.name, path=str(task_dir))


@app.post("/api/tasks/{name}/step", response_model=StepResponse)
def run_step(name: str, request: StepRequest) -> StepResponse:
    """Run one pipeline step for a task.

    Args:
        name: Task name.
        request: Step to run (plan/annotate/inspect/split).

    Returns:
        Step result payload.

    Raises:
        HTTPException: 404 if the task is missing, 500 on step failure.
    """
    _require_task(name)
    try:
        result = _pipeline.run_step(name, request.step)
    except Exception as exc:  # noqa: BLE001 - surface agent errors to the GUI
        logger.exception("Step '%s' failed for task '%s'", request.step, name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not isinstance(result, dict):
        result = {"items": [str(p) for p in result]}
    return StepResponse(task=name, step=request.step, result=result)


@app.post("/api/tasks/{name}/plan", response_model=StepResponse)
def run_plan(name: str) -> StepResponse:
    """Run the plan step for a task.

    Args:
        name: Task name.

    Returns:
        Plan result payload.
    """
    return run_step(name, StepRequest(step="plan"))


@app.post("/api/tasks/{name}/annotate", response_model=StepResponse)
def run_annotate(name: str) -> StepResponse:
    """Run the annotate step for a task.

    Args:
        name: Task name.

    Returns:
        Annotation result payload.
    """
    return run_step(name, StepRequest(step="annotate"))


@app.post("/api/tasks/{name}/inspect", response_model=StepResponse)
def run_inspect(name: str) -> StepResponse:
    """Run the inspect step for a task.

    Args:
        name: Task name.

    Returns:
        Inspection result payload.
    """
    return run_step(name, StepRequest(step="inspect"))


@app.get("/api/tasks/{name}/report", response_model=InspectionReportResponse)
def get_report(name: str) -> InspectionReportResponse:
    """Return the latest inspection report for a task.

    Args:
        name: Task name.

    Returns:
        Report dict.

    Raises:
        HTTPException: 404 if the task or report is missing.
    """
    report_path = _require_task(name) / "inspection_report.yaml"
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail=f"No inspection report for task: {name}")
    return InspectionReportResponse(report=load_yaml(report_path))


@app.get("/api/tasks/{name}/plan")
def get_plan(name: str) -> dict[str, Any]:
    """Return the current training plan for a task.

    Args:
        name: Task name.

    Returns:
        ``training_plan`` section of the task config.

    Raises:
        HTTPException: 404 if the task is missing.
    """
    _require_task(name)
    plan: dict[str, Any] = _task_manager.load_task(name).get("training_plan", {})
    return plan


@app.get("/api/tasks/{name}/export")
def get_export_summary(name: str) -> dict[str, Any]:
    """Return the dataset export summary produced by the split step.

    Args:
        name: Task name.

    Returns:
        Export summary dict.

    Raises:
        HTTPException: 404 if the task or export summary is missing.
    """
    summary_path = _require_task(name) / "dataset" / "export_summary.yaml"
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail=f"No dataset export for task: {name}")
    return load_yaml(summary_path)


@app.get("/api/tasks/{name}/labels", response_model=LabeledImagesResponse)
def list_labeled_images(name: str) -> LabeledImagesResponse:
    """List images in a task that have a label file.

    Args:
        name: Task name.

    Returns:
        Sorted image names with at least one label file.

    Raises:
        HTTPException: 404 if the task is missing.
    """
    task_dir = _require_task(name)
    labeled = [
        p.name
        for p in list_images(task_dir / "images")
        if _find_label_file(task_dir, p.name) is not None
    ]
    return LabeledImagesResponse(images=labeled)


@app.get("/api/tasks/{name}/labels/{image_name}", response_model=LabelBoxesResponse)
def get_label_boxes(name: str, image_name: str) -> LabelBoxesResponse:
    """Return the OBB boxes for one image (candidate labels take priority).

    Args:
        name: Task name.
        image_name: Image file name.

    Returns:
        Parsed boxes plus the label source directory.

    Raises:
        HTTPException: 400 on an unsafe file name, 404 if the task, image, or
            label file is missing, 422 if the label file is unreadable.
    """
    task_dir = _require_task(name)
    image_path = _safe_child(task_dir / "images", image_name)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image not found: {image_name}")
    found = _find_label_file(task_dir, image_name)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No label file for image: {image_name}")
    label_path, source = found
    try:
        boxes = _parse_label_file(label_path, _allowed_classes(task_dir))
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Unreadable label file: {label_path.name}") from exc
    return LabelBoxesResponse(image=image_name, boxes=boxes, source=source)


@app.get("/api/tasks/{name}/images", response_model=list[ImageItem])
def get_images(name: str) -> list[ImageItem]:
    """List images in a task.

    Args:
        name: Task name.

    Returns:
        Image entries with API URLs.

    Raises:
        HTTPException: 404 if the task is missing.
    """
    task_dir = _require_task(name)
    images = list_images(task_dir / "images")
    return [ImageItem(name=p.name, url=f"/api/tasks/{name}/images/{p.name}") for p in images]


@app.get("/api/tasks/{name}/images/{image_name}")
def get_image(name: str, image_name: str) -> FileResponse:
    """Serve one image file from a task.

    Args:
        name: Task name.
        image_name: Image file name.

    Returns:
        The image file response.

    Raises:
        HTTPException: 400 on an unsafe file name, 404 if the task or image is
            missing.
    """
    task_dir = _require_task(name)
    image_path = _safe_child(task_dir / "images", image_name)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image not found: {image_name}")
    return FileResponse(image_path)


@app.get("/api/tasks/{name}/history", response_model=TaskHistory)
def get_task_history(name: str, limit: int = 50) -> TaskHistory:
    """Return a task's execution history, read from the on-disk audit logs.

    Served from ``tasks/<name>/*.jsonl`` rather than the SQLite index: the logs
    are the source of truth, so history stays correct even when the index is
    missing or stale, and a task that never ran simply returns two empty lists.

    Args:
        name: Task name.
        limit: Maximum entries per log, clamped by the metadata layer.

    Returns:
        Run and model-call entries, newest first.

    Raises:
        HTTPException: 404 if the task does not exist.
    """
    task_dir = _require_task(name)
    return read_task_history(task_dir, task=name, limit=limit)


@app.get("/api/index/stats", response_model=IndexStats)
def get_index_stats() -> IndexStats:
    """Return cross-task counts from the metadata index.

    Unlike ``/api/tasks/{name}/history``, this reads SQLite: aggregating across
    tasks is what the index is for. It is also the only endpoint that depends on
    the database, so it is the only one that fails when the database is broken.

    Returns:
        Index row counts and availability.

    Raises:
        HTTPException: 503 if indexing is disabled or the database is unusable.
    """
    try:
        stats = _metadata_store.stats()
    except sqlite3.Error as exc:  # defensive: stats() reports rather than raises
        raise HTTPException(status_code=503, detail=f"Metadata index unavailable: {exc}") from exc
    if not stats.available:
        raise HTTPException(status_code=503, detail=f"Metadata index unavailable: {stats.db_path}")
    return stats


@app.get("/api/config", response_model=EffectiveConfig)
def get_config() -> EffectiveConfig:
    """Return the effective configuration with per-field provenance.

    Model keys are never included: ``api_key`` reports ``has_api_key`` and a
    masked hint only.

    Returns:
        The effective configuration.
    """
    return _config_store.effective()


@app.put("/api/config", response_model=WriteResult)
def update_config(patch: ConfigPatch) -> Any:
    """Apply configuration changes to ``overrides.yaml`` and the secret store.

    Args:
        patch: Per-role changes; omitted fields are left untouched.

    Returns:
        A :class:`WriteResult` listing what was written and what was skipped.
        A body of ``{}`` (all fields ignored) is returned with status 409.

    Raises:
        HTTPException: 422 when the patch is empty or the merged result would be
            invalid; 409 as described above.
    """
    errors = _config_store.validate_patch(patch)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    result = _config_store.write(patch)
    if not result.written and result.ignored:
        # Nothing at all could be written (env-locked fields, read-only config
        # dir, no secret store). 200 here would tell the GUI "saved" about a
        # configuration that did not change.
        return JSONResponse(status_code=409, content=result.model_dump())
    return result


@app.post("/api/config/test", response_model=ProbeResult)
def test_config(request: ConfigTestRequest) -> ProbeResult:
    """Probe one model endpoint with a single minimal request.

    Args:
        request: Role to probe plus optional temporary overrides.

    Returns:
        The probe outcome; a failed probe is a 200 with ``status="fail"`` and an
        actionable hint, not an HTTP error — the endpoint answered, the check
        just did not pass.

    Raises:
        HTTPException: 404 for an unknown role, 422 when no ``base_url`` is
            available to probe.
    """
    if request.role not in ROLES:
        raise HTTPException(status_code=404, detail=f"Unknown role: {request.role}")
    role: Role = request.role
    settings = _settings.llm if role == "llm" else _settings.vl
    base_url = request.base_url if request.base_url is not None else settings.base_url
    if not base_url.strip():
        raise HTTPException(status_code=422, detail=f"No base_url to probe for role '{role}'")
    return probe_endpoint(
        settings,
        role=role,
        api_key_override=request.api_key,
        base_url_override=request.base_url,
        model_override=request.model,
    )


@app.get("/api/diagnostics", response_model=DiagnosticReport)
def get_diagnostics(deep: bool = False) -> DiagnosticReport:
    """Run the self-check and return its report.

    Args:
        deep: When ``True``, also probe the model endpoints (network calls) and
            append the report to ``logs/diagnostics.jsonl``. Left ``False`` by
            default so a GUI poll stays local and silent.

    Returns:
        The diagnostic report.
    """
    return _doctor.run(deep=deep)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=_settings.host, port=_settings.port)
