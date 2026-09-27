# 产品经理记忆

> **定位**：VL-YOLO-Anchor 的需求上下文、文档一致性结论、SDD 合规记录、Spec 台账、变更影响、遗留技术债与**用户明确表达过的偏好**。
> **维护者**：`pm` agent；规范源 = [`.claude/skills/pm-memory/SKILL.md`](../../.claude/skills/pm-memory/SKILL.md)（结构以该 skill 的「记忆文档结构」为准）。
> **归档**：spec 归档后其四件套（需求上下文/基线/影响/合规）迁往 [`pm-memory-archive.md`](pm-memory-archive.md)。
> **读取方式**：优先 `Grep(regex='^## |^### ')` 定位节号后按行区间 `Read`，**禁止全文读取**（本文件会持续增长）。
> **写入纪律**：只记影响后续决策的信息；用户偏好**只记明确表达过的**，不推断；一次性执行细节不记。

## 需求上下文

### agent 资产迁移与双栈规范落地（2026-09-27）

- **背景**：项目从单栈 Python CLI 扩展为 FastAPI 后端 + Tauri/React 前端双栈后，编码规范、审查维度、门禁命令在两栈间无重叠；需要把外部项目已验证的 agent/skill/rule/workflow 资产体系引入本项目，并按本栈实况重写。
- **范围**：**包含** `.claude/**`（9 agents / 19 skills / 8 rules / 8 workflows / settings.json hooks）、`.qoder/` 镜像、`scripts/` 门禁脚本、`docs/memory|templates|audit-reports|architecture|archive`、`specs/` 目录化改造、`CLAUDE.md` + `AGENTS.md`、Makefile/ci.yml/pre-commit/.claudeignore/.mcp.json/.gitattributes。**不包含** `src/**` 与 `gui/**` 任何业务源码。
- **实施栈**：跨栈（配置与文档层），不改业务代码
- **用户偏好/约束**：见下方「用户偏好」的 4 项已确认决策

### 平台核心流水线

- **背景**：面向缺陷检测场景，用 VL 模型自动生成训练计划、批量标注 OBB 框、质检、导出可训练数据集，降低 YOLO-OBB 从 0 到可训练数据集的门槛
- **范围**：包含 `plan → annotate → inspect → split` 四步编排、任务目录磁盘契约、CLI 与 HTTP 双入口共享同一 pipeline；不包含模型训练本身（只产出 `train_command.txt` 与 `data.yaml`，训练由用户执行）
- **实施栈**：后端
- **用户偏好/约束**：显存预算锁 RTX 3060Ti 8GB；Python 侧做全部 CV/推理/文件 IO

### 逐图标签读写（per-image-label-api）

- **背景**：标注审核页需要按图片读取/修正 OBB 框，人工修正不得污染 AI 原始产出
- **范围**：包含标签读取端点、`candidate_labels/` 写入优先、BOM/非 UTF-8/越界坐标/非法类别的容错解析；**不包含**覆盖写 `ai_labels/`（硬约束）
- **实施栈**：跨栈
- **用户偏好/约束**：标签来源优先级固定 `candidate_labels` > `ai_labels`

### Tauri 桌面壳与启动脚本

- **范围**：`gui/src-tauri/` 壳（仅 `main.rs`）+ `gui-launch-scripts` 的端口/健康检查/退出码传播；**技术栈锁定 Tauri + React + TS + AntD，禁止改投 PyQt/Electron**
- **实施栈**：前端

### Docker 部署

- **范围**：后端 CPU 镜像 + gui 镜像（Vite build + nginx `/api` 反代）+ compose；模型侧走远程 OpenAI 兼容端点，镜像内不含权重
- **实施栈**：后端 + 运维

### 测试套件

- **范围**：pytest 覆盖 `src.utils.*` / `src.core.pipeline` / `src.api_server`，`tmp_path` 隔离文件系统；基线 **99 passed**、覆盖率 88%→98%
- **用户偏好/约束**：单文件用例 ≤10s；跨平台 IO 失败用 monkeypatch 注入而非真实文件锁

## 文档一致性历史

