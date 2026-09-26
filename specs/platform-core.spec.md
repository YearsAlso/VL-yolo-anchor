# Spec: platform-core（一期基线，as-built）

## 功能描述

通用工业缺陷 YOLO-OBB 训练计划生成与智能标注平台的核心链路：
自然语言任务描述 → PlanAgent 生成结构化训练计划 → AnnotateAgent 调用本地 VL 模型（Qwen2.5-VL int4）自动 OBB 标注 → InspectAgent 质检并输出候选修正 → 导出可直接训练的 YOLO-OBB 数据集。

对外形态：CLI（`run.py`）、FastAPI 服务（`src/api_server.py`，127.0.0.1:8765）、Tauri + React + Ant Design GUI（`gui/`）。

## 输入约束

- Python 3.11+，包管理 uv + `pyproject.toml`（无 requirements.txt）。
- 任务由 `TaskManager` 管理于 `tasks/<name>/`：`task.yaml`、`images/`、`ai_labels/`、`candidate_labels/`、`dataset/`、`plan.yaml`、`inspection_report.yaml`。
- 任务配置种子为 `config/task_template.yaml`；全局配置 `config/global.yaml`。
- Agent 系统提示词外置于 `prompts/{plan,annotate,inspect}_agent.yaml`，不硬编码。

## 输出约束

- OBB 一律为 4 顺时针归一化角点 `[x1,y1,x2,y2,x3,y3,x4,y4]`，范围 [0,1]；不使用水平矩形。
- 标签文件行格式 `cls x1 y1 ... x4 y4`，写入 `ai_labels/`；质检修正只写 `candidate_labels/`。
- 每任务规则隔离：任务自带 classes/rules，agent 不跨任务混用。
- 训练导出：`dataset/{train,val,test}/{images,labels}`、`data.yaml`、`train_command.txt`、`export_summary.yaml`。
- Python 侧承担全部 CV/推理/文件 IO；前端仅画布渲染与 UI。

## 边界条件

- 显存目标 RTX 3060Ti 8GB：VL 模型 4-bit 加载，支持 CPU 回退。
- 无图片任务执行 split → 空 summary；未知 step → `ValueError`；任务不存在 → `FileNotFoundError` / HTTP 404。
- 一期 LLM 与 VL 推理为确定性 stub（`StubLLMClient` / 固定伪检测框），可离线跑通全流程；接入真实模型时实现 `LLMClient` 协议 / 替换 `AnnotateAgent._infer`。

## 验收标准

1. `uv run ruff check src/ run.py` 与 `uv run mypy src/`（strict）零错误。
2. `uv run python run.py --help` 可用；`run.py {plan,annotate,inspect,split,full,create} --task NAME` 可执行。
3. demo 任务全流程产出：`plan.yaml`、`ai_labels/*.txt`、`inspection_report.yaml`、`dataset/`（含 `data.yaml`、`train_command.txt`）。
4. FastAPI 端点按 `README.md` 表格可用。

> 实现：`src/agents/*`、`src/core/{task_manager,pipeline}.py`、`src/utils/*`、`src/api_server.py`、`run.py`、`gui/*`。
