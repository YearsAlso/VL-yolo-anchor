# Spec: test-suite

> **状态**：已实施（as-built，从 `specs/test-suite.spec.md` 迁移并重组为六要素；**基线由 94 例更新为 99 例**）
> **设计**：[`design.md`](design.md)
> **实施栈**：后端
> **最后核对**：2026-09-27（实测 `uv run pytest -q` → **99 passed, 2 warnings in 4.40s**）

## 一、业务背景

`tests/` 的 pytest 套件覆盖核心工具与编排层的纯逻辑，为**接入真实 VL 推理提供回归基线** —— 在 stub 模型下把全链路行为钉死，换真模型时才能区分"模型变了"与"代码坏了"。

## 二、功能范围

**包含**（12 个测试文件）

| 文件 | 行数 | 覆盖 |
|------|------|------|
| `tests/conftest.py` | 29 | 共享 fixture（TestClient、隔离 tasks root） |
| `tests/test_obb_utils.py` | 83 | 四点↔旋转框往返、IoU、退化、归一化/反归一化、越界 |
| `tests/test_image_utils.py` | 69 | 解码失败、尺寸、非 ASCII 路径 |
| `tests/test_file_yaml_utils.py` | 58 | 图片列举、YAML 读写 |
| `tests/test_task_manager.py` | 64 | 任务创建/列举/加载/播种 |
| `tests/test_pipeline.py` | 118 | `plan→annotate→inspect→split` 全流程，含 `data.yaml` 与 `train_command.txt` 产出 |
| `tests/test_plan_agent.py` | 67 | 计划解析与畸形输出兜底 |
| `tests/test_annotate_agent.py` | 76 | 检测过滤（conf 阈值、最小像素） |
| `tests/test_inspect_agent.py` | 144 | 各错误分支与 `candidate_labels` 修正产出 |
| `tests/test_api_server.py` | 433 | 任务 CRUD、labels 端点、images/report/export/step、CORS、路径遍历、标签容错、鉴权 |
| `tests/test_config.py` | 92 | 三层配置优先级与角色覆盖 |
| `tests/test_cli.py` | 40 | `run.py` 退出码 |

**不包含**

- 前端单测（`gui/` **无测试框架**，前端只有 `tsc --noEmit` 门禁）
- 真实模型接入的端到端验证（`provider: remote` 需外部服务，不属本套件）

## 三、接口契约

| 项 | 契约 |
|----|------|
| 运行 | `uv run pytest -q`（`pyproject.toml`：`testpaths=["tests"]`、`pythonpath=["."]`） |
| 单文件 | `uv run pytest tests/test_x.py -q`，**必须 ≤10s** |
| 过滤 | `uv run pytest -k "表达式"` |
| 命名 | `test_{方法}_{场景}_{预期}`（见 `.claude/rules/assertion-integrity.md`） |
| 断言依据 | **只依赖公开 API**，不测私有函数内部状态 |

## 四、数据模型（隔离产物）

| 手段 | 用途 |
|------|------|
| `tmp_path` | 构造隔离 tasks root，**不读写仓库内 `tasks/`** |
| `monkeypatch` | 注入 IO / 环境异常（如 `Path.read_text` 抛 `OSError`、env 变量覆盖） |
| `fastapi.testclient.TestClient` | HTTP 端点测试（dev 依赖含 `httpx`） |
| numpy / cv2 | 现场生成测试图像，不依赖仓库内图片 |

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 1 OBB 格式 | `test_obb_utils.py` 是硬约束 1 值域契约的唯一自动化守护者（越界、往返一致性） |
| 2 标签不可覆盖 | `test_api_server.py:258` 断言读端点**不修改**任何标签文件 |
| 3 任务规则隔离 | `test_api_server.py:246` 断言类别过滤取自**当前任务** `plan.yaml` |
| 7 工程约束 | `make lint` 与 `make type` 的检查面含 `tests/`（lint）与 `src/`（type） |

## 六、边界条件

- 无图片任务执行 split → 空 summary 且不崩溃
- 零面积 OBB 的 IoU → `0.0`
- 测试**相互独立**，任一文件可单独运行
- **跨平台文件锁不可靠**：Linux/macOS 无强制锁，真实独占写法在 CI（ubuntu-latest）上永远通过 ⇒ IO 失败一律 `monkeypatch` 注入
- `get_settings()` 带 `@lru_cache(maxsize=1)` ⇒ 测配置需绕缓存（直接调 `load_settings()` 或清缓存）
- 运行时有 2 条来自 starlette/anyio 的 `DeprecationWarning`，属上游告警，非本项目缺陷

## 七、验收标准

1. `uv run pytest -q` 全绿（当前 **99 passed**，本次实测）
2. `make lint`（含 `tests/`）与 `make type` 零错误
3. 断言有效性逐条满足 `.claude/rules/assertion-integrity.md` 的判据：**「若把实现改错，这条断言会变红吗？」**

> 覆盖率：历史记录为 88% → 98%（2026-09 补测到 99 例那一轮）。**本次未复测** —— `coverage` 不在 `[dev]` extra 内，仓库根的 `.coverage` 是当时留下的产物。引用该百分比时请标注为历史值，或先把 `coverage` 参加 dev 依赖后复测。

## 已知缺口

- 🟡 **T1**：7 处 `pytest.raises` 缺 `match=`，异常消息改错不会变红
- 🔵 **A8**：`tests/test_annotate_agent.py:19` 存在冗余赋值

## 关联

- 断言纪律：`.claude/rules/assertion-integrity.md`、`test-design` skill
- 发现账本：T1、A8（`docs/audit-reports/README.md`）
