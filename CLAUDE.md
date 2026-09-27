# CLAUDE.md — VL-YOLO-Anchor 代理指引

> **本文件是代理入口**：开发纪律、工具链索引、命令、架构锚点、文档地图、配置参考。
> **架构事实**（现在长什么样）在 [`docs/architecture/技术架构.md`](docs/architecture/技术架构.md) —— 本文件的架构小节只作索引，事实冲突时以该文档为准。
> **单一事实源**：`.claude/{agents,skills,rules,workflows}` 为规范源，`.qoder/{agents,skills}` 由脚本单向同步；详见「单一事实源与双平台镜像」。

---

## 一、开发纪律

### 1.1 工作流路由（leader 先行）

任何非平凡请求先由 `leader` 判定场景并路由到对应 workflow，同时判定**实施栈**：

| workflow | 适用场景 | 实施栈判定 |
|----------|---------|-----------|
| `.claude/workflows/feature-dev.md` | 新增能力 / 新端点 / 新页面 | 按 spec 影响面分派；**跨栈契约变更强制串行** |
| `.claude/workflows/bugfix.md` | 缺陷修复 | 定位 → 同类排查 → 最小修复 |
| `.claude/workflows/refactor-mechanical.md` | 机械式重构（不改变行为） | 触及升级红线即停止并升级为大流程 |
| `.claude/workflows/code-review.md` | 变更审查 | 按 reviewer 路由表分派维度 |
| `.claude/workflows/design-doc.md` | 产出 spec + design | `architect` 主笔 |
| `.claude/workflows/design-eval.md` | 多方案评估 | 方案**不足 3 个不得进入下一阶段** |
| `.claude/workflows/doc-tidy.md` | 文档整理与一致性 | `doc-writer` 主，两 engineer 协同 |
| `.claude/workflows/technical-research.md` | 外部技术调研 | 外部检索无结果**禁止编造**版本号/API 签名/性能数据 |

**审查与验证不得并发**：先冻结变更 → 审查 → 解冻 → 再实跑验证命令，并对被审文件做 **md5 哈希锚定**（审查期间文件被改动则结论失效并重审）。

### 1.2 不确定性处理

需求、边界、取值范围不明时，按 [`.claude/rules/ask-dont-assume.md`](.claude/rules/ask-dont-assume.md) **提问而非脑补**：给出 **≥3 个方案** + 各自的收益/风险 + **明确推荐**。

表达结论遵循 [`.claude/rules/pyramid-answer.md`](.claude/rules/pyramid-answer.md)（先结论后论证）与 [`.claude/rules/plain-summary.md`](.claude/rules/plain-summary.md)（通俗表达，不堆术语）。

### 1.3 规范驱动开发（SDD）

依据 [`.claude/rules/sdd.md`](.claude/rules/sdd.md)。**无 spec 不编码**；五阶段：

| 阶段 | 操作 | 产出 |
|------|------|------|
| 1 Spec | 定义六要素（业务背景 / 功能范围 / 接口契约 / 磁盘产物与 YAML schema / 硬约束影响 / 边界条件） | `specs/<name>/spec.md` |
| 2 Design | 技术设计 + 决策权衡 + **契约冻结点** | `specs/<name>/design.md` |
| 3 Implement | 按栈分派实施，同批改齐契约 | 代码 + 测试 |
| 4 Verify | `make lint type test gui-typecheck` + 维度审查 | 审查结论（🔴 则落审计报告） |
| 5 Archive | 更新架构基线与记忆 | `docs/architecture/`、`docs/memory/` |

**触发 SDD**：新增 API 端点 / 改 OBB 归一化或 IoU 逻辑 / 改 `prompts/*.yaml` 提示词契约 / 改 `config/*.yaml` schema / 改 `tasks/<task>/` 磁盘契约 / 新增依赖 / 重构 >2 文件。
**可跳过**：单文件 <20 行 bugfix、注释与文档、纯配置值调整、测试代码。

### 1.4 OBB 硬约束（永不违反）

依据 [`.claude/rules/obb-hard-constraints.md`](.claude/rules/obb-hard-constraints.md) 的 7 个小节。违反任一条的审查发现一律定级 **🔴 阻断**：