- 2026-09-27：`CLAUDE.md` 原 174 行是一次性 Build Task 脚手架提示词，**不是**代理指引 → 处理：整体归档 `docs/archive/build-task-scaffold.md`，`CLAUDE.md` 按纪律/工具链/构建/架构/文档地图/配置六节重写
- 2026-09-27：3 个 workflow（design-doc / design-eval / technical-research）把 ADR 落点误写为 `architect-memory.md` → 处理：修复为 `architect-decisions.md`（ADR 全量台账与红线/选型拆分见 `architect-memory` skill Step 3）
- 2026-09-27：`technical-research.md` 引用了**不存在的 `deep-research` skill** → 处理：改为 `code-indexer` 检索链路 + WebSearch/`context7` MCP；并把该 skill 名加入 `check_assets.py` 残留门禁，防止再次引入
- 2026-09-27：`docs/template/` 与 `docs/templates/` 双目录并存是上游历史包袱 → 处理：**本项目只保留 `docs/templates/` 单目录**，上游 `template-Bug排查记忆` 合并入内
- 2026-09-27：`doc-template` skill 的映射表要求 6 类模板 + README，多于迁移方案正文的「5 个」→ 处理：以 **skill 契约为准**（skill 是规范源，且其 Step 4 要求 README 与 skill 两处映射表一致）
- 2026-09-27：`settings.json` 的契约 hook 初稿写了 5 条契约面，`api-contract-review` skill 实际只有 4 条 → 处理：以 skill 术语逐字对齐，磁盘产物 schema 变更明确移交 `docs-consistency-review`
- 2026-09-27：**UI 文案语言不一致** —— `gui/src/App.tsx:11,15-18` 为中文（标题 + 四个 Tab 标签），四个 Page 组件全英文 → 处理：规则定为「与所在文件既有语言保持一致」，登记为技术债，等一次统一决策再批量改

## SDD 合规记录

- 2026-09-27：agent 资产迁移（约 70 个手写文件 + 28 个生成镜像文件）→ **合规**：迁移前先出方案并经两轮确认（4 项决策），实施按 8 步顺序分批
- 2026-09-27：SDD 体系由旧的 `opsx` 系列命令 + `changes/` 中间态迁为 `specs/<feature>/spec.md + design.md` → **合规但属破坏性变更**，已在 `CHANGELOG.md` 记录；旧 `specs/*.spec.md` 平铺结构与 `changes/` 目录是**迁移前的既有违规**，本次一并消除。注：本节故意不写命令形式的斜杠前缀，以免被 `check_assets.py` 第 7 项「旧 SDD 命令残留」门禁误判
- 2026-09-27：本次**未触碰** `src/**` 与 `gui/**` 业务代码 → 无需 spec 驱动（纯配置资产 + 文档 + 脚本），但门禁新增第 7 项「SDD 结构门禁」把该约定固化为 `check_assets.py` 的强制校验

## Spec 台账

| Spec | 状态 | 关联变更 | 归档日期 |
|------|------|---------|---------|
| `specs/platform-core/` | 进行中 | 一期主干（plan/annotate/inspect/split） | — |
| `specs/per-image-label-api/` | 进行中 | 逐图标签读写 | — |
| `specs/tauri-shell/` | 进行中 | Tauri v1 桌面壳 | — |
| `specs/gui-launch-scripts/` | 进行中 | 启动脚本与端口/健康检查 | — |
| `specs/deployment/` | 进行中 | Docker 部署；吸收原 `2026-09-27-docker-deployment` 提案 | — |
| `specs/test-suite/` | 进行中 | pytest 套件与覆盖率基线 | — |
| `specs/audit-fixes/` | **已归档** | 2026-09-26 审计修复（H/M/L 系列，已实施完成） | 2026-09-27 |

> `specs/README.md` 是目录约定说明，不属任何 feature，不入台账。

## 变更影响记录

- 2026-09-27：SDD 结构改造 → 影响 **文档路径约定**（`specs/` 目录化）、`docs/sdd-workflow.md`（五阶段表重写，移除全部旧命令引用）、`CHANGELOG.md`（破坏性变更条目）、`check_assets.py` 第 7 项门禁
- 2026-09-27：实施 agent 一拆为二 → 影响 **agent 编排契约**：`leader` 分派表、`feature-dev` 阶段 3a/3b、`bugfix`/`refactor-mechanical`/`doc-tidy` 实施者、`reviewer` 路由表；详见 ADR-002
- 2026-09-27：新增 `frontend-backend-contract.md` rule + `api-contract-review` skill + PostToolUse hook → 影响 **API 契约纪律**：`src/api_server.py` / `gui/src/types/index.ts` / `gui/src/services/api.ts` 必须同批改齐，否则 🔴 阻断
- 2026-09-27：删除本地推理层（`ollama-call` / `prompt-optimizer`）→ 影响 **审查执行方式**：hooks 与 auto-committer 改为单级语义深审，不再有机械预审
- 2026-09-27：新增 `.gitattributes`（迁移方案未列，但**必需**）→ 影响 **构建/门禁**：`core.autocrlf=true` 且无属性文件时，pre-commit 的 shebang 会被解析成 `bash\r` 而直接失效；同时镜像比对必须归一化行尾，否则 Windows 上永久判为「漂移」
- 2026-09-27：`make lint` / `make type` / ci 的检查面扩展到 `scripts/` → 影响 **门禁命令范围**（两个新脚本须过 ruff + `mypy --strict`）

