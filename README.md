# VL-YOLO-Anchor

通用工业缺陷 **YOLO-OBB 训练计划生成与智能标注平台**。

通过自然语言描述任务 → **PlanAgent** 生成结构化 YOLO-OBB 训练计划（类别、标注规则、超参数、质检规则）→ **AnnotateAgent** 调用本地 VL 模型（Qwen2.5-VL int4）自动旋转框标注 → **InspectAgent** 质检标签（漏标、重复、类别错误、越界、尺寸异常）并输出候选修正 → 导出可直接训练的 YOLO-OBB 数据集 + yaml + 训练命令。

## 核心约束

- OBB 输出为 **4 顺时针归一化角点** `[x1,y1,x2,y2,x3,y3,x4,y4]`，范围 `[0,1]`，不使用水平矩形。
- **永不覆盖原始标签**；所有修正写入 `candidate_labels/`。
- 任务间规则隔离：每个任务有独立的 classes/rules，agent 不跨任务混用规则。
- 显存目标：RTX 3060Ti（8GB），VL 模型 4-bit 加载，支持 CPU 回退。
- Python 后端负责全部 CV/推理/文件 IO；前端只做画布渲染 + UI。
- 所有 agent 系统提示词外置于 `prompts/*.yaml`，不硬编码。

## 目录结构

```
pyproject.toml          uv 包配置（ruff/mypy/pytest）
config/
  global.yaml           全局配置（模型、推理、路径、服务端口）
  task_template.yaml    任务配置模板（新建任务时拷贝）
prompts/
  plan_agent.yaml       PlanAgent 提示词
  annotate_agent.yaml   AnnotateAgent 提示词
  inspect_agent.yaml    InspectAgent 提示词
  prompt_versions/      提示词版本归档
tasks/                  各任务目录（task.yaml / images / ai_labels /
                        candidate_labels / dataset / inspection_report.yaml / plan.yaml）
src/
  agents/               base_agent, plan_agent, annotate_agent, inspect_agent
  core/                 task_manager, pipeline
  api_server.py         FastAPI 后端（127.0.0.1:8765）
  utils/                obb_utils, image_utils, file_utils, yaml_utils
gui/                    Tauri + React + TypeScript + Ant Design 前端
run.py                  CLI 入口
start_gui.ps1 / .sh     一键启动后端 + GUI
```

## 安装

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 同步基础依赖 + 开发工具
uv venv
uv sync --extra dev

# （可选）启用本地 VL 模型 GPU 推理
uv sync --extra gpu
```

## CLI 使用

```bash
# 创建任务
uv run python run.py create --task demo --description "光伏 EL 图像：crack / break_grid 缺陷"

# 逐步执行
uv run python run.py plan     --task demo
uv run python run.py annotate --task demo
uv run python run.py inspect  --task demo
uv run python run.py split    --task demo

# 或一次性全流程
uv run python run.py full     --task demo
```

图片放置在 `tasks/<task>/images/` 后执行 `annotate`。`split` 会在
`tasks/<task>/dataset/` 生成 `train/val/test` 目录、`data.yaml` 与
`train_command.txt`（可直接粘贴运行的 `yolo obb train ...` 命令）。

## FastAPI 后端

```bash
uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8765
```

主要端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/tasks` | 列出 / 创建任务 |
| POST | `/api/tasks/{name}/step` | 运行单步（plan/annotate/inspect/split） |
| POST | `/api/tasks/{name}/plan` `…/annotate` `…/inspect` | 单步快捷端点 |
| GET | `/api/tasks/{name}/plan` | 当前训练计划 |
| GET | `/api/tasks/{name}/report` | 最新质检报告 |
| GET | `/api/tasks/{name}/export` | 数据集导出摘要 |
| GET | `/api/tasks/{name}/labels` | 有标签文件的图片名列表 |
| GET | `/api/tasks/{name}/labels/{img}` | 单图 OBB 标签（candidate_labels 优先） |
| GET | `/api/tasks/{name}/images` `/images/{img}` | 图片列表 / 图片文件 |