1. **OBB 输出格式** —— 4 个顺时针归一化点 `[x1..y4] ∈ [0,1]`；**禁止**水平矩形；归一化/互转/`obb_iou` 只经 `src/utils/obb_utils.py`
2. **标签不可覆盖** —— 修正只写 `tasks/<task>/candidate_labels/`；`ai_labels/` 只读
3. **任务规则隔离** —— 类别映射/标注规则/质检阈值一律从当前 task 配置读，禁跨任务混用与硬编码类别 ID
4. **提示词外置** —— 全部走 `prompts/*.yaml` 两段式 + `{}` 占位符，禁硬编码；占位符集合变更属契约变更
5. **前后端职责边界** —— Python 承担全部 CV/推理/文件 IO；前端只渲染 canvas + UI；栈锁 Tauri+React+TS+AntD；只经 `gui/src/services/api.ts` 访问后端
6. **资源目标** —— VRAM 预算 RTX 3060Ti 8GB + 4-bit + **必须支持 CPU 回退**；远程 API 为主，镜像不内置权重
7. **Python 工程约束** —— 3.11+ / `from __future__ import annotations` / 全类型注解禁裸 `Any` / Google-style docstring / ruff+mypy 零错 / uv+pyproject / 全相对路径

### 1.5 项目 Rules 索引

| 规则文件 | 约束 | 门禁归属 |
|---------|------|---------|
| [`.claude/rules/ask-dont-assume.md`](.claude/rules/ask-dont-assume.md) | 不确定时提问，≥3 方案 + 收益风险 + 推荐 | 语义审查 |
| [`.claude/rules/plain-summary.md`](.claude/rules/plain-summary.md) | 结论用通俗语言表达 | 语义审查 |
| [`.claude/rules/pyramid-answer.md`](.claude/rules/pyramid-answer.md) | 先结论、后论证 | 语义审查 |
| [`.claude/rules/sdd.md`](.claude/rules/sdd.md) | 无 spec 不编码；五阶段与目录约定 | `scripts/check_assets.py` 第 7 项 |
| [`.claude/rules/assertion-integrity.md`](.claude/rules/assertion-integrity.md) | 断言必须有效（改错实现会变红） | 语义审查 + `make test` |
| [`.claude/rules/api-response-contract.md`](.claude/rules/api-response-contract.md) | 端点必须声明 `response_model`，错误统一 `HTTPException` + `detail` | 语义审查 + `make type` |
| [`.claude/rules/obb-hard-constraints.md`](.claude/rules/obb-hard-constraints.md) | 7 小节永不违反的硬约束 | 语义审查（多 skill 必查） |
| [`.claude/rules/frontend-backend-contract.md`](.claude/rules/frontend-backend-contract.md) | 契约变更必须同批改齐三个契约文件 | `api-contract-review` + PostToolUse hook |

---

## 二、开发工具链

### 2.1 Agents（9 个，`.claude/agents/`）

| Agent | 职责 | 何时使用 |
|-------|------|---------|
| `leader` | 场景路由、实施栈分派、串行次序编排、升级判定 | 任何非平凡请求的第一步 |
| `pm` | 需求评审、文档一致性、SDD 合规、记忆维护 | 需求确认与归档阶段 |
| `architect` | 方案设计、ADR、推演留痕、架构裁决 | design 阶段与技术选型 |
| `backend-engineer` | `src/**`、`run.py`、`config/**`、`prompts/**`、`tests/**` 实施 | 后端变更 |
| `frontend-engineer` | `gui/src/**`、`gui/src-tauri/**` 实施 | 前端与桌面壳变更 |
| `unit-tester` | pytest 用例设计与补全（`tmp_path` + `monkeypatch` + TestClient） | 新增行为或修 bug |
| `reviewer` | 分级审查（按路由表分派 skill），🔴 阻断合入 | 变更完成后、提交前 |
| `doc-writer` | 文档撰写与注释（docstring / TSDoc） | 文档与注释阶段 |
| `auto-committer` | 提交前跑 `make lint type test` + `check_assets.py`，生成提交信息 | 提交阶段 |

### 2.2 Hooks（`.claude/settings.json`）

| 时机 | 触发条件 | 动作 | 超时 |
|------|---------|------|------|
| PreToolUse | `Bash` 且命令为 `git commit*` / `git push*` | agent hook：`security-review` 单级语义深审 diff（7 维度） | 120s |
| PostToolUse | 写入/编辑 `**/*.py` | agent hook：`python-code-review` 快速审查变更片段 | 60s |
| PostToolUse | 写入/编辑 `gui/src/**/*.ts*` | agent hook：`react-ts-review` 快速审查 | 60s |
| PostToolUse | 写入/编辑三个契约文件任一 | agent hook：`api-contract-review` **四条契约面**强制校验 | 60s |
| UserPromptSubmit | 每次 | command hook：注入工作流路由与实施栈判定提示 | 10s |

