# 归档：一期脚手架 Build Task（2026-09-27 移出 CLAUDE.md）

> **本文件是历史归档，不是当前指引。** 当前代理纪律见根目录 `CLAUDE.md`，模块映射见 `AGENTS.md`，架构事实见 `docs/architecture/技术架构.md`。
> **移出原因**：本内容是项目一期的一次性**生成任务提示词**（"Build the entire project below end-to-end… Do not ask for confirmation"），面向"从零创建脚手架"，**不具备长期约束力**，且其中多项描述已被实现实况取代：
>
> | 本文件中的描述 | 现状 |
> |---------------|------|
> | 目录结构无 `src/config.py` | 已有 218 行三层配置加载（`src/config.py`） |
> | 端点清单 7 个 | 已有 14 个端点 + 1 个 http 中间件 |
> | `api_server.py` 无逐图标签端点 | 已有 `GET .../labels`、`GET .../labels/{img}`、`.../step`、`.../export`、`/api/health` |
> | "calls a local VL model (Qwen2.5-VL, int4)" | 交付形态改为**远程 OpenAI 兼容端点为主**（`provider: remote`），镜像不内置权重；本地 4bit 走 `[gpu]` extra |
> | 依赖清单 8 个（含 `jinja2`） | 现 11 个；`jinja2`/`matplotlib`/`pillow`/`pydantic-settings` 当前**零引用**（账本 D1） |
> | `pydantic-settings` 未提及 | 已声明为依赖但 `Settings` 实为 `BaseModel`，未使用 `BaseSettings`（见 ADR-001） |
> | 无 Docker / CI 章节 | 已有两阶段镜像、compose、三套 GitHub Actions |
> | 单实施者提示词 | 已拆 `backend-engineer` / `frontend-engineer`（ADR-002） |
>
> **硬约束未丢失**：第 14-25 行的 10 条 never-violate 约束已提升为正式规则 `.claude/rules/obb-hard-constraints.md`（7 小节），本文不再作为约束源。

---

# Build Task: VL-YOLO-Anchor Platform

You are an autonomous senior full-stack CV engineer. Build the entire project below end-to-end. Create every file, install dependencies, run lint/type checks, and fix all errors until the scaffold compiles. Do not ask for confirmation — proceed autonomously.

## What to build

A **generic industrial defect YOLO-OBB training plan generation & intelligent annotation platform**. Not tied to any specific industry or imaging modality. The flow:

1. User describes a task in natural language (industry, modality, defects) → **PlanAgent** generates a structured YOLO-OBB training plan (classes, rules, hyperparams, inspection rules).
2. **AnnotateAgent** calls a local VL model (Qwen2.5-VL, int4 quantized) to auto-label images with OBB boxes per the plan.
3. **InspectAgent** checks labels for errors (missing, duplicate, wrong-class, out-of-range, rule conflicts) and outputs candidate corrections.
4. Pipeline exports a ready-to-train YOLO-OBB dataset + yaml + training command.

## Hard constraints (never violate)

- OBB output is 4 clockwise normalized points [x1,y1,x2,y2,x3,y3,x4,y4], range [0,1]. Never use horizontal rectangles.
- Never overwrite original labels. All corrections go to `candidate_labels/`.
- Per-task rule isolation: every task has its own classes/rules; agents must not mix rules across tasks.
- VRAM target: RTX 3060Ti (8GB). VL model loads in 4-bit. Support CPU fallback.
- Python backend does ALL CV/inference/file IO. Frontend only renders canvas + UI.
- GUI stack: **Tauri + React + TypeScript + Ant Design**. No Vue, no PyQt.
- All agent system prompts are external YAML files under `prompts/`, never hardcoded.
- Python: full type hints on every public function/class, Google-style docstrings, zero ruff/mypy errors.
- Package manager: **uv + pyproject.toml**. No requirements.txt.
- All paths relative. No hardcoded absolute paths.

## Directory structure (create exactly)

```
pyproject.toml
config/
  global.yaml
  task_template.yaml
prompts/
  plan_agent.yaml
  annotate_agent.yaml
  inspect_agent.yaml
  prompt_versions/
tasks/
src/
  __init__.py
  agents/
    __init__.py
    base_agent.py
    plan_agent.py
    annotate_agent.py
    inspect_agent.py
  core/
    __init__.py
    task_manager.py
    pipeline.py
  api_server.py
  utils/
    __init__.py
    obb_utils.py
    image_utils.py
    file_utils.py
    yaml_utils.py
gui/
  package.json
  tsconfig.json
  vite.config.ts
  index.html
  src/
    main.tsx
    App.tsx
    components/
    pages/
    hooks/
    types/
    services/
start_gui.ps1
start_gui.sh
README.md
run.py
```

## pyproject.toml (already exists — verify/keep it)

Dependencies: opencv-python, pyyaml, numpy, pillow, matplotlib, fastapi, uvicorn, pydantic, jinja2.
Optional [gpu]: torch, torchvision, transformers, accelerate, bitsandbytes.
Optional [dev]: ruff, mypy, pytest.
Configure [tool.ruff] (line-length=120, select E/W/F/I/N/UP/B/SIM) and [tool.mypy] (strict=true, disallow_untyped_defs=true).

