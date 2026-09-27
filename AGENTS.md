# AGENTS.md

面向 **Qoder / Cursor / Copilot 及其他编码代理**的项目说明。完整开发纪律、hooks、审查路由与文档地图见 [`CLAUDE.md`](CLAUDE.md)（本文件与其内容不冲突，冲突时以 `CLAUDE.md` 与 `.claude/` 下的 rule/skill 正文为准）。

## 项目定位

**VL-YOLO-Anchor** —— YOLO-OBB 训练计划生成 + 智能标注平台。

用户用自然语言描述行业 / 成像方式 / 缺陷类型，系统产出结构化训练计划，调用视觉语言模型批量标注旋转框，做质检并导出可直接训练的数据集。**训练本身不在系统内**：只产出 `data.yaml` 与 `train_command.txt`。

- **后端**：Python 3.11+，FastAPI + Pydantic，uv 管理，`src/` 包；CLI 入口 `run.py`，HTTP 入口 `src/api_server.py`
- **前端**：React 18 + TypeScript strict + Ant Design 5 + Vite 5，桌面壳 Tauri v1，位于 `gui/`
- **持久层**：**无数据库**。唯一持久层是 `tasks/<task>/` 下的 YAML 与 txt 文件
- **模型侧**：默认 `provider: stub`（离线确定性输出）；生产走**远程 OpenAI 兼容端点**（LLM 与 VL 双角色配置）；镜像不内置模型权重

## 模块映射

| 路径 | 职责 | 代理角色 |
|------|------|---------|
| `src/utils/obb_utils.py` | OBB 归一化/反归一化、四点↔旋转框互转、`obb_iou` | backend-engineer |
| `src/utils/{image,file,yaml}_utils.py` | 图像加载、图片列举、YAML 读写（`safe_load`） | backend-engineer |
| `src/agents/` | `base_agent`、`plan_agent`、`annotate_agent`、`inspect_agent`、`model_client` | backend-engineer |
| `src/core/pipeline.py` | `plan → annotate → inspect → split` 四步编排 | backend-engineer |
| `src/core/task_manager.py` | 任务目录生命周期与 `task.yaml` 读写 | backend-engineer |
| `src/config.py` | env > `config/global.yaml` > 默认值 三层配置 | backend-engineer |
| `src/api_server.py` | FastAPI 应用、Pydantic 模型、14 个端点 | backend-engineer |
| `run.py` | CLI 入口 | backend-engineer |
| `prompts/*.yaml`、`config/*.yaml` | 外置提示词与配置 schema | backend-engineer |
| `tests/` | pytest 套件（基线 99 passed） | unit-tester |
| `gui/src/components/`、`gui/src/pages/`、`gui/src/hooks/` | OBBCanvas、四标签页、视口 hook | frontend-engineer |
| `gui/src/services/api.ts` | **唯一 axios 出口** | frontend-engineer |
| `gui/src/types/index.ts` | 与后端 Pydantic 对应的 TS 接口 | frontend-engineer |
| `gui/src-tauri/` | Tauri v1 壳（`src/main.rs` 仅 8 行） | frontend-engineer |
| `Dockerfile`、`gui/Dockerfile`、`docker-compose.yml` | 两阶段镜像 + nginx `/api` 反代 | backend-engineer |

## 命名与分层约定

**后端**

