---
kind: configuration_system
name: 基于 YAML 的任务驱动配置系统（全局配置 + 任务模板 + Agent 提示词）
category: configuration_system
scope:
    - '**'
source_files:
    - config/global.yaml
    - config/task_template.yaml
    - src/utils/yaml_utils.py
    - src/core/task_manager.py
    - src/api_server.py
    - run.py
    - prompts/plan_agent.yaml
    - prompts/annotate_agent.yaml
    - prompts/inspect_agent.yaml
    - tasks/demo/task.yaml
    - tasks/demo/plan.yaml
    - pyproject.toml
---

## 1. 使用的系统与工具

本项目没有引入外部配置框架（如 Pydantic Settings、dynaconf、python-dotenv），而是采用**纯 YAML 文件 + 自定义加载器**的轻量方案：
- 使用 `pyyaml`（`pyproject.toml` 中声明 `pyyaml>=6.0.2`）作为唯一序列化/反序列化后端。
- 所有配置以 `.yaml` 明文文件形式存在，通过 `src/utils/yaml_utils.py` 中的 `load_yaml` / `save_yaml` 统一读写。
- CLI (`run.py`) 与 FastAPI 服务 (`src/api_server.py`) 均通过命令行参数或硬编码路径直接定位配置文件，无环境变量覆盖层。

## 2. 关键文件与包

| 文件 | 作用 |
|---|---|
| `config/global.yaml` | 全局运行配置：模型名称、设备、推理阈值、paths、server、logging |
| `config/task_template.yaml` | 新建任务时复制的默认 `training_plan` 模板（classes、split、hyperparameters、inspection、metrics） |
| `prompts/*.yaml` | 三类 Agent（plan_agent、annotate_agent、inspect_agent）的 system_prompt + user_prompt_template |
| `src/utils/yaml_utils.py` | 统一的 YAML 加载/保存封装（强制 root 为 mapping、空文件返回 `{}`、自动创建父目录） |
| `src/core/task_manager.py` | 任务生命周期管理：从 `config/task_template.yaml` 生成 `tasks/<name>/task.yaml` |
| `src/api_server.py` | FastAPI 入口，硬编码 `_TASKS_ROOT = Path("tasks")` 并实例化 `TaskManager` / `Pipeline` |
| `run.py` | CLI 入口，通过 `argparse` 暴露 `--tasks-root`、`--log-level` 等运行时开关 |
| `tasks/demo/task.yaml` | 示例任务的最终合并配置（由 plan 步骤写入） |
| `tasks/demo/plan.yaml` | 由 plan agent 生成的训练计划（被合并进 task.yaml） |
| `pyproject.toml` | 项目元数据、依赖、ruff/mypy/pytest 工具链配置 |

## 3. 架构与设计约定

### 3.1 配置分层

当前实现是**扁平式**而非分层覆盖：
- **全局配置** `config/global.yaml` 描述平台级参数（模型、推理、路径、服务器、日志），但代码中并未在启动时集中读取；它更像一份“文档型”配置，供用户参考和手动编辑。
- **任务模板** `config/task_template.yaml` 是真正的可执行配置源。`TaskManager.create_task()` 会将其作为种子，填入 `task.name` 和 `task.description` 后持久化为 `tasks/<name>/task.yaml`。
- **任务实例配置** `tasks/<name>/task.yaml` 是最终生效的配置，包含 `task`、`training_plan`（classes、split、model、hyperparameters、inspection、metrics、task_overview）。
- **Agent 提示词** `prompts/*.yaml` 独立于业务配置，按 agent 类型组织，每个文件提供 `system_prompt` 与 `user_prompt_template` 两个字段。

### 3.2 配置加载流程

1. CLI 通过 `argparse` 解析 `--tasks-root`（默认 `tasks`）、`--log-level`（默认 `INFO`）。
2. `TaskManager(Path(args.tasks_root))` 初始化，内部调用 `ensure_dir` 确保根目录存在。
3. 新建任务时，`_template_config()` 尝试加载 `config/task_template.yaml`，不存在则回退到最小默认字典 `{"task": {}, "training_plan": {}}`。
4. 任务配置通过 `load_yaml` / `save_yaml` 读写，路径拼接遵循 `<tasks_root>/<name>/task.yaml` 的固定布局。
5. API 服务模块级构造全局单例 `_task_manager = TaskManager(Path("tasks"))`，所有路由共享同一实例。

### 3.3 配置校验与约束

- `yaml_utils.load_yaml` 强制 YAML 根节点必须是 mapping（dict），否则抛出 `ValueError`；空文件返回 `{}`。
- `TaskManager.create_task` 对同名任务抛出 `FileExistsError`，防止覆盖。
- `TaskManager.load_task` 在 `task.yaml` 缺失时抛出 `FileNotFoundError`。
- `TaskManager.delete_task` 在任务不存在时抛出 `FileNotFoundError`。
- API 层将底层异常映射为 HTTP 状态码：404（任务/图片/标签缺失）、409（任务已存在）、500（步骤执行异常）。
- Pydantic `BaseModel` 用于 API 请求/响应体校验（如 `StepRequest.step` 限定为 `Literal["plan", "annotate", "inspect", "split"]`）。

### 3.4 未实现的特性

- **无环境变量覆盖**：未发现任何 `os.environ` 或 `.env` 文件的使用；`global.yaml` 中的 server/host/port/logging 仅作为参考，实际由 `api_server.py` 硬编码 `host="127.0.0.1"`、`port=8765`，CLI 通过 `--log-level` 单独覆盖日志级别。
- **无配置合并策略**：`plan.yaml` 与 `task_template.yaml` 之间没有显式的 deep merge 逻辑；`tasks/demo/task.yaml` 看起来是 plan agent 输出的完整结构，而非 template 与 plan 的合并产物。
- **无配置热重载**：FastAPI 未启用 `reload=True`（`global.yaml` 中有该字段但未被读取）。
- **无 secrets 管理**：未发现密钥注入机制，所有敏感信息（如模型名）以明文 YAML 存储。

## 4. 约定与约束总结

- 所有 YAML 文件必须使用 UTF-8 编码，且根节点必须是 mapping。
- 任务目录结构固定为 `<tasks_root>/<name>/{images,ai_labels,candidate_labels,dataset}`，由 `TaskManager` 保证。
- 新建任务时，`task.yaml` 始终源自 `config/task_template.yaml`；若模板缺失则回退为空结构。
- Agent 提示词统一放在 `prompts/` 下，文件名对应 agent 类型，字段名为 `system_prompt` 与 `user_prompt_template`。
- 路径配置（`tasks_root`、`prompts_dir`、`config_dir`）集中在 `config/global.yaml`，但当前代码未统一读取——各组件仍通过相对路径硬编码访问。
- 测试通过 pytest 的 `tmp_path` 隔离文件系统，避免污染真实 `tasks/` 目录。
- 开发工具链配置（ruff、mypy、pytest）集中在 `pyproject.toml`，并通过 `[tool.ruff.lint.per-file-ignores]` 排除 `__init__.py` 的 F401 警告。