## 遗留待办与技术债

> 🔴 必须修 / 🟡 建议修 / 🔵 可选。全部为**既有事实**，非本次迁移引入；均需同步登记 `docs/audit-reports/README.md` 发现账本。

### 契约与后端架构

- [ ] 🔴 **A1 `agent.config` 并发竞态**：`src/api_server.py:41-46` 模块级 `_pipeline` 单例 + `src/core/pipeline.py:154/:176/:191` 对 `plan_agent.config`/`annotate_agent.config`/`inspect_agent.config` **就地重赋值**（注释写着 per-task rule isolation）+ 14 个端点全为同步 `def`（跑在 anyio 线程池，可并发）+ 零锁 ⇒ 两个任务同时跑步骤时后写覆盖前写，**任务规则隔离硬约束失效**。`specs/platform-core/design.md` 需给出结论 → 登记于账本
- [ ] 🟡 **A4 两端点返回裸 `dict`**：`api_server.py:411 get_plan -> dict[str, Any]`、`:429 get_export_summary -> dict[str, Any]`；`.claude/rules/api-response-contract.md` 已列为「现存豁免，不得仿写」，前端对应 `res.data as TaskPlan` / `as ExportSummary` 断言 → 触及即补齐 Pydantic 模型
- [ ] 🟡 **A5 `prompts/inspect_agent.yaml` 是无消费方的孤儿契约**：该文件定义了完整 `system_prompt` + `{iou_threshold}`/`{allowed_classes}`/`{size_range}` 占位符，但 `src/agents/inspect_agent.py` 全文**不含 "prompt" 字样**、从不调用 `load_prompt`；`pipeline.py:86 InspectAgent({}, prompts_dir)` 传了目录却没人读 ⇒ 质检步骤纯规则计算，无 LLM 参与；`api-contract-review` 的「面 D 占位符双向一致」会静默漏掉它
- [ ] 🔵 **A6 提示词死参数**：`annotate_agent.py:116` 传 `image_name=image_path.name`，但 `prompts/annotate_agent.yaml` 的占位符只有 `annotation_rules/class_mapping/exclude_items/h/min_pixel/w`（**无 `image_name`**）；`.format()` 允许多余关键字 ⇒ 不报错、静默无效
- [ ] 🔵 **A7 dead surface（前端）**：`gui/src/services/api.ts` 导出 `getPlan`（`:410` 端点 + 封装均零消费）、`runStep`、`API_BASE` —— 后两者只被同文件内部使用（`:43-46` 的四个包装、`:17`/`:68`），无需导出

### 启动与运维

- [ ] 🔴 **L1 鉴权开启后启动脚本必失败**：`start_gui.sh:46` / `start_gui.ps1:46` 的探活请求打 `GET /api/tasks`，而 `src/api_server.py:53` 的 `_PUBLIC_PATHS` 只含 `"/api/health"`，判断在 `:70` ⇒ 一旦设置 `VL_ANCHOR_AUTH_TOKEN`，探活返回 **401** → `curl -fs` 失败 → 脚本误判「后端未就绪」并非零退出。默认 `auth_token=""`（鉴权关闭）故本地开发一直未触发。**修复方向：探活改打 `/api/health`**（该端点本就为此而生且免鉴权）；**不要**把 `/api/tasks` 参加公开集合（会让任务枚举绕过鉴权）。登记于账本 L1

### 安全

- [ ] 🔴 **S1 二阶命令注入链**：`api_server.py:89 description: str = Field("", ...)` **无 `max_length`/`pattern`** → `prompts/plan_agent.yaml:24 {task_description}` 原样插入 → LLM 输出 plan → `pipeline.py` f-string 渲染 `dataset/train_command.txt` → **用户在 shell 中执行**。同名任务字段（`:82-88`）已有 `pattern`+`max_length=64`，description 没有
- [ ] 🟡 **S2 prompt 注入无输入约束**：同 `S1` 前半段，用户 task description 直接进 LLM 提示词，无长度/内容约束
- [ ] 🟡 **S3 CORS 未校验通配**：`src/config.py:174-177` 读 `VL_ANCHOR_CORS_ORIGINS` 逗号切分后直接使用，不拒绝 `*`
- [ ] 🟡 **S4 nginx 端口绑全网卡**：`docker-compose.yml:43` `"8080:80"` 无 `127.0.0.1:` 前缀
- [ ] 🔵 **S5 日志可能含提示词全文**：远程 client 记录请求体时未脱敏，提示词里已插入用户 description 原文

### 错误语义与跨栈提示