**收敛决策**：① 已**删除**本地模型机械预审环节（hooks 与 `auto-committer` 均为单级语义深审）；② 已**删除** SQL 迁移相关 hook（本项目无数据库）；③ 契约 hook 同时匹配 `Write` 与 `Edit` —— 只匹配 `Write` 会因日常改文件多用 `Edit` 而几乎不触发。

### 2.3 Skills（19 个，`.claude/skills/`）

| Skill | 用途 |
|-------|------|
| `/python-code-review` | Python 编码约束 + 10 维度审查（分层/命名/类型/异步/异常/路径/资源/ruff 坏味道/可测试性/日志） |
| `/react-ts-review` | React+TS 审查 11 项（strict/分层/hooks/状态/AntD/单一出口/类型对齐/canvas 性能/key/Tauri v1/tsc） |
| `/api-contract-review` | **四条契约面**三方一致性：面 A 端点路由 / 面 B 响应结构 / 面 C 请求结构 / 面 D 提示词占位符 |
| `/security-review` | 9 维度安全审查（路径穿越/任意写入/命令注入/密钥/CORS 绑定/prompt 注入/反序列化/容器/依赖投毒） |
| `/architecture-review` | 现状架构审查：分层依赖、配置注入、选型合规 |
| `/architecture-principles` | 架构硬性约束与举例基线 |
| `/architecture-reasoning` | 设计**之前**的前瞻推演，落 `docs/memory/architect-reasoning.md` |
| `/architect-memory` | 架构记忆维护（红线/选型/待办/教训 + ADR） |
| `/pm-memory` | PM 记忆维护（需求上下文/一致性/SDD/台账/影响/债务/偏好） |
| `/bug-investigation-memory` | 根因 + **同类排查（同模块/同模式/同类型）** 记忆生成 |
| `/docs-consistency-review` | 文档 ↔ 代码一致性 + SDD 三段合规 |
| `/doc-comment` | Python Google-style docstring + TS TSDoc 规范 |
| `/doc-template` | 写文档前先查 `docs/templates/` 匹配模板；维护类型→模板映射表 |
| `/audit-report` | 审查发现落盘为八章审计报告 + 账本登记 |
| `/code-indexer` | 代码检索：`Grep`/`Glob` + LSP 优先，两条检索链路（需求链 / UI 链） |
| `/test-design` | pytest 用例设计与**断言有效性**判据 |
| `/api-smoke-test` | 起服务打端点链路的冒烟验证 |
| `/ui-impact-review` | UI 影响面分析（**只建议不改代码**），OBBCanvas 与四标签页 |
| `/review` | `/review` 入口：调用 `reviewer` 按路由表执行全维度审查 |

### 2.4 单一事实源与双平台镜像

```
.claude/  ──(scripts/sync_agent_assets.py 单向)──▶  .qoder/
  agents/*.md   ── 文件级 ──▶  agents/*.md
  skills/*/SKILL.md ── 目录级 ──▶  skills/*/SKILL.md
  rules/*.md    ┐
  workflows/*.md┘  只保留 .claude/ 单份（Qoder 侧经本文件与 AGENTS.md 的路径引用读取）
```

| 命令 | 作用 |
|------|------|
| `make assets-sync` | 生成/更新 `.qoder` 镜像（SHA-256 比对，仅差异才复制，清理镜像孤儿） |
| `make assets-check` | 7 项一致性门禁（含**旧栈术语残留门禁**与 **SDD 结构门禁**） |
| `make hooks-install` | 启用仓库 hooks：`git config core.hooksPath .githooks` |

**规范源优先级**：skill/rule 正文 > 记忆与模板文件 > 本文件索引。改动只改 `.claude/` 再 `make assets-sync`，**禁止**直接改 `.qoder/`。

### 2.5 前后端契约纪律

依据 [`.claude/rules/frontend-backend-contract.md`](.claude/rules/frontend-backend-contract.md)：

