"""Tests for src.api_server (labels endpoints, task CRUD) via TestClient."""

from __future__ import annotations

import pytest
import src.api_server as api_server
from fastapi.testclient import TestClient
from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient bound to an isolated tasks root under tmp_path.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Configured ``fastapi.testclient.TestClient``.
    """
    tm = TaskManager(tmp_path / "tasks")
    monkeypatch.setattr(api_server, "_task_manager", tm)
    monkeypatch.setattr(api_server, "_pipeline", Pipeline(tm))
    return TestClient(api_server.app)


def _make_task_with_image_and_label(client: TestClient, image_name: str = "img_01.png") -> None:
    """Create a task, drop an image placeholder, and write an AI label.

    Args:
        client: TestClient fixture (task created through the API).
        image_name: Image file name to create.
    """
    client.post("/api/tasks", json={"name": "demo", "description": "test"})
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "images" / image_name).write_bytes(b"\x00\x01")
    (task_dir / "ai_labels" / f"{image_name.rsplit('.', 1)[0]}.txt").write_text(
        "0 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n", encoding="utf-8"
    )


def test_create_and_list_tasks(client: TestClient) -> None:
    """POST /api/tasks creates; GET /api/tasks lists sorted names."""
    res = client.post("/api/tasks", json={"name": "demo", "description": "d"})
    assert res.status_code == 200
    assert res.json()["name"] == "demo"
    duplicate = client.post("/api/tasks", json={"name": "demo"})
    assert duplicate.status_code == 409
    assert client.get("/api/tasks").json() == ["demo"]


def test_list_labeled_images(client: TestClient) -> None:
    """GET /labels lists image names that have a label file."""
    _make_task_with_image_and_label(client)
    res = client.get("/api/tasks/demo/labels")
    assert res.status_code == 200
    assert res.json() == {"images": ["img_01.png"]}


def test_get_label_boxes(client: TestClient) -> None:
    """GET /labels/{img} returns parsed normalized OBB boxes."""
    _make_task_with_image_and_label(client)
    res = client.get("/api/tasks/demo/labels/img_01.png")
    assert res.status_code == 200
    body = res.json()
    assert body["image"] == "img_01.png"
    assert body["boxes"] == [{"cls": 0, "points": [0.1, 0.2, 0.5, 0.2, 0.5, 0.6, 0.1, 0.6], "conf": 1.0}]


def test_candidate_labels_take_priority(client: TestClient) -> None:
    """A candidate label overrides the raw AI label for the same image."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_text("1 0.0 0.0 1.0 0.0 1.0 1.0 0.0 1.0\n", encoding="utf-8")
    res = client.get("/api/tasks/demo/labels/img_01.png")
    assert res.json()["boxes"][0]["cls"] == 1


def test_malformed_lines_are_skipped(client: TestClient) -> None:
    """Malformed and wrong-length lines are skipped, valid lines kept."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_text(
        "0 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\nnot a line\n0 0.1 0.2\n\n", encoding="utf-8"
    )
    res = client.get("/api/tasks/demo/labels/img_01.png")
    boxes = res.json()["boxes"]
    assert len(boxes) == 1


def test_label_endpoints_404s(client: TestClient) -> None:
    """404 semantics: missing task, missing image, and missing label file."""
    _make_task_with_image_and_label(client)
    assert client.get("/api/tasks/ghost/labels").status_code == 404
    assert client.get("/api/tasks/ghost/labels/img_01.png").status_code == 404
    assert client.get("/api/tasks/demo/labels/missing.png").status_code == 404
    (api_server._task_manager.task_dir("demo") / "images" / "unlabeled.png").write_bytes(b"\x00")
    assert client.get("/api/tasks/demo/labels/unlabeled.png").status_code == 404


def test_empty_label_file_returns_empty_boxes(client: TestClient) -> None:
    """An empty label file yields 200 with an empty box list."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_text("", encoding="utf-8")
    res = client.get("/api/tasks/demo/labels/img_01.png")
    assert res.status_code == 200
    assert res.json()["boxes"] == []


def test_run_plan_step_via_api(client: TestClient) -> None:
    """POST /plan returns the normalized training plan."""
    client.post("/api/tasks", json={"name": "demo", "description": "d"})
    res = client.post("/api/tasks/demo/plan")
    assert res.status_code == 200
    body = res.json()
    assert body["task"] == "demo" and body["step"] == "plan"
    assert "classes" in body["result"]
    assert client.post("/api/tasks/ghost/plan").status_code == 404


def test_get_plan_endpoint(client: TestClient) -> None:
    """GET /plan exposes the training_plan section of the task config."""
    client.post("/api/tasks", json={"name": "demo", "description": "d"})
    client.post("/api/tasks/demo/plan")
    plan = client.get("/api/tasks/demo/plan").json()
    assert "classes" in plan and "hyperparameters" in plan
