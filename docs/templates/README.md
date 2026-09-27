# 文档模板中心

本目录是 VL-YOLO-Anchor 的**标准文档模板目录**，也是 `.claude/skills/doc-template/SKILL.md` 的强制检查位置。

> **单目录约定**：本项目只有 `docs/templates/` 一个模板目录，**不存在** `docs/template/`（单数）。上游项目曾双目录并存，属历史包袱，本项目不继承。新增模板一律放这里并在下方映射表登记。

## 使用规则

1. 编写/更新任何文档前，先检查本目录是否有匹配模板（`ls docs/templates/` 或读下方映射表）
2. 有模板 → 按模板章节结构填写，占位符 `{...}` **全部替换**为实际内容，不保留示例数据
3. 无模板 → 不自行编造结构，先提示「本目录无该类型模板」并建议创建；急需则参考 `docs/` 下同类文档结构编写
4. 新建模板后 → **同步更新下方映射表与 `doc-template` skill 的映射表，两处必须一致**（该 skill Step 4 的硬性要求）

## 文档类型 → 模板映射表

| 文档类型 | 模板文件（`docs/templates/`） | 适用产出 |
|---------|------------------------------|---------|
| 开发规范 | `template-开发规范文档.md` | `.claude/rules/*.md`、`CLAUDE.md` 章节、编码约定说明 |
| 设计方案 / 技术架构 | `template-设计方案文档.md` | `specs/<name>/design.md`、`docs/architecture/*.md` |
| 审查结果 | `template-审查结果文档.md` | reviewer 分级审查报告的落盘形态 |
| 审计报告 | `template-审计报告.md` | `docs/audit-reports/{date}-{scope}.md`（audit-report skill 用） |
| API 契约 | `template-API契约文档.md` | 端点 ↔ Pydantic ↔ TS interface 对照表（api-contract-review skill 用） |
| Bug 排查记忆 | `template-Bug排查记忆.md` | `docs/memory/bug-investigation-memory.md`（bug-investigation-memory skill 用） |
| Spec 规范 | 无独立模板，结构定义在 `.claude/rules/sdd.md` | `specs/<name>/spec.md`（六要素） |

## 模板清单与归属

| 模板 | 规范源 skill | 下游消费文件 |
|------|-------------|-------------|
| `template-开发规范文档.md` | `doc-template` | `.claude/rules/*.md`、`CLAUDE.md` |
| `template-设计方案文档.md` | `doc-template`、`design-doc` workflow | `specs/*/design.md`、`docs/architecture/*.md` |
| `template-审查结果文档.md` | `review` | reviewer 对话输出 |
| `template-审计报告.md` | `audit-report` | `docs/audit-reports/*.md` + 账本登记 |
| `template-API契约文档.md` | `api-contract-review` | 契约附录、`specs/*/design.md` 的 4.4 节 |
| `template-Bug排查记忆.md` | `bug-investigation-memory` | `docs/memory/bug-investigation-memory.md` |

> 本节故意不记录模板行数 —— 行数是派生值，写进文档就会与实际漂移；需要时用 `Read` 或 `Grep(regex='^## ')` 实时取。

## 模板编写规范

- 模板用 `{占位符}` 标注需填写位置，且**必须提供填写示例或指向本项目实况文档**
- 章节结构可复用 `docs/` 现有文档的成熟结构，不为了统一而砍掉有用小节
- 每个模板文件头必须说明：用途、产出路径、使用方、规范源（哪个 skill/agent 消费它）
- 模板只定义结构与填写要点，**不承载实际内容**（不写具体实现结论）
- 文件名统一 `template-{中文类型名}.md`，放本目录
- 内容必须与本项目实况一致：**禁止**出现数据库表结构、ER 图、SQL、迁移脚本、C#/.NET 相关章节
- 内层结构块用**四个反引号**包裹（`````），以便内部示例中的三反引号代码块正常渲染

## 相关目录

| 目录 | 用途 | 读写时机 |
|------|------|---------|
| `specs/<feature>/` | spec（六要素）+ design（技术设计） | 每个新能力必须先建目录 |
| `docs/architecture/` | 架构事实基线 | 架构变更后更新；`docs-consistency-review` 的比对锚点 |
| `docs/memory/` | agent 记忆（红线 / ADR / 推演 / PM / 排查） | 由对应 skill 追加，只追加不改写 |
| `docs/audit-reports/` | 审计报告与发现账本 | reviewer 出现 🔴 或用户要求审计时落盘 |
| `docs/archive/` | 已废弃文档 | 删除历史文档前先移入，不直接 `rm` |