- [ ] 🟡 **IO 异常冒泡成 500**（2 处需修）：`src/agents/inspect_agent.py:188` 严格 `utf-8` 且无 `except OSError`；`src/agents/model_client.py:158 read_bytes()` 无保护 —— 均经 `api_server.py:342-346` 的 `except Exception` 变成 `500 + detail=str(exc)`。详见 [`bug-investigation-memory.md`](bug-investigation-memory.md) 排查记录 #1
- [ ] 🟡 **F2 后端 `detail` 到不了用户**：`gui/src/services/api.ts` **无 `interceptors`、全文不含 `detail`**；6 处 `catch` 统一读 `(err as Error).message`（`TaskPlanPage.tsx:34/:51`、`AnnotationReviewPage.tsx:30/:97`、`InspectionReportPage.tsx:53`、`TrainingExportPage.tsx:28`）⇒ axios 只会给出「Request failed with status code 422」。**这直接抵消了 H4 的修复**：后端精心把文件名写进 `detail`，前端从不显示
- [ ] 🔵 **H-3 内部异常文本透传**：`api_server.py:346 detail=str(exc)` 把内部异常原文回显给 GUI，与 `api-response-contract.md`「`detail` 不回显绝对路径/堆栈」相冲
- [ ] 🔵 **H-4（疑似，需人工确认）**：`src/utils/image_utils.py:64-67 validate_image` 只捕 `(FileNotFoundError, ValueError)`；`load_image:30` 的 `np.fromfile` 在文件被独占时抛 `PermissionError`/`OSError`，不在列表内。**未实测独占场景异常类型，勿凭推测扩大捕获面**

### 前端体验

- [ ] 🔴 **F1 假上传**：`gui/src/pages/TaskPlanPage.tsx:89` `<Upload.Dragger multiple beforeUpload={() => false} maxCount={50}>` —— 既不上传也不报错，文案还写着「backend import via CLI/API」，用户会以为图已进系统
- [ ] 🟡 **F3 破坏性操作无二次确认**：`runAnnotate` / `runInspect` / `runSplit` 直接触发（`AnnotationReviewPage.tsx:96`、`InspectionReportPage.tsx:52`、`TrainingExportPage.tsx:27`），其中 split 会重写 `dataset/`
- [ ] 🔵 **F4 UI 文案语言不一致**：`App.tsx:11,15-18` 中文 vs 四个 Page 英文（见「文档一致性历史」）

### 配置与依赖

- [ ] 🔵 **D1 6 个已声明未使用依赖**：Python 侧 `jinja2`、`matplotlib`、`pillow`、`pydantic-settings`（`src/**` + `run.py` 零引用）；前端 `@ant-design/charts`、`@tauri-apps/api`（`gui/src/**` 零引用）。删除前需确认无运行期动态引用
- [ ] 🔵 **D2 三字段无 env 覆盖**：`config.py:120-152 _overlay_model` 只对 `provider`/`base_url`/`api_key`/`model` 做 env 覆盖，`timeout_s`/`max_retries`/`max_new_tokens` 只能从 `global.yaml` 读且 **LLM 与 VL 共享同一份** ⇒ 容器化部署无法按角色调超时（ADR-001 已知限制）
- [ ] 🔵 **D3 ruff 实际不约束行长**：`pyproject.toml` 的 ruff 配置 `select` 含 `E`，但 **`ignore = ["E501"]`** ⇒ 「`line-length=120`」是形式声明、无强制力。写规则文档时不得声称行长被门禁拦截

### 测试

- [ ] 🟡 **T1 `pytest.raises` 缺 `match=`**：7 处只断言异常类型，改错异常消息不会红（`.claude/rules/assertion-integrity.md`）

## 用户偏好

> 仅记录用户**明确表达**的内容，不推断。

- 2026-09-27：**实施 agent 拆两个** —— 因本项目前后端分离，选择 `backend-engineer` + `frontend-engineer`，否掉单一实施 agent（源自上游单 agent 形态）
- 2026-09-27：**完整镜像迁移** —— 不裁剪，含 hooks、pre-commit、同步/校验脚本、`.claudeignore`、`docs/memory`、`docs/templates`、审计报告体系、`.mcp.json`
- 2026-09-27：**SDD 以源项目为主干** —— 采用 `specs/<feature>/spec.md + design.md`，废弃本项目原有的旧命令与 `changes/` 中间态（用户已确认接受破坏性变更）
- 2026-09-27：**删除本地推理层** —— 不保留本地模型预审，依赖它的资产一律改为单级语义深审
- 2026-09-27：**先评审后执行** —— 迁移类任务必须先出方案并评审通过再动手
- 2026-09-27：**本任务不触碰业务源码** —— 用户批准的方案明确「不修改 `src/` 与 `gui/` 任何源码」，审查发现的既有缺陷一律**登记不修**