## Module specs

### src/utils/obb_utils.py
- `OBBBox` dataclass: points(list[float]), cls(int), conf(float)
- `four_points_to_rotated_box(points) -> (cx,cy,w,h,angle)` using cv2.minAreaRect
- `rotated_box_to_four_points(cx,cy,w,h,angle) -> list[float]` using cv2.boxPoints
- `obb_iou(a, b) -> float` using cv2.intersectConvexConvex
- `normalize_points(points, w, h)`, `denormalize_points(...)`, `is_coords_in_range(points)`

### src/utils/image_utils.py
- `load_image(path) -> np.ndarray` (cv2.imread, grayscale-capable)
- `validate_image(path) -> bool` (check file exists, decodes, min size)

### src/utils/file_utils.py
- `list_images(dir) -> list[Path]` (jpg/png/bmp/tif)
- `ensure_dir(path)` create if missing

### src/utils/yaml_utils.py
- `load_yaml(path) -> dict`
- `save_yaml(data, path)`

### src/agents/base_agent.py
- Abstract `BaseAgent` with `__init__(self, config: dict, prompt_dir: Path)`, `load_prompt(name) -> dict`, abstract `run(...) -> dict`. Logging built in.

### src/agents/plan_agent.py
- `PlanAgent(BaseAgent)`: `run(task_description: str, dataset_size: str) -> dict`
- Calls an LLM (stub an interface; real impl optional). Parses structured plan into dict matching task_template.yaml schema.

### src/agents/annotate_agent.py
- `AnnotateAgent(BaseAgent)`: `run(task_dir: Path, task_config: dict) -> list[Path]`
- Stub VL inference (no actual model download). For each image: load prompt from prompts/annotate_agent.yaml, inject class_mapping/rules, produce dummy OBB results, write YOLO OBB txt to `ai_labels/`. Filter by conf_threshold and min_defect_pixels.

### src/agents/inspect_agent.py
- `InspectAgent(BaseAgent)`: `run(task_dir: Path, task_config: dict) -> dict`
- Check: missing_labels, duplicate_boxes (IoU > threshold), class_error (cls not in allowed), coords_out_of_range, size_anomaly. Output report dict + write candidate_labels.

### src/core/task_manager.py
- `TaskManager(tasks_root: Path)`: `create_task(name, description) -> Path`, `list_tasks() -> list[str]`, `load_task(name) -> dict`, `task_dir(name) -> Path`.

### src/core/pipeline.py
- `Pipeline(task_manager, agents)`: `run_full(task_name)` orchestrates plan → annotate → inspect → split. `run_step(task_name, step)`.

### src/api_server.py
- FastAPI app. Endpoints:
  - GET /api/tasks, POST /api/tasks
  - POST /api/tasks/{name}/plan, POST /api/tasks/{name}/annotate, POST /api/tasks/{name}/inspect
  - GET /api/tasks/{name}/report
  - GET /api/tasks/{name}/images, GET /api/tasks/{name}/images/{img}
- Pydantic request/response models.

### run.py
- argparse CLI: `run.py plan|annotate|inspect|split --task NAME`. Uses TaskManager + Pipeline.

### prompts/*.yaml
Three YAML files with `system_prompt` and `user_prompt_template` keys. Content as specified in the project design (generic, parameterized — no EL/PL hardcoding).

### gui/ (Tauri + React + TS + Ant Design)
- package.json with react, react-dom, antd, @ant-design/charts, axios, vite, @tauri-apps/cli, typescript.
- src/App.tsx with Ant Design Tabs: (1) Task & Plan, (2) Annotation Review, (3) Inspection Report, (4) Training Export.
- src/types/ with TS interfaces: ImageItem, OBBBox, TaskInfo, TaskPlan, InspectionReport.
- src/services/api.ts: axios wrapper to FastAPI at 127.0.0.1:8765.
- src/components/OBBCanvas.tsx: canvas rendering with zoom/pan, OBB box drawing (stub acceptable).
- src/pages/: four page components.

## Code style

- Python 3.11+, `from __future__ import annotations` in every module.
- Full type hints, no bare `Any`.
- Google-style docstrings on every public module/class/function.
- Run `uv run ruff check src/` and `uv run mypy src/` — fix until zero errors.

## Execution steps

1. Verify pyproject.toml exists.
2. Create all files listed above.
3. Run `uv venv .venv && uv sync --extra dev` (gpu extra optional, skip if it fails — note it).
4. Run `uv run ruff check src/` and fix.
5. Run `uv run mypy src/` and fix.
6. Run `uv run python -c "from src.core.task_manager import TaskManager; print('ok')"` to verify imports.
7. Create a demo task: `uv run python run.py plan --task demo` (stub plan generation is fine).
8. Write README.md with: install steps (uv), CLI usage, GUI launch steps, project overview.
9. Report what you built and any gaps.

## Acceptance

- All files exist per structure.
- `uv run ruff check src/` passes.
- `uv run mypy src/` passes.
- `uv run python run.py --help` works.
- README is complete.
