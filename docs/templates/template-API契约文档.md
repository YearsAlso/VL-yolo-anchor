# 模板：API 契约文档

> **用途**：定义并锁定前后端之间的数据契约，做 Pydantic ↔ TS 的逐字段对照，防止契约静默漂移
> **产出路径**：`specs/<feature>/` 下的契约附录，或 `docs/architecture/api-契约.md`；审查产出走 `api-contract-review` skill 的报告格式，本模板用于**契约的规范定义与变更留档**
> **使用方**：`api-contract-review` skill；`backend-engineer` 定契约、`frontend-engineer` 对齐；`architect` 在 design.md 的 4.4 节引用
> **规范源**：`.claude/rules/api-response-contract.md`（响应模型硬性要求）、`.claude/rules/frontend-backend-contract.md`（同批提交纪律）
> **取代关系**：本模板替代上游项目的「数据契约文档」模板 —— 本项目**无数据库**，持久化是 `tasks/<task>/` 下的 YAML/txt，故删除 ER 图、表结构、索引/外键、迁移脚本等全部 DB 专用子结构

---

## 模板结构

`````markdown
# {契约标题}

> **定位**：{一句话说明本契约约束哪些端点/结构}
> **状态**：{草稿 / 评审中 / 已生效 / 已废弃}
> **实施栈**：跨栈
> **契约冻结点**：{后端 Pydantic 模型定稿的时点或提交号}
> **作者**：{agent 名或人名}
> **最后更新**：{YYYY-MM-DD}
> **版本**：{v1.0.0}

## 一、概述

{2-3 句说明本契约覆盖的功能范围、涉及的页面与上下游依赖。}

**三方一致性范围**：

| 角色 | 位置 | 事实源地位 |
|------|------|-----------|
| 契约定义 | `src/api_server.py`（Pydantic 模型 + `@app.*` 装饰器） | **唯一事实源** |
| 类型消费 | `gui/src/types/index.ts` | 必须与定义逐字段同名同型 |
| 调用消费 | `gui/src/services/api.ts` | 唯一 axios 出口，禁组件内直连 |
| 提示词契约 | `prompts/*.yaml` 的 `{}` ↔ `src/agents/*.py` | 双向一致（面 D） |

## 二、端点清单（契约面 A）

| 方法 | 路径 | 请求模型 | 响应模型 | 错误码 | 前端封装 | TS 类型 | 状态 |
|------|------|---------|---------|--------|---------|---------|------|
| `GET` | `/api/tasks` | — | `list[str]` | 404 | `listTasks()` | `string[]` | ✅ |
| `POST` | `/api/tasks/{name}/annotate` | `StepRequest` | `StepResponse` | 400/404/500 | `runAnnotate()` | `StepResult` | {✅/🔴/🟡} |

**校验点**：路径参数字面量、`{name}` 顺序、是否 `encodeURIComponent`、HTTP 方法、前缀 `/api`（nginx 反代依赖）。

## 三、字段字典

### 3.1 {模型名}（Pydantic ↔ TS 对照）

{一句话说明该结构承载什么。}

| 字段 | Pydantic 类型 | TS 类型 | 必填 | 默认值 | 可空 | 约束 | 说明 |
|------|--------------|---------|------|--------|------|------|------|
| `{field}` | `str` | `string` | Y/N | `{default}` | Y/N | {min_length / max_length / pattern / ge-le / enum 值集} | {一句话} |
| `{field}` | `list[OBBBoxModel]` | `ObbBox[]` | Y | `[]` | N | 每项 8 个 `[0,1]` 归一化坐标 | {…} |

**逐字段一致判定**：字段名（都 `snake_case`）/ 类型 / 可空性 / 枚举值集 / 数组形状 / 嵌套层级 —— 任一项不一致即 🔴 或 🟡。

**示例值**：

```json
{
  "image": "img_01.png",
  "boxes": [
    { "cls": 0, "points": [0.11, 0.22, 0.33, 0.44, 0.55, 0.66, 0.77, 0.88] }
  ],
  "source": "candidate_labels"
}
```

### 3.2 {下一个模型}

…

## 四、序列化与命名规则

| 项 | 后端 | 前端 | 是否一致 |
|----|------|------|---------|
| 字段命名 | `snake_case` | `snake_case`（**禁止** camelCase 转换层） | {✅/🔴} |
| `None` / 缺省 | Pydantic `default` + `exclude_none` 策略 | `?:` 可选属性 | {✅/🟡} |
| 数值 | `float` 归一化坐标 | `number` | {✅} |
| 枚举 | `Literal[...]` / `str` + `pattern` | 字符串字面量联合类型 | {✅/🔴} |
| 路径型字段 | 只回**文件名或相对名**，不回绝对路径 | 经 `imageUrl()` 拼接 | {✅/🔴} |
| 日期 | `str`（ISO 8601） | `string` | {✅} |
| 错误体 | `{"detail": "..."}` | **必须读 `detail`**，不得只读 `err.message` | {✅/🔴} |

## 五、约束规则

| 规则编号 | 等级 | 适用范围 | 规则描述 | 门禁 |
|----------|------|----------|----------|------|
| CR-001 | 强制 | 所有端点 | 必须声明 `response_model` 或具体返回类型注解 | `api-response-contract.md` |
| CR-002 | 禁止 | `gui/src/components/**`、`gui/src/pages/**` | 组件内直连 axios | `react-ts-review` ⑥ |
| CR-003 | 强制 | 契约变更 | 同批提交改齐 `api_server.py` + `types/index.ts` + `services/api.ts` | `frontend-backend-contract.md` |
| CR-004 | 禁止 | 归一化坐标 | 值域越出 `[0,1]` 或用 `x,y,w,h` 表达有向框 | `obb-hard-constraints.md` 第 1 节 |

## 六、磁盘产物 schema（本项目特有，替代上游 DB schema 章节）

| 产物 | 写入方 | 读取方 | 格式 | 变更影响 |
|------|--------|--------|------|---------|
| `tasks/<task>/task.yaml` | `TaskManager` / `pipeline` | 各 Agent | YAML | 键增删 = 配置契约变更 |
| `tasks/<task>/plan.yaml` | `pipeline._run_plan` | `api_server._allowed_classes`、前端计划页 | YAML | `classes` 结构变更 → 面 B |
| `tasks/<task>/ai_labels/*.txt` | `AnnotateAgent` | Inspect、导出 split、审核页 | 每行 `cls x1 y1 … x4 y4` | **只读，禁止覆盖** |
| `tasks/<task>/candidate_labels/*.txt` | 人工修正 / Inspect 建议 | split 优先读取 | 同上 | 标签来源优先级 |
| `tasks/<task>/inspection_report.yaml` | `InspectAgent` | 报告页 | YAML | 报告字段 → 面 B |
| `tasks/<task>/dataset/{train,val,test}` + `data.yaml` + `train_command.txt` + `export_summary.yaml` | `pipeline._run_split` | 导出页、用户训练命令 | YAML/txt | `train_command.txt` 是**可执行内容**，写入受 S1 命令注入约束 |

## 七、提示词契约（契约面 D）

| 文件 | 占位符集合 | 渲染方 `.format()` 实参 | 双向一致 |
|------|-----------|------------------------|---------|
| `prompts/plan_agent.yaml` | `{task_description}` `{dataset_size}` | `src/agents/plan_agent.py:132` | {✅/🔴} |
| `prompts/annotate_agent.yaml` | `{annotation_rules}` `{class_mapping}` `{exclude_items}` `{w}` `{h}` `{min_pixel}` | `src/agents/annotate_agent.py:86/:116` | {…} |

- **多余实参**（代码传了但模板没有）：{列出，或写"无"} —— `.format()` 不报错，属静默无效
- **缺失占位符**（模板有但代码没传）：{列出，或写"无"} —— `.format()` 抛 `KeyError`，属运行时崩溃
- **变更归档**：重大提示词变更按 `prompts/prompt_versions/<agent>_v<n>.yaml` 快照

## 八、变更历史

| 版本 | 日期 | 变更内容 | 破坏性 | 作者 |
|------|------|----------|--------|------|
| v1.0.0 | {YYYY-MM-DD} | 初始定义 | — | {作者} |
| v1.1.0 | {YYYY-MM-DD} | 新增 `{field}` | 否（前端可选消费） | {作者} |
| v2.0.0 | {YYYY-MM-DD} | `{field}` 改名 | **是**（前端会 `undefined`） | {作者} |

## 九、已知豁免

> 已登记的历史不一致，**不得仿写**；触及即补齐。

| 位置 | 现状 | 补齐条件 | 登记处 |
|------|------|---------|--------|
| `GET /api/tasks/{name}/plan` | 返回裸 `dict[str, Any]`，前端 `res.data as TaskPlan` | 触及该端点时补 Pydantic 模型 | `api-response-contract.md` |
`````

---

## 填写要点

1. **每一行对照都必须来自实际 `Read`**，字段名/类型/行号禁止凭印象；未取证处标注「需人工确认」
2. **Pydantic 模型是唯一事实源**：TS 类型向 Python 对齐，不是反过来
3. **逐字段核对六项**：名称、类型、可空性、枚举值集、数组形状、嵌套层级 —— 只核对"字段存在"会漏掉宽松类型导致的运行时 `undefined`
4. **严重度判定**（与 `api-contract-review` 一致）：🔴 契约断裂（前端会拿到 `undefined`）/ 🟡 类型宽松（能跑但不安全）/ 🔵 命名不一致（能跑但易误用）
5. **破坏性变更必须升 major 版本并同步 `docs/memory/pm-memory.md`**；无法同批对齐的前端改动必须留 `// TODO(contract)`
6. **错误体一节不得省略**：后端 `HTTPException` 只发 `detail`，前端若只读 `err.message` 则所有错误提示失真 —— 这一项在第四节最后一行强制核对
7. 模板内**禁止**出现数据库表结构、ER 图、SQL、索引/外键、迁移脚本（本项目无数据库；第六节是磁盘产物，不是 DB schema）