- 分层依赖**恒为** `src/utils` ← `src/agents` ← `src/core` ← `src/api_server.py` / `run.py`，**禁止反向 import**
- 每模块 docstring 后首行 `from __future__ import annotations`；公开函数/类**全类型注解**，禁裸 `Any`
- Google-style docstring；`logging` 分级日志，**禁 `print`**；异常消息与 docstring 用**英文**
- 模块/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`，私有 `_` 前缀
- 一律 `pathlib.Path` + **全相对路径**；外部输入拼路径必须经前缀校验（`_safe_child` 模式）
- 包管理只用 **uv + `pyproject.toml`**，禁止 `requirements.txt` 作为依赖源

**前端**

- 页面/组件**只经 `services/api.ts`** 访问后端，禁止组件内直连 axios
- TS strict，禁 `any` 逃逸；`gui/src/types/index.ts` 与后端 Pydantic 模型**逐字段对齐**（Pydantic 是唯一事实源）
- 优先 AntD 组件，禁止裸 HTML 重造轮子；**禁止**引入 Vue / PyQt / 其他 UI 框架 / 状态管理库 / 路由库
- **canvas 渲染**用 `useRef` + `requestAnimationFrame`，禁止每帧重建对象
- **职责边界硬约束**：前端只渲染 canvas + UI，所有 CV / 推理 / 文件 IO 留在 Python
- UI 文案语言**与所在文件既有语言保持一致**（现存不一致已登记为技术债，勿自行扩大）

**文档**：`docs/**`、`specs/**`、`.claude/**`、`CHANGELOG.md` 用中文；代码注释与 docstring 用英文。

## 命令入口

| 场景 | 命令 |
|------|------|
| 安装依赖 | `uv sync --extra dev`；前端 `make gui-install` |
| 静态检查 | `make lint`（ruff）、`make type`（mypy strict） |
| 测试 | `make test`；单文件 `uv run pytest tests/test_x.py -q`；表达式过滤 `uv run pytest -k "expr"` |
| 前端 | `make gui-typecheck`、`make gui-build` |
| 起后端 | `uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8765` |
| 资产门禁 | `make assets-check`（7 项）、`make assets-sync`（生成 `.qoder` 镜像） |
| 启用 git hooks | `make hooks-install`（= `git config core.hooksPath .githooks`） |
| 容器 | `make docker-build`、`make up`、`make down`、`make logs`、`make ps` |

本机无 `make` 时直接跑对应裸命令。

## Agent 资产入口

| 资产 | 位置 | 说明 |
|------|------|------|
| Rules（8） | `.claude/rules/` | 强制遵循的规范源；**仅 `.claude/` 单份**，无 `.qoder` 副本 |
| Workflows（8） | `.claude/workflows/` | 场景工作流；**仅 `.claude/` 单份** |
| Agents（9） | `.claude/agents/` → 镜像 `.qoder/agents/` | 文件级单向同步 |
| Skills（19） | `.claude/skills/*/SKILL.md` → 镜像 `.qoder/skills/*/SKILL.md` | 目录级单向同步 |
| Hooks | `.claude/settings.json` | 提交前安全审查、写后快速审查、契约文件强制校验、路由提示注入 |
| 文档模板 | `docs/templates/`（+ `README.md` 映射表） | **写任何文档前先查映射表** |
| 记忆 | `docs/memory/` | 架构红线/ADR/推演、PM 记忆、排查记忆；只追加不改写 |
| 发现账本 | `docs/audit-reports/README.md` | open / fixed / waived 逐条跟踪 |
| MCP | `.mcp.json` | `context7`（框架文档）、`playwright`（GUI 验证）、`git`、`thinking` |

**改动纪律**：agent/skill 只在 `.claude/` 下修改，然后跑 `make assets-sync`。**禁止直接编辑 `.qoder/agents/` 与 `.qoder/skills/`**（会被下次同步覆盖）。`.qoder/repowiki/` 是机器生成物，不手工修改。

## SDD 流程

见 [`.claude/rules/sdd.md`](.claude/rules/sdd.md) 与 [`docs/sdd-workflow.md`](docs/sdd-workflow.md)：**无 spec 不编码**。每个能力一个目录 `specs/<feature>/`，含 `spec.md`（六要素）+ `design.md`（技术设计，跨栈须含**契约冻结点**），可选 `test-design.md`。**禁止**在 `specs/` 根下平铺 `*.spec.md`，**不存在** `changes/` 中间态目录。

## 硬性禁区

- ❌ 覆盖 `tasks/<task>/ai_labels/` 或原始标签（修正只写 `candidate_labels/`）
- ❌ 用水平矩形 `x,y,w,h` 表达有向框；坐标必须 4 点且 ∈ `[0,1]`，归一化只经 `obb_utils`
- ❌ 跨任务混用规则，或把某任务的类别 ID / 阈值硬编码进 `src/` 或 `prompts/`
- ❌ 在 Python 源码里内联 system prompt
- ❌ 在 `gui/src/` 里做几何计算、坐标归一化或文件读写
- ❌ `yaml.load`（必须 `yaml.safe_load`）；裸 `except:` 或 `except Exception: pass`
- ❌ 硬编码密钥（`VL_LLM_API_KEY` / `VL_VL_API_KEY` / `VL_MODEL_API_KEY` / `VL_ANCHOR_AUTH_TOKEN` 只走 env）
- ❌ 生产绑定 `0.0.0.0` 或通配 CORS；`detail` 回显绝对路径 / 堆栈 / 密钥
- ❌ `Any` 顶替真实类型；`assert` 做运行时校验；无 `encoding=` 的文本读写
- ❌ 提交带 ruff / mypy / `tsc --noEmit` 错误的代码
