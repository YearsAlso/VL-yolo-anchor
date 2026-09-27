---
name: audit-report
description: 审计报告编写 Skill — reviewer 完成审查后按 docs/templates/template-审计报告.md 模板，将分级审查发现落盘为 docs/audit-reports/YYYY-MM-DD-{scope}.md 规范审计报告，并登记 docs/audit-reports/README.md 发现账本（open/fixed/waived），供历史检索与重复问题统计
---

# Audit Report — 审计报告编写规范

将 reviewer 的审查发现固化为**可检索、可跟踪、可复用**的审计报告。审计报告是过程证据（谁审了什么、发现了什么、状态如何），与 `architect-memory` / `pm-memory` 的结论沉淀互补：报告回答"这次审查发现了什么"，记忆回答"沉淀了什么规则"。

## 触发时机

| 触发 | 是否落盘 | 说明 |
|------|---------|------|
| `/review` 全维度审查完成 | **必做** | 每次深度审查都必须产出审计报告 |
| hooks 快速审查出现 🔴 阻断级发现 | **必做** | 阻断级问题必须留痕，便于跟踪修复 |
| hooks 快速审查仅 🟡/🔵 | 不落盘 | 轻量反馈，避免噪音 |
| 用户显式要求审计 | **必做** | 按用户指定范围审计 |

## 报告落盘位置

- 报告文件：`docs/audit-reports/{YYYY-MM-DD}-{scope}.md`
  - `scope` = 变更主题短名（kebab-case，中英文皆可但同一批保持一致），如 `annotate-agent-retry`、`obb-normalize-fix`、`gui-task-select`
  - 同日多份时追加序号：`{YYYY-MM-DD}-{scope}-{n}.md`
- 发现账本：`docs/audit-reports/README.md`（**两张表**：报告索引一行一报告 + 发现账本一行一发现，见"账本登记"）
- 模板来源：`docs/templates/template-审计报告.md`（本项目模板只有 `docs/templates/` 单目录，无 `docs/template/` 单数目录）

## 执行流程

### Step 1: 收集审查输入

- 变更范围：`git diff main...HEAD --name-only`（或传入文件列表）；提交前审查用 `git diff --cached --name-only`
- 各审查维度输出：python-code-review / react-ts-review / security-review / architecture-review / docs-consistency-review / ui-impact-review / api-contract-review 的分级发现
- 核心边界命中：变更是否触及 **OBB 硬约束**（`src/utils/obb_utils.py` 归一化与 IoU、`candidate_labels/` 写入、任务规则隔离、`prompts/*.yaml` 契约、`config/*.yaml` schema、`tasks/<task>/` 磁盘产物），命中则单独标注"OBB 硬约束影响"
- 跨栈变更额外记录**契约冻结点**：`src/api_server.py` 的 Pydantic 模型 ↔ `gui/src/types/index.ts` ↔ `gui/src/services/api.ts` 三方是否同批改齐

### Step 2: 按模板生成报告

按 `docs/templates/template-审计报告.md` 的章节结构填写，**禁止自由发挥结构**：

1. 元信息（审计时间 / 触发方式 / 审查范围 / 路由维度 / 实施栈 / **哈希锚定**）
2. 执行摘要（1-3 句 + 3 项最高优先级）
3. 统计总览（分级计数、维度计数、后端/前端文件计数）
4. 详细发现（P0 / P1 / P2 分级，每条含状态、`文件:行号` 证据与**修复方**）
5. OBB 硬约束影响（触及 `.claude/rules/obb-hard-constraints.md` 任一条时的逐条核对结论）
6. 前后端契约影响（跨栈变更时的契约面 A/B/C/D 比对结论与遗留债）
7. 修复建议（按优先级 + 估计工时 + 建议执行 agent）
8. 附录

分级映射（与 reviewer 输出一致）：

