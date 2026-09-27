# 审计报告与发现账本

> **本目录两个职责**：① 存放 `reviewer` 审查发现的落盘报告；② 维护**发现账本**，让每条发现的状态可跟踪、重复问题可统计。
> **报告文件命名**：`{YYYY-MM-DD}-{scope}.md`，`scope` = 变更主题短名（kebab-case，中英文皆可但同一批保持一致），如 `annotate-agent-retry`、`obb-normalize-fix`、`gui-task-select`；同日多份追加序号 `-2`/`-3`。
> **模板**：[`../templates/template-审计报告.md`](../templates/template-审计报告.md)（八章结构，禁止自由增删顶层章节）
> **规范源**：[`.claude/skills/audit-report/SKILL.md`](../../.claude/skills/audit-report/SKILL.md)（本 README 的两张表列头必须与该 skill Step 4 逐字一致）

---

## 一、报告索引

**一行一报告**。每落盘一份审计报告追加一行。

| 日期 | scope | 报告文件 | 实施栈 | 🔴 | 🟡 | 🔵 | 整体状态 |
|------|-------|---------|--------|----|----|----|---------|
| — | — | （暂无落盘报告） | — | — | — | — | — |

## 二、发现账本

**一行一发现**（跨报告的逐条跟踪），不展开明细；明细留在各自报告文件的第四节。

- **状态三态**：`open` 已登记未修复 / `fixed` 已修复并经验证 / `waived` 人工批准豁免（**必须注明批准人与理由**）
- **无报告的发现如何登记**：来源列指向登记处（如 `pm-memory`），`fixed`/`waived` 前仍须回到实际证据复核
- **严重度**：🔴 P0 必须修 / 🟡 P1 建议修 / 🔵 P2 可选

| 编号 | 日期 | 来源 | 严重度 | 发现（一句话） | 位置 | 修复方 | 状态 | 关联 spec |
|------|------|------|--------|---------------|------|--------|------|----------|
| A1 | 2026-09-27 | pm-memory | 🔴 | 模块级 `_pipeline` 单例 + agent `.config` 就地重赋值 + 全同步端点并发，任务规则隔离硬约束失效 | `src/api_server.py:41-46`；`src/core/pipeline.py:154/:176/:191` | backend-engineer | open | `specs/platform-core/` |
| S1 | 2026-09-27 | pm-memory | 🔴 | description 无长度/内容约束 → 进提示词 → LLM 输出 → 渲染 `train_command.txt` → 用户执行（二阶命令注入） | `src/api_server.py:89` → `prompts/plan_agent.yaml:24` → `src/core/pipeline.py` split 段 | backend-engineer | open | `specs/platform-core/` |
| F1 | 2026-09-27 | pm-memory | 🔴 | `Upload.Dragger beforeUpload={() => false}` 假上传：不上传也不报错 | `gui/src/pages/TaskPlanPage.tsx:89` | frontend-engineer | open | `specs/tauri-shell/` |
| L1 | 2026-09-27 | gui-launch-scripts spec | 🔴 | 启动脚本探活打 `/api/tasks`，而鉴权公开路径只含 `/api/health` ⇒ 设置 `VL_ANCHOR_AUTH_TOKEN` 后探活返回 401，脚本误判「后端未就绪」并终止（默认未配 token 时不触发） | `start_gui.sh:46`、`start_gui.ps1:46`；判据 `src/api_server.py:53`/`:70` | backend-engineer（改探活为 `/api/health`） | open | `specs/gui-launch-scripts/` |
| A5 | 2026-09-27 | pm-memory | 🟡 | `prompts/inspect_agent.yaml` 是无消费方的孤儿契约，inspect 步骤无 LLM 参与 | `prompts/inspect_agent.yaml`；`src/agents/inspect_agent.py`（全文无 prompt 字样） | 需人工确认（是否启用 LLM 质检） | open | `specs/platform-core/` |
| F2 | 2026-09-27 | pm-memory | 🟡 | 前端只读 `err.message`，后端 `HTTPException` 的 `detail` 从不呈现（抵消 H4 的异常分类修复） | `gui/src/services/api.ts`（无 interceptor）；6 处 `catch` | frontend-engineer | open | `specs/per-image-label-api/` |
| IO-1 | 2026-09-27 | 排查记录 #1 | 🟡 | `read_text` 严格 utf-8 且无 `except OSError` → inspect 步骤 IO/解码失败冒泡成 500 | `src/agents/inspect_agent.py:188` | backend-engineer | open | `specs/platform-core/` |
| IO-2 | 2026-09-27 | 排查记录 #1 | 🟡 | `read_bytes()` 无保护 → annotate 步骤图片不可读冒泡成 500 | `src/agents/model_client.py:158` | backend-engineer | open | `specs/platform-core/` |
| S3 | 2026-09-27 | pm-memory | 🟡 | CORS 来源逗号切分后不校验，`*` 可通过 | `src/config.py:174-177` | backend-engineer | open | `specs/deployment/` |
| S4 | 2026-09-27 | pm-memory | 🟡 | gui 服务端口 `8080:80` 无 `127.0.0.1:` 前缀，绑全网卡 | `docker-compose.yml:43` | backend-engineer | open | `specs/deployment/` |
| F3 | 2026-09-27 | pm-memory | 🟡 | `runAnnotate`/`runInspect`/`runSplit` 无二次确认，split 会重写 `dataset/` | `AnnotationReviewPage.tsx:96`、`InspectionReportPage.tsx:52`、`TrainingExportPage.tsx:27` | frontend-engineer | open | `specs/tauri-shell/` |
| T1 | 2026-09-27 | pm-memory | 🟡 | 7 处 `pytest.raises` 缺 `match=`，异常消息改错不会红 | `tests/*.py` | unit-tester | open | `specs/test-suite/` |
| A4 | 2026-09-27 | api-response-contract 豁免表 | 🔵 | 两端点返回裸 `dict[str, Any]`，前端 `as` 断言 | `src/api_server.py:411/:429` | backend-engineer | open | `specs/per-image-label-api/` |
| A6 | 2026-09-27 | pm-memory | 🔵 | 提示词死参数：代码传 `image_name` 但模板无该占位符（`.format()` 静默忽略） | `src/agents/annotate_agent.py:116` | backend-engineer | open | `specs/platform-core/` |
| A7 | 2026-09-27 | pm-memory | 🔵 | 前端 dead surface：`getPlan` 导出零消费；`runStep`/`API_BASE` 无需导出 | `gui/src/services/api.ts` | frontend-engineer | open | `specs/per-image-label-api/` |
| H-3 | 2026-09-27 | 排查记录 #1 | 🔵 | `detail=str(exc)` 把内部异常原文透给 GUI | `src/api_server.py:346` | backend-engineer | open | `specs/platform-core/` |
| H-4 | 2026-09-27 | 排查记录 #1 | 🔵 | **疑似**：`validate_image` 捕获粒度不含 `PermissionError`/`OSError`（未实测独占场景异常类型） | `src/utils/image_utils.py:64-67` | 需人工确认 | open | `specs/platform-core/` |
| S5 | 2026-09-27 | pm-memory | 🔵 | 日志可能含提示词全文（提示词已插入用户 description） | 远程 client 请求体记录处 | backend-engineer | open | `specs/deployment/` |
| D1 | 2026-09-27 | pm-memory | 🔵 | 6 个已声明未使用依赖（Python 4 + 前端 2） | `pyproject.toml`、`gui/package.json` | backend-engineer / frontend-engineer | open | `specs/test-suite/` |
| D2 | 2026-09-27 | ADR-001 已知限制 | 🔵 | `timeout_s`/`max_retries`/`max_new_tokens` 无 env 覆盖且 LLM/VL 共享 | `src/config.py:120-152` | backend-engineer | open | `specs/deployment/` |
| F4 | 2026-09-27 | pm-memory | 🔵 | UI 文案语言不一致：入口中文、四个页面英文 | `gui/src/App.tsx:11,15-18` | 需人工确认（统一为哪种语言） | open | `specs/tauri-shell/` |

