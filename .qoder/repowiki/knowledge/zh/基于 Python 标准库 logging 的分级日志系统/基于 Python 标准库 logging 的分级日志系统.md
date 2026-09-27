---
kind: logging_system
name: 基于 Python 标准库 logging 的分级日志系统
category: logging_system
scope:
    - '**'
source_files:
    - run.py
    - src/api_server.py
    - src/core/pipeline.py
    - src/core/task_manager.py
    - src/agents/base_agent.py
---

## 1. 使用的系统与框架

仓库未引入第三方日志框架（如 structlog、loguru、sentry），完全依赖 Python 标准库 `logging`。所有模块通过 `logging.getLogger(name)` 获取命名 logger，由入口统一配置根 handler。

## 2. 关键文件与位置

- `run.py`：CLI 入口，调用 `logging.basicConfig(level=..., format="%(asctime)s %(name)s %(levelname)s %(message)s")`，并通过 `--log-level` 参数控制级别（默认 INFO）。
- `src/api_server.py`：FastAPI 服务，使用模块级 logger `logging.getLogger("api_server")`，在异常捕获处用 `logger.exception(...)` 输出堆栈。
- `src/core/pipeline.py`：Pipeline 类实例化 `self.logger = logging.getLogger(self.__class__.__name__)`，记录步骤执行、计划生成、数据集导出等流程事件。
- `src/core/task_manager.py`：同样以类名为 logger name 的方式获取 logger。
- `src/agents/base_agent.py`：BaseAgent 基类为每个子类构造 `self.logger = logging.getLogger(self.__class__.__name__)`，并在 `load_prompt` 中记录 prompt 加载信息。
- 其余 agent 实现（`plan_agent.py`、`annotate_agent.py`、`inspect_agent.py`）继承 BaseAgent 复用该 logger。

## 3. 架构与约定

- **Logger 命名策略**：采用“类名/模块名”作为 logger name 的分层方式。CLI 使用 root logger；API server 使用固定名称 `api_server`；业务组件（Pipeline、TaskManager、各 Agent）均使用 `__class__.__name__` 作为 logger name，便于按模块过滤日志。
- **日志级别**：仅使用 `debug` / `info` / `warning` / `error` / `exception` 五个级别。CLI 默认 INFO，可通过 `--log-level` 调整；API 侧未暴露级别开关，沿用默认。
- **日志格式**：单一格式字符串 `"%(asctime)s %(name)s %(levelname)s %(message)s"`，包含时间戳、logger 名称、级别和消息文本，无 JSON 结构化字段。
- **结构化字段**：日志消息本身是纯文本，通过 `%` 格式化拼接上下文（如任务名、步骤名、类名列表），没有独立的 structured fields（如 JSON 键值）。唯一接近结构化的地方是 `pipeline._run_split` 将 split 统计字典直接作为消息的一部分输出。
- **错误处理中的日志**：
  - CLI 中 `FileNotFoundError` 等异常通过 `logging.error` 输出后返回非零退出码。
  - API 中 `run_step` 捕获异常后用 `logger.exception` 记录完整堆栈，再抛出 HTTP 500。
  - 数据解析失败（如 label 文件格式错误）使用 `logger.warning` 跳过并继续。

## 4. 约定与约束

- **集中式初始化**：只有 `run.py` 调用 `logging.basicConfig` 配置根 handler；其他模块只负责获取 logger 并写入消息，不重复配置 handler，避免重复输出。
- **禁止裸 print 替代日志**：业务逻辑路径（pipeline、agent、api）全部走 logger；仅在 CLI 成功分支用 `print` 输出简短结果摘要（如 `Created task ...`、`== plan ==`），不属于可观测性日志。
- **按模块隔离**：每个核心类独立 logger，便于后续按 name 过滤或路由到不同 sink（当前未启用多 sink）。
- **无异步日志**：FastAPI 端点同步调用 pipeline，未使用异步日志器或队列缓冲。
- **无外部 sink**：当前所有日志输出至 stdout/stderr，未配置文件轮转、远程收集（如 ELK、Sentry）或结构化 JSON 输出。
- **测试中未覆盖日志行为**：测试套件（`tests/`）主要断言返回值与文件系统状态，未发现对 logger 输出或级别的断言。