| reviewer 级别 | 报告级别 | 含义 |
|--------------|---------|------|
| 🔴 阻断 | P0 | 运行时缺陷 / 安全漏洞 / 架构分层违规 / **OBB 硬约束违反** / **前后端契约断裂** → 必须修复 |
| 🟡 警告 | P1 | 命名规范 / 异常处理 / 类型宽松 / 文档不一致 → 建议修复 |
| 🔵 建议 | P2 | 可改进设计 / 测试性优化 / dead surface → 可选 |
| ✅ 通过 | — | 计入统计，不单列条目 |

> **存量债不重复计级**：`.claude/skills/api-contract-review/SKILL.md` 的「现状基线」表已登记的既有技术债，本次未触及时只在附录列一行引用，不重复开 P1 条目。

### Step 3: 落盘报告文件

- 文件写入 `docs/audit-reports/{YYYY-MM-DD}-{scope}.md`
- 占位符 `{...}` 必须全部替换为实际内容，不保留模板示例
- 每条发现必须带 `文件:行号` 证据；误报条目必须附带"教训"

### Step 4: 账本登记

`docs/audit-reports/README.md` 有**两张表**，两张都要写：

**① 报告索引 —— 一行一报告**（含分级计数与整体状态）：

```markdown
| {YYYY-MM-DD} | {scope} | [{scope}.md]({文件名}) | {后端/前端/跨栈} | {N} | {N} | {N} | open |
```

列头：`| 日期 | scope | 报告文件 | 实施栈 | 🔴 | 🟡 | 🔵 | 整体状态 |`

**② 发现账本 —— 一行一发现**（在所有报告之间逐条跟踪，不展开明细）：

```markdown
| {编号} | {YYYY-MM-DD} | {报告 scope / pm-memory / 排查记录 #N} | 🔴 | {一句话发现} | `{文件}:{行号}` | {backend-engineer/frontend-engineer/需人工确认} | open | {specs/<name>/}
```

列头：`| 编号 | 日期 | 来源 | 严重度 | 发现（一句话） | 位置 | 修复方 | 状态 | 关联 spec |`

两张表的列头必须与 `docs/audit-reports/README.md` 现有表头逐字一致；若该文件不存在则先创建 README.md（含上述两节）。只追加与改状态，**不删除历史行**；结论被推翻时追加新行并引用旧编号。

> 存量债不重复计级：已在账本登记的发现，本次未触及时只在报告附录引用一行，不重开条目。

### Step 5: 发现状态跟踪

| 状态 | 含义 | 更新时机 |
|------|------|---------|
| open | 已登记未修复 | 报告落盘时 |
| fixed | 已修复并经验证 | 后续审查确认修复后，在原报告状态区 + 账本更新 |
| waived | 人工批准豁免 | 人工明确批准放弃修复时，注明批准人与理由 |

修复确认后**回到原报告文件**更新对应发现的状态字段（不新建报告），保持单一事实源。

## 与记忆转交的关系

- 审计报告 = 过程证据（落盘 `docs/audit-reports/`）
- 记忆转交 = 结论沉淀（reviewer Step 5 转交 architect/pm）
- **两者都要做**：审计报告不替代 ADR / pm-memory 记录；审查产生架构决策时，报告落盘 + 记忆转交并行执行

## 工作约束

- 本 skill 只负责报告编写与落盘，不修改业务代码
- 报告内容必须与审查事实一致，禁止虚构发现或夸大级别；未实际取证（未读代码/未跑命令）的结论一律标注"需人工确认"
- 报告文件属于 `docs/**`，会被 docs-consistency-review 检查；审计报告是过程记录，比对时聚焦"发现与代码事实是否一致"
- 不向报告写入密钥、Token、PII 等敏感信息（发现涉及 `VL_LLM_API_KEY` / `VL_VL_API_KEY` 泄露时，只描述位置与类别，不复制明文）
- 不向报告粘贴大图/base64 或 `tasks/*/images/` 二进制内容，只引用路径
- 单次审计报告生成耗时 ≤60s（复用已有审查输出，不重复审查）
