"""FastAPI server exposing the pipeline over HTTP for the GUI.

Run with: ``uv run uvicorn src.api_server:app --port 8765``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager
from src.utils.file_utils import list_images
from src.utils.yaml_utils import load_yaml

app = FastAPI(title="VL-YOLO-Anchor API", version="0.1.0")
logger = logging.getLogger("api_server")

_TASKS_ROOT = Path("tasks")
_task_manager = TaskManager(_TASKS_ROOT)
_pipeline = Pipeline(_task_manager)


class TaskCreateRequest(BaseModel):
    """Request body for creating a task."""

    name: str = Field(..., min_length=1, description="Unique task name")
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
    if name not in _task_manager.list_tasks():
        raise HTTPException(status_code=404, detail=f"Task not found: {name}")
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
    report_path = _task_manager.task_dir(name) / "inspection_report.yaml"
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
    if name not in _task_manager.list_tasks():
        raise HTTPException(status_code=404, detail=f"Task not found: {name}")
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
    summary_path = _task_manager.task_dir(name) / "dataset" / "export_summary.yaml"
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail=f"No dataset export for task: {name}")
    return load_yaml(summary_path)


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
    if name not in _task_manager.list_tasks():
        raise HTTPException(status_code=404, detail=f"Task not found: {name}")
    images = list_images(_task_manager.task_dir(name) / "images")
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
        HTTPException: 404 if the image does not exist.
    """
    image_path = _task_manager.task_dir(name) / "images" / image_name
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image not found: {image_name}")
    return FileResponse(image_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
