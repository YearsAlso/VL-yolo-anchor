# SDD 工作流（Spec-Driven Development）

> **规范源**：[`.claude/rules/sdd.md`](../.claude/rules/sdd.md)（触发条件、跳过条件、六要素、合规判定）。本文是其**流程视图**；两者冲突以 rule 为准。
> **目录约定**：见 [`specs/README.md`](../specs/README.md)。
> **变更历史**：2026-09-27 由「旧命令 + `changes/` 中间态」流程改为本流程，属**破坏性变更**（见 `CHANGELOG.md`）。

---

## 五阶段

| 阶段 | 操作 | 执行者 | 产出 | 进入下一阶段的条件 |
|------|------|--------|------|-------------------|
| **1 Spec** | 定义六要素：业务背景 / 功能范围（含**不包含**）/ 接口契约 / 数据模型（磁盘产物与 YAML schema）/ 硬约束影响 / 边界条件 | `pm` 主笔，用户确认范围 | `specs/<name>/spec.md` | 六要素齐全且范围经确认；**无 spec 不进入设计** |
| **2 Design** | 技术设计、决策权衡（含**被否选项的代价**）、阶段拆解、风险回退、**契约冻结点** | `architect` | `specs/<name>/design.md` | 跨栈能力的契约冻结点已确立；OBB 硬约束逐小节有结论 |
| **3 Implement** | 按 design 的 A.1 变更清单实施；跨栈变更**串行** | `backend-engineer` 先落 Pydantic 与 `response_model` → 冻结 → `frontend-engineer` 对齐 TS 与 api.ts | 代码 + 测试，契约三文件**同批提交** | 门禁全绿；三契约文件同批，否则 🔴 阻断 |
| **4 Verify** | 对照 spec 第七节验收标准逐条核验 + 分维度审查 | `unit-tester` → `reviewer` | 测试通过；审查结论（🔴 则按 `audit-report` 落盘并登记账本） | 无 🔴 阻断项；验收逐条有证据 |
| **5 Archive** | 更新事实基线与记忆；spec 状态改「已实施/已归档」 | `pm` + `doc-writer` | `docs/architecture/`、`docs/memory/*`、`specs/<name>/` 状态标注 | 文档与代码一致（`docs-consistency-review` 通过） |

## 两条硬约束

1. **无 spec 不编码** —— 阶段 1 未完成前不得进入阶段 3
2. **所有变更必须有 `specs/<name>/` 目录** —— 由 `scripts/check_assets.py` 第 7 项门禁强制（每个子目录必须含 `spec.md`；`specs/` 根下禁止平铺 `*.spec.md`；`changes/` 目录必须不存在）

## 触发与跳过

**必须走 SDD**（触及任一条）：

- 新增/修改 API 端点
- 修改 OBB 归一化或 IoU 逻辑
- 修改 `prompts/*.yaml` 的提示词占位符集合
- 修改 `config/*.yaml` 的 schema
- 修改 `tasks/<task>/` 磁盘产物 schema
- 新增/删除依赖
- 重构涉及 >2 个文件

**可跳过**（直接实施，仍须门禁全绿）：

- 单文件 <20 行的 bugfix
- 注释与文档修订
- 纯配置值调整（不改 schema）
- 测试代码新增

## 两条串行纪律

**① 跨栈契约串行**（阶段 3）：`backend-engineer` 定契约 → 确立「契约冻结点」→ `frontend-engineer` 对齐。两个 engineer **禁止并发**实施契约变更。无法同批对齐时前端必须留 `// TODO(contract)`，`reviewer` 判 🔴 阻断。

**② 审查与验证不得并发**（阶段 4）：先**冻结**变更 → 审查 → **解冻** → 再实跑验证命令。审查开始时记录被审文件 **md5 哈希锚定**，交付前重算；不一致说明文件已被改动、结论失效，须重审。

## 缺陷与规格的分界

规格描述**要求**，缺陷登记在账本，二者不得混写：

| 内容 | 落点 |
|------|------|
| 应有的行为与验收 | `specs/<name>/spec.md` |
| 与要求的偏差（缺陷） | `docs/audit-reports/README.md` 发现账本（open/fixed/waived） |
| 根因与同类排查 | `docs/memory/bug-investigation-memory.md` |
| 沉淀成规则的教训 | `.claude/rules/*.md` 或对应 skill |

spec 里的「已知缺口」小节只写**账本编号与一句话指向**，不复述缺陷细节 —— 避免同一事实两处维护而漂移。

## 无中间态说明

本流程**不使用**暂存目录：提案直接以 `specs/<name>/spec.md` 起草（状态标「草稿」），评审通过后改「已生效」。历史中间态目录已于 2026-09-27 移除，其两份提案分别并入 `specs/deployment/design.md` 与 `specs/audit-fixes/spec.md`。
