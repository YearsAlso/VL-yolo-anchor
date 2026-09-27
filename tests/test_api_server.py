"""Tests for src.api_server (labels endpoints, task CRUD) via TestClient."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
import src.api_server as api_server
from fastapi.testclient import TestClient
from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager


def make_real_image(path: Path, w: int = 64, h: int = 48) -> Path:
    """Write a real decodable PNG so annotate/inspect can load it.

    Args:
        path: Target image path (parents are created).
        w: Image width in pixels.
        h: Image height in pixels.

    Returns:
        The written path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return path


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
    assert client.get("/api/tasks/ghost/plan").status_code == 404


def test_get_plan_missing_task_returns_404(client: TestClient) -> None:
    """GET /plan for an unknown task is 404 (not a 500 from FileNotFoundError)."""
    assert client.get("/api/tasks/ghost/plan").status_code == 404


def test_cors_allows_gui_origin(client: TestClient) -> None:
    """B1: responses echo the allowed GUI/Tauri origins so the browser accepts them."""
    res = client.get("/api/tasks", headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
    pre = client.options(
        "/api/tasks",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert pre.status_code == 200


def test_task_name_rejects_traversal(client: TestClient) -> None:
    """C2: create-task validates the name before touching the filesystem."""
    for bad in ("../evil", "..\\evil", "a/b", "", ".hidden"):
        assert client.post("/api/tasks", json={"name": bad}).status_code == 422
    assert "evil" not in api_server._task_manager.list_tasks()


def test_get_image_rejects_unsafe_name(client: TestClient) -> None:
    """C2: the file-name guard blocks any path that is not a bare name."""
    _make_task_with_image_and_label(client)
    images_dir = api_server._task_manager.task_dir("demo") / "images"
    for bad in ("..", "a/b", ".", "sub/dir.png"):
        with pytest.raises(api_server.HTTPException):
            api_server._safe_child(images_dir, bad)
    # A clean name passes through unchanged.
    ok = api_server._safe_child(images_dir, "img_01.png")
    assert ok.name == "img_01.png"


def test_get_report_does_not_create_missing_task_dir(client: TestClient) -> None:
    """M9: a GET for an unknown task must not leave a stray directory behind."""
    root = api_server._task_manager.tasks_root
    assert client.get("/api/tasks/ghost/report").status_code == 404
    assert not (root / "ghost").exists()


def test_label_response_reports_source(client: TestClient) -> None:
    """M8: the payload tells the canvas which directory the boxes came from."""
    _make_task_with_image_and_label(client)
    assert client.get("/api/tasks/demo/labels/img_01.png").json()["source"] == "ai_labels"
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_text(
        "0 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n", encoding="utf-8"
    )
    assert client.get("/api/tasks/demo/labels/img_01.png").json()["source"] == "candidate_labels"


def test_label_file_with_bom_keeps_first_box(client: TestClient) -> None:
    """M5: a UTF-8 BOM must not silently drop the first label line."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_text(
        "\ufeff0 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n", encoding="utf-8"
    )
    boxes = client.get("/api/tasks/demo/labels/img_01.png").json()["boxes"]
    assert len(boxes) == 1


def test_non_utf8_label_returns_200_not_500(client: TestClient) -> None:
    """H4: invalid bytes are replaced, the good line survives, no 500."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_bytes(
        b"0 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n\xff\xfe garbage\n"
    )
    res = client.get("/api/tasks/demo/labels/img_01.png")
    assert res.status_code == 200
    assert len(res.json()["boxes"]) == 1


def test_out_of_range_coords_skipped(client: TestClient) -> None:
    """M6: coordinates outside [0, 1] are dropped (CLAUDE.md hard constraint 1)."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_text(
        "0 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n0 1.5 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n", encoding="utf-8"
    )
    boxes = client.get("/api/tasks/demo/labels/img_01.png").json()["boxes"]
    assert len(boxes) == 1


def test_invalid_cls_filtered_by_task_plan(client: TestClient) -> None:
    """M6: classes absent from the task plan are filtered (hard constraint 3)."""
    _make_task_with_image_and_label(client)
    client.post("/api/tasks/demo/plan")  # stub plan -> classes {0: 'defect'}
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "candidate_labels" / "img_01.txt").write_text(
        "0 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n99 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n", encoding="utf-8"
    )
    boxes = client.get("/api/tasks/demo/labels/img_01.png").json()["boxes"]
    assert [b["cls"] for b in boxes] == [0]


def test_label_endpoints_never_write(client: TestClient) -> None:
    """L13: the read-only label endpoints must not modify any label file."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    watched = list((task_dir / "ai_labels").glob("*.txt")) + list((task_dir / "candidate_labels").glob("*.txt"))
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in watched}
    client.get("/api/tasks/demo/labels")
    client.get("/api/tasks/demo/labels/img_01.png")
    after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in watched}
    assert after == before