- 契约变更必须在**同一批提交**内改齐 `src/api_server.py` + `gui/src/types/index.ts` + `gui/src/services/api.ts`
- **Pydantic 模型是唯一事实源**，TS 类型向它对齐，不反过来
- 后端先行而前端无法同批对齐时，前端必须留 `// TODO(contract)`，`reviewer` 判 **🔴 阻断**
- 禁止前端自行猜测字段名
- `api-contract-review` 是**强制门禁**（PostToolUse hook 自动触发）
- 跨栈实施**串行**：`backend-engineer` 定契约 → 确立「契约冻结点」→ `frontend-engineer` 对齐

---

## 三、构建与测试

| 命令 | 作用 |
|------|------|
| `make help` | 列出全部 target |
| `make lint` | `uv run ruff check src/ run.py tests/ scripts/` |
| `make type` | `uv run mypy src/ scripts/`（`strict=true`） |
| `make test` | `uv run pytest -q` |
| `make gui-install` | `cd gui && npm install` |
| `make gui-typecheck` | `cd gui && npx tsc --noEmit` |
| `make gui-build` | `cd gui && npm run build`（= `tsc && vite build`） |
| `make assets-sync` / `make assets-check` | 镜像同步 / 资产门禁 |
| `make hooks-install` | 启用 `.githooks` |
| `make docker-build` / `make up` / `make down` / `make logs` / `make ps` | compose 生命周期 |
| `make clean` | 清理缓存与本地 GUI 构建产物 |

**单测过滤**：`uv run pytest tests/test_api_server.py -q`（单文件，须 ≤10s）、`uv run pytest -k "expr"`（表达式筛选）。

**测试前置**：文件隔离一律用 pytest `tmp_path`；**跨平台文件锁不可靠**（Linux/macOS 无强制锁，真实独占写法在 CI 上永远通过）⇒ IO 失败一律用 `monkeypatch.setattr(Path, "read_text", _boom)` 注入；HTTP 用 `fastapi.testclient.TestClient`。

**提交门禁**（`.githooks/pre-commit`，需先 `make hooks-install`）：① `sync_agent_assets.py --auto-stage` ② `check_assets.py` ③ 暂存含 `.py`/`pyproject.toml`/`uv.lock` 时跑 lint+type+test ④ 暂存含 `gui/` 时跑 gui-typecheck。逃生阀：`SKIP_PY_CHECKS=1` / `SKIP_GUI_CHECKS=1`。

本机无 `make` 时，直接跑上表对应的裸命令（`uv run ruff check …` 等）。

---

## 四、架构参考

### 4.1 目录结构

| 路径 | 内容 |
|------|------|
| `src/utils/` | `obb_utils`（硬约束唯一实现）、`image_utils`、`file_utils`、`yaml_utils` |
| `src/agents/` | `base_agent`、`plan_agent`、`annotate_agent`、`inspect_agent`、`model_client` |
| `src/core/` | `pipeline`（plan→annotate→inspect→split）、`task_manager` |
| `src/api_server.py` | FastAPI 入口、Pydantic 模型集中区、14 端点、CORS 与鉴权中间件 |
| `run.py` | CLI 入口 |
| `src/config.py` | 三层配置加载（env > `config/global.yaml` > 默认） |
| `gui/src/` | `components`、`pages`、`hooks`、`services`、`types` |
| `gui/src-tauri/` | Tauri v1 壳（`src/main.rs` 仅 8 行） |
| `prompts/` | 三个 agent 提示词 YAML + `prompt_versions/` 归档 |
| `config/` | `global.yaml`、`task_template.yaml` |
| `tasks/<task>/` | 任务磁盘产物（唯一持久层） |
| `tests/` | pytest 套件，基线 **99 passed** |

**分层依赖方向**（禁止反向）：`src/utils` ← `src/agents` ← `src/core` ← `src/api_server.py` / `run.py`。

### 4.2 `tasks/<task>/` 磁盘契约

| 产物 | 可写性 |
|------|--------|
| `images/` | 用户投放 |
| `task.yaml`、`plan.yaml`、`inspection_report.yaml` | 系统写入 |
| `ai_labels/` | `AnnotateAgent` 产出，**只读，禁止覆盖** |
| `candidate_labels/` | 人工修正与质检建议的**唯一**写入位置 |
| `dataset/{train,val,test}/`、`data.yaml`、`train_command.txt`、`export_summary.yaml` | split 步骤产出 |

