# Spec: test-suite

## 功能描述

`tests/` pytest 套件覆盖核心工具与编排层纯逻辑，为接入真实 VL 推理提供回归基线。

## 输入约束

- 运行方式 `uv run pytest`（`pyproject.toml`：`testpaths=["tests"]`、`pythonpath=["."]`）。
- 测试通过 `tmp_path` 构造隔离 tasks root，不读写仓库内 `tasks/`。
- API 测试使用 `fastapi.testclient.TestClient`（dev 依赖含 `httpx`）。

## 输出约束

- 覆盖：`obb_utils`（四点↔旋转框往返、IoU、退化、归一化/反归一化、越界）、`file_utils`、`yaml_utils`、`task_manager`、`pipeline`（plan→annotate→inspect→split 全流程，含 `data.yaml`/`train_command.txt` 产出）、`api_server`（任务 CRUD、labels 端点、step 执行）。
- 断言仅依赖公开 API。

## 边界条件

- 无图片任务执行 split → 空 summary 且不崩溃。
- 零面积 OBB 的 IoU → 0.0。
- 测试相互独立，单文件可单独运行。

## 验收标准

1. `uv run pytest` 全绿（当前 37 passed）。
2. `uv run ruff check src/ run.py tests/` 与 `uv run mypy src/` 零错误。