def test_list_labeled_images_excludes_unlabeled(client: TestClient) -> None:
    """L13: only images with a label file are reported as labeled."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "images" / "unlabeled.png").write_bytes(b"\x00")
    res = client.get("/api/tasks/demo/labels")
    assert res.json() == {"images": ["img_01.png"]}


def _setup_full_task(client: TestClient, image_name: str = "img_01.png") -> Path:
    """Create a decodable-image task and run plan + annotate through the API.

    Args:
        client: TestClient fixture.
        image_name: Image file name written into ``images/``.

    Returns:
        The task directory path.
    """
    client.post("/api/tasks", json={"name": "demo", "description": "crack defect"})
    client.post("/api/tasks/demo/plan")
    task_dir = api_server._task_manager.task_dir("demo")
    make_real_image(task_dir / "images" / image_name)
    client.post("/api/tasks/demo/annotate")
    return task_dir


def test_step_endpoint_plan(client: TestClient) -> None:
    """POST /step is the generic entrypoint and returns the plan payload."""
    client.post("/api/tasks", json={"name": "demo"})
    res = client.post("/api/tasks/demo/step", json={"step": "plan"})
    assert res.status_code == 200
    body = res.json()
    assert body["task"] == "demo" and body["step"] == "plan"
    assert "classes" in body["result"]


def test_step_endpoint_validations(client: TestClient) -> None:
    """POST /step: unknown task is 404, step outside the Literal is 422."""
    assert client.post("/api/tasks/ghost/step", json={"step": "plan"}).status_code == 404
    client.post("/api/tasks", json={"name": "demo"})
    assert client.post("/api/tasks/demo/step", json={"step": "bogus"}).status_code == 422


def test_step_endpoint_internal_error(client: TestClient, monkeypatch) -> None:
    """POST /step maps an agent exception to HTTP 500 with its message."""
    client.post("/api/tasks", json={"name": "demo"})

    def _boom(task_name: str, step: str) -> dict:
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(api_server._pipeline, "run_step", _boom)
    res = client.post("/api/tasks/demo/step", json={"step": "plan"})
    assert res.status_code == 500
    assert "agent exploded" in res.json()["detail"]


def test_list_images_endpoint(client: TestClient) -> None:
    """GET /images lists names and API URLs for every file in images/."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "images" / "unlabeled.png").write_bytes(b"\x00")
    res = client.get("/api/tasks/demo/images")
    assert res.status_code == 200
    assert res.json() == [
        {"name": "img_01.png", "url": "/api/tasks/demo/images/img_01.png"},
        {"name": "unlabeled.png", "url": "/api/tasks/demo/images/unlabeled.png"},
    ]
    assert client.get("/api/tasks/ghost/images").status_code == 404


def test_get_image_serves_file(client: TestClient, make_image) -> None:
    """GET /images/{img} returns the exact bytes of the stored file."""
    client.post("/api/tasks", json={"name": "demo"})
    task_dir = api_server._task_manager.task_dir("demo")
    src = make_image("png_src.png", 40, 30)
    target = task_dir / "images" / "real.png"
    target.write_bytes(src.read_bytes())
    res = client.get("/api/tasks/demo/images/real.png")
    assert res.status_code == 200
    assert res.content == src.read_bytes()
    assert client.get("/api/tasks/demo/images/missing.png").status_code == 404
    assert client.get("/api/tasks/ghost/images/real.png").status_code == 404