**统计**：🔴 4 · 🟡 8 · 🔵 9 · 合计 21 条 open，0 fixed，0 waived。

> 本账本的发现**全部为既有事实**，在 2026-09-27 的 agent 资产迁移会话中取证登记；本次迁移按用户批准的范围**未修改任何 `src/` 与 `gui/` 业务代码**，故一律为 `open` 而非 `fixed`。
> 🔴 四项按 `audit-report` skill 的触发规则本应有落盘报告；本次未纳入迁移范围，属**已知欠账**，下次 `/review` 时补落盘并回填报告索引。

---

## 三、读写纪律

| 动作 | 时机 | 必须同步 |
|------|------|---------|
| 新增报告文件 | `/review` 完成、hooks 出现 🔴、用户显式要求审计 | 报告索引 +1 行；发现账本逐条 +N 行 |
| 发现修复完成 | 后续审查确认修复后 | **回到原报告文件**更新状态字段（不新建报告）+ 账本状态改 `fixed` + `pm-memory.md` 待办打勾（不删除该行） |
| 发现豁免 | 人工明确批准放弃修复 | 账本状态改 `waived`，**必须注明批准人与理由** |
| hooks 仅 🟡/🔵 | 快速审查 | **不落盘**（避免噪音），但重复出现的同类项要进账本 |

- 账本**只追加与改状态**，不删除历史行；结论被推翻时追加新行并引用旧编号
- 存量债**不重复计级**：已在本账本登记的发现，后续审查未触及时只在报告附录引用一行，不重开条目
- 本目录文件属于 `docs/**`，会被 `docs-consistency-review` 检查 —— 比对时聚焦「发现描述与代码事实是否一致」，不要求账本与报告逐字相同
- 禁止写入密钥、Token、PII；`tasks/*/images/` 二进制与 base64 一律只引用路径