标签读取优先级：`candidate_labels` > `ai_labels`。

### 4.3 双入口与双模型

- CLI（`run.py`）与 HTTP（`src/api_server.py`）**共享同一 `Pipeline`** ⇒ 改流程必须同步两侧测试
- **LLM 与 VL 双模型配置**（ADR-001）：共享基座 + 角色覆盖，角色未覆盖时自动继承基座；详见 `docs/memory/architect-decisions.md`

完整事实基线：[`docs/architecture/技术架构.md`](docs/architecture/技术架构.md)。

---

## 五、文档地图

| 位置 | 写时机 | 读时机 |
|------|--------|--------|
| `specs/<feature>/spec.md` + `design.md` | 每个新能力（SDD 阶段 1/2） | 实施前、审查时 |
| `docs/architecture/技术架构.md` | 架构变更后 | **一致性比对的锚点** |
| `docs/memory/architect-memory.md` | 新红线/选型/待办/教训 | 设计前、审查前 |
| `docs/memory/architect-decisions.md` | 每个架构决策（ADR，只追加） | 选型争议、复审时 |
| `docs/memory/architect-reasoning.md`（+ `-archive.md`） | 设计**前**的推演留痕 | 新需求设计前必看 |
| `docs/memory/pm-memory.md`（+ `-archive.md`） | 需求结论、SDD 合规、技术债、用户偏好 | 实施前了解上下文 |
| `docs/memory/bug-investigation-memory.md` | bugfix 阶段 2.5 根因 + 同类排查 | 遇到同类信号时 |
| `docs/templates/` | 新增文档类型时 | **写任何文档前先查映射表** |
| `docs/audit-reports/` | 🔴 阻断发现、显式审计 | 跟踪发现状态（open/fixed/waived） |
| `docs/archive/` | 文档废弃时（**不直接删除**） | 追溯历史决策时 |
| `docs/sdd-workflow.md` | SDD 流程变更时 | 不清楚流程五阶段时 |

**大文件读取纪律**：`docs/memory/`、`docs/audit-reports/`、`.qoder/repowiki/` 一律 `Grep` 定位后按行区间 `Read`，**禁止全文读取**。

---

## 六、配置参考

### 6.1 `config/global.yaml`

模型（`model` + `model.llm` + `model.vl`）/ 阈值 / 路径 / 服务器 / 日志 五段。

### 6.2 `config/task_template.yaml`

类别映射 / 训练超参 / 数据集划分 / 质检规则 / 评估指标 五段，`TaskManager` 用它播种每个新任务。

### 6.3 `prompts/*.yaml`

统一 `system_prompt` + `user_prompt_template` 两段式，变量用 `{}` 占位符；重大变更按 `prompts/prompt_versions/<agent>_v<n>.yaml` 快照归档。**占位符集合与渲染方 `.format()` 入参必须双向一致**（缺失 → `KeyError` 崩溃；冗余 → 静默死参数）。

### 6.4 敏感密钥（必须 env 注入，禁止硬编码/入 yaml/入日志）

| 环境变量 | 用途 |
|---------|------|
| `VL_LLM_API_KEY` | LLM 角色密钥 |
| `VL_VL_API_KEY` | VL 角色密钥 |
| `VL_MODEL_API_KEY` | 基座密钥（未设角色覆盖时两者共用） |
| `VL_ANCHOR_AUTH_TOKEN` | 可选 bearer 鉴权令牌 |
| `VL_MODEL_*` / `VL_ANCHOR_*` 其余键 | 见 `docs/architecture/技术架构.md` 第五节配置表 |

参考 `.env.example`；`docker-compose.yml` 的 `env_file: .env` 为**可选**（未复制也能起服务）。

### 6.5 启动 fail-fast 校验

`src/config.py` 的 `load_settings()` 在启动/首次加载时校验配置；配置缺失或非法应**立即抛错**，不延迟到请求期。`get_settings()` 带 `@lru_cache(maxsize=1)`，测试需绕缓存时直接调 `load_settings()`。

---

## 附：历史归档

一期脚手架的一次性生成任务提示词已移至 [`docs/archive/build-task-scaffold.md`](docs/archive/build-task-scaffold.md)（含"哪些描述已被实况取代"的对照表）。其中的 10 条 never-violate 约束已提升为正式规则 `.claude/rules/obb-hard-constraints.md`，该归档文件**不再作为约束源**。
