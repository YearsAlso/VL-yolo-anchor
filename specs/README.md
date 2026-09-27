# specs/

**正式规格目录 —— 实现、测试与验收的唯一可信源。**

## 目录约定

**每个能力一个目录**，目录名用 kebab-case：

```
specs/
  <feature>/
    spec.md          必需 —— 六要素规格
    design.md        必需（修复批次类可豁免，见下方说明）—— 技术设计
    test-design.md   可选 —— 复杂能力的测试设计
```

| 约定 | 说明 |
|------|------|
| 一能力一目录 | 目录名即能力名，跨文档引用一律写 `specs/<feature>/spec.md` |
| 禁止平铺 | **禁止**在 `specs/` 根下放置 `*.spec.md`（该平铺结构已于 2026-09-27 废弃） |
| 无中间态 | **不存在** `changes/` 暂存目录；提案直接写入 `specs/<feature>/`，验收后原地标注状态 |
| 状态写在文档里 | 每份 spec/design 头部标注 `状态`（草稿 / 评审中 / 已生效 / 已实施 / 已归档） |
| 门禁强制 | `scripts/check_assets.py` 第 7 项校验：每个子目录必须含 `spec.md`，根下不得有平铺 `*.spec.md`，`changes/` 必须不存在 |

## spec.md 六要素

依据 [`.claude/rules/sdd.md`](../.claude/rules/sdd.md)：

| 要素 | 内容 |
|------|------|
| 1 业务背景 | 为什么做这个能力，解决谁的什么问题 |
| 2 功能范围 | **包含 / 不包含**（不包含同样重要，防 scope creep） |
| 3 接口契约 | 端点、CLI、协议、提示词占位符 —— 跨栈须明确唯一事实源 |
| 4 数据模型 | **磁盘产物与 YAML schema**（本项目无数据库）：路径、格式、可写性、优先级 |
| 5 硬约束影响 | 触及 `.claude/rules/obb-hard-constraints.md` 哪几小节，逐条给出结论 |
| 6 边界条件 | 空集合、非法输入、缺失配置、并发与跨平台行为 |

各 spec 在此基础上普遍追加**第七节 验收标准**（可执行的命令或用例清单），以及「已知缺口 / 实现位置」小节 —— 用于把现状缺陷与规格本身分离，避免规格为已知问题背书。

## design.md 要求

依据 [`docs/templates/template-设计方案文档.md`](../docs/templates/template-设计方案文档.md)。除决策权衡与阶段拆解外，**跨栈能力必须含「契约冻结点」**：后端 Pydantic 模型定稿的时点或提交号；未冻结则前端不得开工。

**豁免条件**：纯修复批次规格（只改行为、不引入新设计）可不写 `design.md`，但必须在 spec 头部说明理由 —— 事后补设计会写成对既有实现的追认，违反「禁止虚构未实现内容」。当前豁免：`specs/audit-fixes/`。

## 当前规格清单

| 目录 | 状态 | 内容 |
|------|------|------|
| `platform-core/` | 已实施 | 四步流水线编排、双入口、stub/remote 双模型 |
| `per-image-label-api/` | 已实施 | 逐图标签只读端点与容错解析（跨栈） |
| `tauri-shell/` | 已实施 | Tauri v1 桌面壳与 allowlist/CSP 配置 |
| `gui-launch-scripts/` | 已实施 | 一键启动脚本的进程跟踪与退出码传播 |
| `test-suite/` | 已实施 | pytest 套件基线（**99 passed**）与断言纪律 |
| `deployment/` | 已实施 | Linux Docker 自部署与远程模型端点（吸收了原部署提案） |
| `platform-config/` | 已实施 | 配置读取/写回与来源标记、加密密钥库、doctor 自检与端点探测（跨栈，吸收原配置引导提案） |
| `metadata-store/` | 已实施 | 磁盘审计日志（三份 JSONL）+ 可随时重建的 SQLite 派生索引 |
| `audit-fixes/` | **已归档** | 2026-09-26 的 13 项审核问题修复批次 |

> 新增/变更能力时同步本表，否则与 `docs/memory/pm-memory.md` 的 Spec 台账不一致，会被 `docs-consistency-review` 判为文档差异。

## 与文档体系的关系

| 位置 | 分工 |
|------|------|
| `specs/<feature>/spec.md` | 需求与验收（**唯一可信源**） |
| `specs/<feature>/design.md` | 技术设计与权衡 |
| `docs/architecture/技术架构.md` | 跨能力的整体事实基线 |
| `docs/memory/architect-decisions.md` | 架构决策（ADR，只追加） |
| `docs/audit-reports/README.md` | 发现账本（缺陷跟踪，**不写进 spec**） |
| `docs/archive/` | 废弃文档 |

**已知缺陷不写进规格正文当作要求**，只登记账本；spec 的「已知缺口」小节用于指向账本编号，不复述缺陷细节。
