"""Tests for src.api_server (labels, task CRUD, config, diagnostics) via TestClient."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import src.api_server as api_server
from fastapi.testclient import TestClient
from src.agents.model_client import ProbeResult
from src.config import Settings
from src.core.config_store import ConfigStore
from src.core.diagnostics import DiagnosticReport, Doctor
from src.core.metadata_store import MODEL_CALLS_FILENAME, RUN_HISTORY_FILENAME, MetadataStore
from src.core.pipeline import Pipeline
from src.core.task_manager import TaskManager
from src.utils.secrets import SecretStore, generate_master_key

REPO_ROOT = Path(__file__).resolve().parents[1]
"""Repository root, used to reach the checked-in ``config/global.yaml``."""


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


# --------------------------------------------------------------------------- #
# Configuration / diagnostics / history endpoints (M3).
#
# These tests rebind the module-level singletons to a tmp_path config directory:
# the shipped defaults point at the repository's own ``config/``, and a test must
# never write ``overrides.yaml`` or ``secrets.db`` next to the checked-in files.
# --------------------------------------------------------------------------- #


def _isolated_settings(tmp_path: Path, **updates: Any) -> Settings:
    """Build settings rooted in a temporary config directory.

    Args:
        tmp_path: Pytest temporary directory.
        updates: Extra ``Settings`` fields to override.

    Returns:
        A settings copy whose config dir, index DB and tasks root live under
        ``tmp_path``.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, Any] = {
        "config_dir": config_dir,
        "db_path": config_dir / "index.db",
        "tasks_root": tmp_path / "tasks",
    }
    defaults.update(updates)
    return api_server._settings.model_copy(update=defaults)


@pytest.fixture
def config_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with an isolated config dir, encrypted store, and index.

    A master key is installed so the secret store is usable by default; tests
    that need the degraded path delete it. The endpoint/field environment
    variables are cleared because the settings page's whole job is deciding what
    the environment locks, and a stray variable in the developer's shell would
    silently change which branch is under test.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Configured ``fastapi.testclient.TestClient``.
    """
    monkeypatch.setenv("VL_ANCHOR_SECRET_KEY", generate_master_key())
    monkeypatch.delenv("VL_ANCHOR_SECRET_KEY_FILE", raising=False)
    for prefix in ("VL_VL_", "VL_LLM_", "VL_MODEL_"):
        for suffix in ("PROVIDER", "BASE_URL", "NAME", "API_KEY"):
            monkeypatch.delenv(f"{prefix}{suffix}", raising=False)

    settings = _isolated_settings(tmp_path)
    task_manager = TaskManager(settings.tasks_root)
    monkeypatch.setattr(api_server, "_task_manager", task_manager)
    monkeypatch.setattr(api_server, "_pipeline", Pipeline(task_manager))
    monkeypatch.setattr(api_server, "_config_store", ConfigStore(settings, settings.config_dir))
    monkeypatch.setattr(api_server, "_doctor", Doctor(settings, task_manager, settings.config_dir))
    monkeypatch.setattr(api_server, "_metadata_store", MetadataStore(settings.db_path))
    return TestClient(api_server.app)


def _config_dir(tmp_path: Path) -> Path:
    """Return the config directory the ``config_client`` fixture uses.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        ``tmp_path/config``.
    """
    return tmp_path / "config"


def _append_jsonl(task_dir: Path, filename: str, record: dict[str, Any]) -> None:
    """Append one JSON line to an audit log.

    Args:
        task_dir: Task directory.
        filename: Log file name (``run_history.jsonl`` / ``model_calls.jsonl``).
        record: Record to serialize.
    """
    with (task_dir / filename).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_get_config_contract_and_field_sources(
    config_client: TestClient, tmp_path: Path
) -> None:
    """GET /api/config describes every field, its layer, and the secret store."""
    res = config_client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"llm", "vl", "storage", "server", "inference", "secrets"}
    assert body["vl"]["provider"]["value"] == "stub"
    assert body["vl"]["provider"]["source"] == "default"
    assert body["vl"]["provider"]["locked"] is False
    assert body["secrets"]["available"] is True
    assert body["secrets"]["store_path"] == str(_config_dir(tmp_path) / "secrets.db")
    assert body["storage"]["config_dir"] == str(_config_dir(tmp_path))
    assert body["storage"]["config_writable"] is True
    assert body["server"]["auth_enabled"] is False


def test_get_config_never_returns_a_plaintext_key(
    config_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An api_key is reported as locked/masked metadata, never as a value."""
    monkeypatch.setenv("VL_VL_API_KEY", "env-key-abcd1234")
    res = config_client.get("/api/config")
    assert res.status_code == 200
    assert "env-key-abcd1234" not in res.text
    vl = res.json()["vl"]
    assert vl["api_key"]["value"] is None
    assert vl["api_key"]["source"] == "env"
    assert vl["api_key"]["locked"] is True
    assert vl["api_key"]["locked_by"] == "VL_VL_API_KEY"
    assert vl["has_api_key"] is True
    assert vl["api_key_masked"] == "****1234"


