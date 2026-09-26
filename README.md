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

## 质量检查

```bash
uv run ruff check src/ run.py   # 零告警
uv run mypy                     # strict 模式，零错误
uv run pytest                   # 测试（tests/）
```

## 当前状态 / 已知限制

- **PlanAgent / AnnotateAgent 使用确定性 stub 推理**（`StubLLMClient` /
  固定伪检测框），用于离线跑通全流程；接入真实 Qwen2.5-VL 时实现
  `LLMClient` 协议 / 替换 `AnnotateAgent._infer` 即可，提示词与解析逻辑已就绪。
- `gui/` 与 `gui/src-tauri/` 依赖未随仓库提交，需先 `npm install`
  （`npm run tauri dev` 另需 Rust toolchain）。
- SDD 流程文档见 `docs/sdd-workflow.md`；正式 spec 位于 `specs/`，
  变更历史位于 `changes/`。