交互式文档：<http://127.0.0.1:8765/docs>

## GUI 启动

```powershell
# Windows
./start_gui.ps1
```

```bash
# Linux / macOS
./start_gui.sh
```

脚本会启动 FastAPI 后端（8765）并在 `gui/` 中运行 Vite 开发服务器（Tauri 打包用
`npm run tauri dev`）。GUI 含四个标签页：任务与计划、标注审核（OBB 画布，缩放/平移）、
质检报告、训练导出。

## Docker 自部署（Linux）

推荐的生产部署方式：后端镜像为纯 CPU（VL/LLM 通过可配置的远程 OpenAI 兼容端点调用，
镜像不含模型权重、无需 GPU），前端为 nginx 托管的静态 SPA 并反向代理 `/api`。

```bash
cp .env.example .env    # 按需修改 VL_MODEL_* / VL_ANCHOR_*
docker compose up -d --build
# 浏览器打开 http://localhost:8080
```

- `backend`：FastAPI，仅回环暴露 `127.0.0.1:8765`；任务与日志写入命名卷 `vl-data:/data`。
- `gui`：nginx 提供静态站（`:8080`），`/api` 反代到 backend，同源免 CORS。
- 对外访问请用带 TLS 的反向代理置于 `gui` 前，并设置 `VL_ANCHOR_AUTH_TOKEN` 开启鉴权。

### 模型后端配置

平台区分两类模型角色（由 `model:` 共享默认 + `llm:`/`vl:` 分角色覆盖，
或环境变量 `VL_MODEL_*` 共享 + `VL_LLM_*`/`VL_VL_*` 分角色）：

| 角色 | 驱动的 Agent | 模型类型 | 用途 |
| --- | --- | --- | --- |
| `llm` | PlanAgent | 文本 LLM | 自然语言描述 → 结构化训练计划 |
| `vl` | AnnotateAgent | 视觉语言模型 | 图像 → OBB 检测框 |

InspectAgent 为纯规则质检，不调用任何模型。`provider` 取值：

| provider | 行为 |
| --- | --- |
| `stub`（默认） | 离线确定性伪输出，无需模型/网络，用于演示与 CI |
| `remote` | 调用对应 `base_url` 的 OpenAI 兼容 `/chat/completions` 端点 |

未单独配置的会自动回退：`llm`/`vl` 缺字段时继承共享 `model:` 块，因此单端点部署
无需逐角色配置；而典型生产部署可让 plan 走便宜文本模型、annotate 走自建
Qwen2.5-VL。配置优先级：分角色 env (`VL_LLM_*`/`VL_VL_*`) > 分角色 yaml >
共享 env (`VL_MODEL_*`) > 共享 yaml `model:` > 内置默认（见 `src/config.py`）。

常用镜像标签：`ghcr.io/<owner>/vl-yolo-anchor-backend` 与 `…-gui`（`latest` / `v*` / sha）。

## 质量检查

```bash
uv run ruff check src/ run.py   # 零告警
uv run mypy                     # strict 模式，零错误
uv run pytest                   # 测试（tests/）
```

## 当前状态 / 已知限制

- **默认 `provider: stub` 使用确定性推理**（`StubLLMClient` / 固定伪检测框），用于离线
  跑通全流程与 CI；设 `provider: remote` 后 `PlanAgent`/`AnnotateAgent` 经
  `OpenAICompatClient` 调用可配置的远程 VL/LLM 端点（自定义 base_url / api_key / model）。
- `gui/` 与 `gui/src-tauri/` 依赖未随仓库提交，需先 `npm install`
  （`npm run tauri dev` 另需 Rust toolchain）。
- SDD 流程文档见 `docs/sdd-workflow.md`；正式 spec 位于 `specs/`，
  变更历史位于 `changes/`。