def test_put_config_writes_overrides_and_takes_effect_after_restart(
    config_client: TestClient, tmp_path: Path
) -> None:
    """PUT writes non-secret fields to overrides.yaml; a fresh read sees them.

    The settings snapshot is process-cached, so the running process keeps serving
    the old values until a restart — which is exactly what ``restart_required``
    tells the GUI to say. A newly built store therefore stands in for the restart.
    """
    res = config_client.put(
        "/api/config",
        json={"vl": {"provider": "remote", "base_url": "http://127.0.0.1:8000/v1", "model": "qwen2.5vl"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["written"]["vl"] == {
        "provider": "remote",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "qwen2.5vl",
    }
    assert body["ignored"] == []
    assert body["restart_required"] is True
    assert body["overrides_path"] == str(_config_dir(tmp_path) / "overrides.yaml")

    settings = _isolated_settings(tmp_path)
    restarted = ConfigStore(settings, settings.config_dir)
    vl = restarted.effective().vl
    assert vl.provider.value == "remote"
    assert vl.provider.source == "overrides"
    assert vl.base_url.value == "http://127.0.0.1:8000/v1"


def test_put_config_keeps_global_yaml_untouched(config_client: TestClient, tmp_path: Path) -> None:
    """Acceptance 5: write-back must not re-serialize global.yaml.

    A YAML round-trip would strip every comment and commented-out example from a
    file whose whole value is that documentation.
    """
    original = (REPO_ROOT / "config" / "global.yaml").read_bytes()
    (_config_dir(tmp_path) / "global.yaml").write_bytes(original)
    assert config_client.put("/api/config", json={"llm": {"model": "gpt-4o-mini"}}).status_code == 200
    assert (_config_dir(tmp_path) / "global.yaml").read_bytes() == original


def test_put_config_stores_api_key_encrypted(config_client: TestClient, tmp_path: Path) -> None:
    """Acceptance 11: a submitted key lands in the encrypted store, in no file as text."""
    secret = "vl-secret-9f2a"
    res = config_client.put(
        "/api/config",
        json={
            "vl": {
                "provider": "remote",
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "qwen2.5vl",
                "api_key": secret,
            }
        },
    )
    assert res.status_code == 200
    assert res.json()["written"]["vl"]["api_key"] == "***"
    assert secret not in res.text

    overrides = (_config_dir(tmp_path) / "overrides.yaml").read_text(encoding="utf-8")
    assert secret not in overrides
    assert "api_key" not in overrides

    store_path = _config_dir(tmp_path) / "secrets.db"
    assert store_path.is_file()
    assert secret.encode() not in store_path.read_bytes()
    assert SecretStore(store_path).get("VL_VL_API_KEY") == secret

    vled = config_client.get("/api/config")
    assert vled.status_code == 200
    assert secret not in vled.text
    assert vled.json()["vl"]["has_api_key"] is True
    assert vled.json()["vl"]["api_key_masked"] == "****9f2a"
    assert vled.json()["vl"]["api_key"]["source"] == "secrets"


def test_put_config_rejects_invalid_patch(config_client: TestClient, tmp_path: Path) -> None:
    """422 carries the validation errors and writes nothing."""
    res = config_client.put("/api/config", json={"vl": {"provider": "remote"}})
    assert res.status_code == 422
    errors = res.json()["detail"]["errors"]
    assert any("base_url" in message for message in errors)
    assert any("model" in message for message in errors)

    empty = config_client.put("/api/config", json={})
    assert empty.status_code == 422
    assert empty.json()["detail"]["errors"] == ["未提交任何字段。"]

    assert not (_config_dir(tmp_path) / "overrides.yaml").exists()


def test_put_config_all_fields_locked_returns_409(
    config_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """409 means "nothing was saved": no field may be reported as written."""
    monkeypatch.setenv("VL_VL_PROVIDER", "remote")
    monkeypatch.setenv("VL_VL_BASE_URL", "http://locked:8000/v1")
    monkeypatch.setenv("VL_VL_NAME", "locked-model")
    res = config_client.put(
        "/api/config",
        json={"vl": {"provider": "remote", "base_url": "http://other:9000/v1", "model": "other"}},
    )
    assert res.status_code == 409
    body = res.json()
    assert body["written"] == {}
    assert {item["reason"] for item in body["ignored"]} == {"env_locked"}
    assert {item["field"] for item in body["ignored"]} == {"provider", "base_url", "model"}
    assert not (_config_dir(tmp_path) / "overrides.yaml").exists()


def test_put_config_reports_partially_locked_fields(
    config_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A patch mixing locked and free fields is a 200 that names the skipped ones."""
    monkeypatch.setenv("VL_VL_PROVIDER", "stub")
    res = config_client.put("/api/config", json={"vl": {"provider": "stub", "model": "qwen2.5vl"}})
    assert res.status_code == 200
    body = res.json()
    assert body["written"]["vl"] == {"model": "qwen2.5vl"}
    assert body["ignored"] == [{"role": "vl", "field": "provider", "reason": "env_locked"}]


def test_put_config_without_master_key_ignores_api_key(
    config_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance 13: no master key → the key is ignored and the store is not created.

    The status is 409 rather than 200 because the key was the only field in the
    patch and nothing at all was saved — the same rule as any other all-ignored
    request.
    """
    monkeypatch.delenv("VL_ANCHOR_SECRET_KEY", raising=False)
    res = config_client.put("/api/config", json={"vl": {"api_key": "unreachable-secret"}})
    assert res.status_code == 409
    body = res.json()
    assert body["written"] == {}
    assert body["ignored"] == [{"role": "vl", "field": "api_key", "reason": "no_secret_store"}]
    assert "unreachable-secret" not in res.text
    assert not (_config_dir(tmp_path) / "secrets.db").exists()


def test_config_put_preflight_is_allowed(config_client: TestClient) -> None:
    """The browser sends a preflight for PUT; without it the settings page cannot save."""
    res = config_client.options(
        "/api/config",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT"},
    )
    assert res.status_code == 200
    assert "PUT" in res.headers.get("access-control-allow-methods", "")


def test_config_put_preflight_is_allowed_with_auth_enabled(
    config_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preflight carries no Authorization header, so it must bypass the auth check.

    Found by running the real stack with VL_ANCHOR_AUTH_TOKEN set: the browser's
    OPTIONS got a 401, which the browser surfaces as a CORS error — so enabling
    auth (acceptance 8) made the settings page unable to save at all.
    """
    secured = api_server._settings.model_copy(
        update={"auth_token": "tok-123", "auth_token_source": "VL_ANCHOR_AUTH_TOKEN"}
    )
    monkeypatch.setattr(api_server, "_settings", secured)

    res = config_client.options(
        "/api/config",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT"},
    )
    assert res.status_code == 200
    assert "PUT" in res.headers.get("access-control-allow-methods", "")

    # The exemption is narrow: the actual PUT still needs the token, and an
    # OPTIONS that is not a preflight does not get a free pass either.
    assert config_client.put("/api/config", json={"vl": {"model": "m"}}).status_code == 401
    assert config_client.options("/api/config").status_code == 401


def test_config_test_role_validation(config_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """404 for an unknown role; 422 when there is no base_url to probe."""
    assert config_client.post("/api/config/test", json={"role": "vision"}).status_code == 404

    # Pin the endpoint empty: the assertion is about the route's own guard, not
    # about whatever the ambient environment happens to configure.
    model = api_server._settings.vl.model_copy(update={"base_url": ""})
    monkeypatch.setattr(api_server, "_settings", api_server._settings.model_copy(update={"vl": model}))
    res = config_client.post("/api/config/test", json={"role": "vl"})
    assert res.status_code == 422
    assert "base_url" in res.json()["detail"]


def test_config_test_forwards_overrides_to_probe(
    config_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe receives exactly what the settings page typed, unsaved."""
    captured: dict[str, Any] = {}

    def _fake_probe(settings: Any, **kwargs: Any) -> ProbeResult:
        captured.update(kwargs)
        return ProbeResult(role=kwargs["role"], status="ok", latency_ms=12, http_status=200, message="ok")

    monkeypatch.setattr(api_server, "probe_endpoint", _fake_probe)
    res = config_client.post(
        "/api/config/test",
        json={"role": "llm", "base_url": "http://127.0.0.1:9/v1", "api_key": "typed-but-unsaved"},
    )
    assert res.status_code == 200
    assert res.json() == {
        "role": "llm",
        "status": "ok",
        "latency_ms": 12,
        "http_status": 200,
        "message": "ok",
        "hint": "",
    }
    assert captured["role"] == "llm"
    assert captured["base_url_override"] == "http://127.0.0.1:9/v1"
    assert captured["api_key_override"] == "typed-but-unsaved"


def test_diagnostics_shallow_run_is_local(config_client: TestClient) -> None:
    """Default diagnostics: 200 with the stub warning, no probes, no log written."""
    res = config_client.get("/api/diagnostics")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "warn"
    assert body["probes"] == []
    by_id = {check["id"]: check for check in body["checks"]}
    assert by_id["config.vl.provider"]["status"] == "warn"
    assert by_id["model.vl.connectivity"]["status"] == "skip"
    assert by_id["storage.prompts"]["status"] == "ok"
    assert by_id["config.paths.secret_store"]["status"] == "ok"
    assert not (api_server._settings.logs_dir / "diagnostics.jsonl").exists()


def test_diagnostics_deep_flag_is_forwarded(
    config_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`?deep=true` is what enables probing; the default must never reach the network."""
    seen: list[bool] = []

    class _FakeDoctor:
        """Doctor stand-in: records the flag instead of probing."""

        def run(self, *, deep: bool = False) -> DiagnosticReport:
            seen.append(deep)
            return DiagnosticReport(status="ok", generated_at="2026-01-01T00:00:00Z", checks=[])

    monkeypatch.setattr(api_server, "_doctor", _FakeDoctor())
    assert config_client.get("/api/diagnostics").json()["status"] == "ok"
    assert config_client.get("/api/diagnostics?deep=true").json()["status"] == "ok"
    assert seen == [False, True]


def test_history_endpoint_reads_disk(config_client: TestClient) -> None:
    """History comes from the JSONL logs, newest first, with a limit."""
    assert config_client.get("/api/tasks/ghost/history").status_code == 404
    config_client.post("/api/tasks", json={"name": "demo", "description": "d"})
    task_dir = api_server._task_manager.task_dir("demo")
    assert config_client.get("/api/tasks/demo/history").json() == {
        "task": "demo",
        "runs": [],
        "calls": [],
        "source": "jsonl",
    }

    for step, stamp in (("plan", "2026-01-01T00:00:01Z"), ("annotate", "2026-01-01T00:00:02Z")):
        _append_jsonl(
            task_dir,
            RUN_HISTORY_FILENAME,
            {
                "step": step,
                "status": "ok",
                "started_at": stamp,
                "finished_at": stamp,
                "duration_ms": 5,
                "items": None,
                "message": "",
            },
        )
    _append_jsonl(
        task_dir,
        MODEL_CALLS_FILENAME,
        {
            "role": "llm",
            "provider": "stub",
            "model": "",
            "host": "",
            "status": "stub",
            "http_status": None,
            "duration_ms": 2,
            "started_at": "2026-01-01T00:00:01Z",
        },
    )

    body = config_client.get("/api/tasks/demo/history").json()
    assert [run["step"] for run in body["runs"]] == ["annotate", "plan"]
    assert body["calls"][0]["status"] == "stub"
    assert len(config_client.get("/api/tasks/demo/history?limit=1").json()["runs"]) == 1


def test_index_stats_endpoint(
    config_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 reports an empty-but-usable index; 503 reports one that cannot be read."""
    res = config_client.get("/api/index/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True and body["tasks"] == 0
    # A read path must not create the database: zeros are reported, not materialized.
    assert not (_config_dir(tmp_path) / "index.db").exists()

    unreachable = MetadataStore(_config_dir(tmp_path) / "gone" / "index.db")
    monkeypatch.setattr(api_server, "_metadata_store", unreachable)
    assert config_client.get("/api/index/stats").status_code == 503

    meta = api_server._metadata_store
    monkeypatch.setattr(api_server, "_metadata_store", MetadataStore(meta.db_path, enabled=False))
    assert config_client.get("/api/index/stats").status_code == 503


def test_new_endpoints_require_the_auth_token(
    config_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance 8: with a token configured, every new endpoint is 401 without it."""
    secured = api_server._settings.model_copy(
        update={"auth_token": "tok-123", "auth_token_source": "VL_ANCHOR_AUTH_TOKEN"}
    )
    monkeypatch.setattr(api_server, "_settings", secured)

    assert config_client.get("/api/health").status_code == 200
    for path in ("/api/config", "/api/diagnostics", "/api/index/stats", "/api/tasks/ghost/history"):
        assert config_client.get(path).status_code == 401, path
        assert config_client.get(path, headers={"Authorization": "Bearer tok-123"}).status_code in {200, 404}
    assert config_client.put("/api/config", json={"vl": {"model": "m"}}).status_code == 401
    assert (
        config_client.put(
            "/api/config", json={"vl": {"model": "m"}}, headers={"Authorization": "Bearer tok-123"}
        ).status_code
        == 200
    )
