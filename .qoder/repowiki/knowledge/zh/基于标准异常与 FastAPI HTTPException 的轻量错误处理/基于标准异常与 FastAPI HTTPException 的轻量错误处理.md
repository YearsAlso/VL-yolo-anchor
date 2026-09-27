---
kind: error_handling
name: 基于标准异常与 FastAPI HTTPException 的轻量错误处理
category: error_handling
scope:
    - '**'
source_files:
    - src/api_server.py
    - src/core/task_manager.py
    - src/utils/image_utils.py
    - src/utils/yaml_utils.py
    - src/agents/annotate_agent.py
    - tests/test_api_server.py
---

## 1. 采用的方案

仓库没有自定义异常类或统一的错误码枚举，而是采用 Python 内置异常 + FastAPI `HTTPException` 的组合：
- 业务层（`src.core.*`、`src.utils.*`、`src.agents.*`）直接抛出 `FileNotFoundError`、`FileExistsError`、`ValueError` 等标准异常。
- API 层（`src/api_server.py`）在路由函数中捕获这些异常并转换为带 HTTP 状态码的 `HTTPException`，作为 GUI 可消费的响应。
- 日志通过标准库 `logging`（模块级 `logger = logging.getLogger(...)`），使用 `logger.warning` / `logger.exception` 记录警告和堆栈。
- 未定义全局异常处理器（middleware）、未使用 `try/except` 包裹整个应用；每个路由自行决定如何转换异常。

## 2. 关键文件

- `src/api_server.py` — FastAPI 应用入口，唯一集中进行 `HTTPException` 映射的位置。
- `src/core/task_manager.py` — 任务生命周期，抛出 `FileExistsError` / `FileNotFoundError`。
- `src/utils/image_utils.py` — 图像 I/O，抛出 `FileNotFoundError` / `ValueError`。
- `src/utils/yaml_utils.py` — YAML I/O，抛出 `FileNotFoundError` / `ValueError`。
- `src/agents/annotate_agent.py` — Agent 逻辑，抛出 `FileNotFoundError`，并对解析失败走 `try/except ValueError` 后 `logger.warning` 跳过。
- `tests/test_api_server.py` — 断言 409（重复任务）、404（缺失任务/图片/标签）等状态码。

## 3. 架构与约定

### 3.1 分层异常传播

```text
utils/agents (业务层) → core/pipeline → api_server (HTTP 层)
```

业务层只抛标准异常，不关心 HTTP 语义；API 层负责把异常翻译成 HTTP 状态码。例如：

- `TaskManager.create_task` 抛出 `FileExistsError`，`create_task` 路由捕获后转为 `HTTPException(status_code=409, detail=str(exc))`。
- `run_step` 用 `except Exception as exc`（注释注明 `# surface agent errors to the GUI`）捕获任意步骤异常，记录 `logger.exception` 后返回 `500`。
- 其余路由对“找不到”的情况直接 `raise HTTPException(status_code=404, detail=...)`，因为此时已经知道是客户端参数错误而非内部故障。

### 3.2 状态码约定（由实现与测试共同体现）

| 场景 | HTTP 状态码 | 依据 |
|---|---:|---|
| 创建已存在的任务 | 409 | `api_server.py` L164–165；`test_api_server.py` L50 |
| 访问不存在的任务 | 404 | 多个路由显式 raise；`test_api_server.py` L96、L121 |
| 访问不存在的图片/标签文件 | 404 | `get_label_boxes` / `get_image` 显式 raise；`test_api_server.py` L97–100 |
| 流水线步骤执行失败 | 500 | `run_step` 的 `except Exception` 分支；`test_api_server.py` L117 |
| 成功 | 200 | 所有正常路径返回 Pydantic model，FastAPI 自动序列化 |

### 3.3 数据校验失败的降级策略

对于“部分数据损坏但可继续”的场景，代码选择**记录警告并跳过**而非抛出异常：

- `_parse_label_file`（`api_server.py` L108–116）：每行解析失败时 `logger.warning("Skipping malformed label line...")` 并 `continue`。
- `AnnotateAgent._detections_to_yolo_lines`（`annotate_agent.py` L141–147）：VL 输出字段缺失/类型错误时 `logger.warning("Skipping malformed detection")` 并跳过该检测。
- `validate_image`（`image_utils.py` L64–67）：捕获 `FileNotFoundError` / `ValueError` 返回 `False`，将异常转化为布尔判定。

这种模式使标注流水线能容忍少量脏数据而不中断。

### 3.4 日志约定

- 每个模块通过 `logging.getLogger(__name__ / __class__.__name__)` 获取 logger。
- 业务异常用 `logger.warning` 记录可恢复问题（坏行、未知 class id）。
- 不可恢复的内部错误用 `logger.exception` 记录完整堆栈（`run_step` 的 500 分支）。
- 无结构化日志框架（如 structlog），也无日志级别配置。

## 4. 观察到的约定与约束

- **业务层不直接使用 `HTTPException`**：除 `api_server.py` 外，其他 `.py` 文件中未见 `from fastapi import HTTPException`，异常全部为 Python 内置类型。
- **`run_step` 是唯一 catch-all 异常点**：它用 `except Exception` 捕获 pipeline 内任何异常并统一返回 500，注释明确说明目的是把 agent 错误暴露给 GUI。
- **404 用于“资源不存在”**：任务、图片、标签文件、inspection report、export summary 缺失均返回 404，且由测试断言覆盖。
- **409 用于“冲突”**：仅用于重复任务创建，由 `FileExistsError` 转换而来。
- **无全局异常中间件**：未发现 `@app.exception_handler` 或自定义 middleware；错误处理分散在每个路由函数内。
- **无自定义异常基类**：仓库中没有 `errors/` 目录或自定义 exception 类定义。
- **GUI 侧（Tauri + React）**：当前 `gui/src/services/api.ts` 及页面组件中未发现针对非 2xx 响应的专门错误处理逻辑（本次分析范围限于后端）。