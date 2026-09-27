# Spec: platform-core（一期基线，as-built）

> **状态**：已实施（as-built，从 `specs/platform-core.spec.md` 迁移并重组为六要素）
> **设计**：[`design.md`](design.md)
> **最后核对**：2026-09-27

## 一、业务背景

通用工业缺陷 YOLO-OBB 训练计划生成与智能标注平台的核心链路：自然语言任务描述 → `PlanAgent` 生成结构化训练计划 → `AnnotateAgent` 调用 VL 模型自动产出 OBB 标注 → `InspectAgent` 质检并输出候选修正 → 导出可直接训练的 YOLO-OBB 数据集。

不绑定具体行业或成像方式。对外三种形态：CLI（`run.py`）、FastAPI 服务（`src/api_server.py`，默认 `127.0.0.1:8765`）、Tauri + React + Ant Design 桌面/浏览器 GUI（`gui/`）。

## 二、功能范围

**包含**

- 四步编排 `plan → annotate → inspect → split`，支持单步执行（`run_step`）与全流程（`run_full`）
- 任务生命周期：创建、列举、加载、配置播种
- LLM 与 VL 双角色模型接入（`provider: stub` 离线 / `provider: remote` OpenAI 兼容端点）
- CLI 与 HTTP **双入口共享同一 `Pipeline`**

**不包含**

- 模型训练本身（只产出 `dataset/data.yaml` 与 `dataset/train_command.txt`，训练由用户执行）
- 数据库与用户体系（唯一持久层是 `tasks/<task>/` 文件）
- 前端侧的 CV 计算（硬约束禁止）

## 三、接口契约

| 面 | 契约 |
|----|------|
| CLI | `run.py {create,plan,annotate,inspect,split,full} --task NAME`，失败以非零码退出 |
| HTTP | `POST /api/tasks/{name}/step`（`StepRequest.step` ∈ `plan/annotate/inspect/split`）+ 四个专用快捷端点；响应统一 `StepResponse(task, step, result)` |
| Agent 协议 | `BaseAgent.run(...) -> dict`；`VLClient`（`Protocol`）定义 `generate(system_prompt, user_prompt, image_path)` |
| 提示词 | `prompts/{plan,annotate}_agent.yaml` 的 `{}` 占位符 ↔ `src/agents/*.py` 的 `.format()` 实参双向一致 |

**内部编排契约**：`Pipeline.run_step(name, step)` → `_run_plan` / `_run_annotate` / `_run_inspect` / `_run_split`，每步执行前把当前任务的配置交给对应 agent。

## 四、数据模型（磁盘产物与 YAML schema）

| 产物 | 说明 |
|------|------|
| `tasks/<name>/task.yaml` | 任务配置（`config/task_template.yaml` 播种 + 计划合并），agent 的规则来源 |
| `tasks/<name>/plan.yaml` | 训练计划，`classes` 为 `{id: name}` 映射 |
| `tasks/<name>/images/` | 输入图像（`list_images` 非递归，`jpg/jpeg/png/bmp/tif/tiff`） |
| `tasks/<name>/ai_labels/*.txt` | `AnnotateAgent` 产出，**只读** |
| `tasks/<name>/candidate_labels/*.txt` | 质检建议与人工修正的**唯一**写入位置 |
| `tasks/<name>/inspection_report.yaml` | 质检报告 |
| `tasks/<name>/dataset/{train,val,test}/`、`data.yaml`、`train_command.txt`、`export_summary.yaml` | split 导出产物 |

**标签行格式**：`cls x1 y1 x2 y2 x3 y3 x4 y4`，8 个归一化浮点，值域 `[0,1]`。

## 五、硬约束影响

对应 `.claude/rules/obb-hard-constraints.md`：

| 小节 | 本能力的落点 |
|------|-------------|
| 1 OBB 输出格式 | 四点顺时针归一化，只经 `src/utils/obb_utils.py` 的 `normalize_points` / `denormalize_points` / `four_points_to_rotated_box` / `rotated_box_to_four_points` / `obb_iou` |
| 2 标签不可覆盖 | `ai_labels/` 只读；修正只写 `candidate_labels/` |
| 3 任务规则隔离 | 每个 agent 从**当前任务**配置取 `class_mapping` / `annotation_rules` / 阈值；禁止跨任务混用与硬编码类别 ID |
| 4 提示词外置 | 全部走 `prompts/*.yaml` 两段式；占位符集合变更属契约变更 |
| 5 职责边界 | Python 承担全部 CV/推理/IO；前端只渲染 canvas + UI |
| 6 资源目标 | VRAM 预算 RTX 3060Ti 8GB + 4-bit + CPU 回退；远程 API 为主，镜像不内置权重 |
| 7 Python 工程约束 | 3.11+ / 全类型注解 / Google-style docstring / ruff + mypy 零错 / uv + pyproject / 全相对路径 |

> ⚠️ **小节 3 存在已知缺陷**：账本 **A1**（模块级 `_pipeline` 单例 + agent `.config` 就地重赋值 + 全同步端点并发 ⇒ 跨任务规则可被击穿）。本 spec 声明隔离要求，实现尚未完全满足，修复前不得声称并发安全。

## 六、边界条件

- 显存目标 RTX 3060Ti 8GB：本地推理时 VL 模型 4-bit 加载，支持 CPU 回退
- 无图片任务执行 split → 空 summary，不崩溃
- 未知 step → `ValueError`；任务不存在 → `FileNotFoundError` / HTTP 404
- `provider: stub` 为确定性输出（`StubLLMClient` + 固定伪检测框），可完全离线跑通全流程，用于演示与 CI
- 接入真实模型：配置 `provider: remote` 与 `VL_MODEL_*` / `VL_LLM_*` / `VL_VL_*` env，无需改 agent 代码

## 七、验收标准

1. `make lint` 与 `make type` 零错误
2. `uv run python run.py --help` 可用；`run.py {plan,annotate,inspect,split,full,create} --task NAME` 可执行
3. demo 任务全流程产出 `plan.yaml`、`ai_labels/*.txt`、`inspection_report.yaml`、`dataset/`（含 `data.yaml` 与 `train_command.txt`）
4. FastAPI 端点行为与 `docs/architecture/技术架构.md` 第七节一致

## 实现位置

`src/agents/*`、`src/core/{task_manager,pipeline}.py`、`src/utils/*`、`src/api_server.py`、`run.py`、`gui/*`

## 关联

- 设计：[`design.md`](design.md)
- 决策：ADR-001（`docs/memory/architect-decisions.md`）
- 发现账本：A1、A5、A6、S1、IO-1、IO-2、H-3（`docs/audit-reports/README.md`）