def test_export_summary_endpoint(client: TestClient) -> None:
    """GET /export 404s before split and returns the summary afterwards."""
    _setup_full_task(client)
    assert client.get("/api/tasks/demo/export").status_code == 404
    client.post("/api/tasks/demo/step", json={"step": "split"})
    res = client.get("/api/tasks/demo/export")
    assert res.status_code == 200
    body = res.json()
    assert body["task"] == "demo"
    assert sum(body["splits"].values()) == 1
    assert client.get("/api/tasks/ghost/export").status_code == 404


def test_report_endpoint(client: TestClient) -> None:
    """GET /report 404s without an inspection and returns the report after."""
    _setup_full_task(client)
    assert client.get("/api/tasks/demo/report").status_code == 404
    res = client.post("/api/tasks/demo/inspect")
    assert res.status_code == 200
    assert "errors" in res.json()["result"]
    report = client.get("/api/tasks/demo/report")
    assert report.status_code == 200
    assert report.json()["report"]["num_images"] == 1


def test_annotate_endpoint_lists_items(client: TestClient) -> None:
    """POST /annotate wraps the non-dict label list into {"items": ...}."""
    client.post("/api/tasks", json={"name": "demo", "description": "crack"})
    client.post("/api/tasks/demo/plan")
    make_real_image(api_server._task_manager.task_dir("demo") / "images" / "img_01.png")
    res = client.post("/api/tasks/demo/annotate")
    assert res.status_code == 200
    items = res.json()["result"]["items"]
    assert len(items) == 1 and items[0].endswith("img_01.txt")


def test_corrupt_plan_skips_class_filter(client: TestClient) -> None:
    """A plan.yaml that cannot be parsed disables filtering instead of 500ing."""
    _make_task_with_image_and_label(client)
    task_dir = api_server._task_manager.task_dir("demo")
    (task_dir / "plan.yaml").write_text("{{{ [unclosed", encoding="utf-8")
    (task_dir / "candidate_labels" / "img_01.txt").write_text(
        "99 0.1 0.2 0.5 0.2 0.5 0.6 0.1 0.6\n", encoding="utf-8"
    )
    res = client.get("/api/tasks/demo/labels/img_01.png")
    assert res.status_code == 200
    assert [b["cls"] for b in res.json()["boxes"]] == [99]


def test_missing_task_yaml_returns_404(client: TestClient) -> None:
    """A stray task dir without task.yaml is reported as 404, not a 500."""
    root = api_server._task_manager.tasks_root
    (root / "stray").mkdir()
    assert client.get("/api/tasks/stray/plan").status_code == 404


def test_url_encoded_traversal_rejected(client: TestClient) -> None:
    """C2: %5c-encoded traversal is rejected with 400 at the HTTP layer."""
    _make_task_with_image_and_label(client)
    secret = api_server._task_manager.tasks_root.parent / "secret.png"
    secret.write_bytes(b"topsecret")
    for path in (
        "/api/tasks/demo/labels/..%5c..%5csecret.png",
        "/api/tasks/demo/images/..%5c..%5csecret.png",
    ):
        res = client.get(path)
        assert res.status_code == 400, path


def test_unreadable_label_file_returns_422(client: TestClient, monkeypatch) -> None:
    """H4: an OSError while reading a label surfaces as 422, never 500."""
    _make_task_with_image_and_label(client)

    def _boom(self, *args, **kwargs):
        raise OSError("sharing violation")

    monkeypatch.setattr(Path, "read_text", _boom)
    res = client.get("/api/tasks/demo/labels/img_01.png")
    assert res.status_code == 422
    assert "Unreadable label file" in res.json()["detail"]
