# 产品经理记忆（归档）

> **定位**：[`pm-memory.md`](pm-memory.md) 的溢出归档区。spec 状态转为「已归档」时，其**四件套**（需求上下文 / 基线 / 变更影响 / SDD 合规记录）整体迁到这里，活跃区只留 Spec 台账一行。
> **规范源**：`.claude/skills/pm-memory/SKILL.md` Step 3 与「文件拆分约定」。
> **读取方式**：仅在回溯历史需求时查阅，**必须 `Grep(regex='^### ')` 定位后按行区间 `Read`，禁止全文读取**。
> **写入纪律**：迁移 = 从活跃区剪切 + 在此追加；**已归档记录不得改写**（结论被推翻时新增记录并引用旧条目）。

## 迁移操作

1. 在活跃区 `pm-memory.md` 找到该 spec 的四段记录
2. 按下方格式整段追加到本文件「归档记录」末尾
3. 从活跃区删除对应段落，Spec 台账行的「状态」改为「已归档」、填「归档日期」
4. 若该 spec 有未完成的遗留待办，**待办留在活跃区**（归档的是需求历史，不是债务台账）

## 归档记录格式

```markdown
### {spec 名}（归档于 {YYYY-MM-DD}）

- **需求上下文**：{背景 / 范围 / 实施栈 / 用户偏好约束}
- **交付基线**：{实际落地的文件与端点，带 `文件:行号`}
- **变更影响**：{影响到的文档 / 硬约束 / 磁盘产物 schema / API 契约 / 配置键}
- **SDD 合规**：{spec → design → implement → verify → archive 五阶段完成情况}
- **遗留债务**：{归档时仍未关闭的项，指向活跃区待办编号}
```

## 归档记录

### audit-fixes（归档于 2026-09-27）

- **需求上下文**：一期收尾对代码审核发现的集中修复，覆盖标签解析鲁棒性（BOM / 非 UTF-8 / 越界坐标 / 非法类别）、启动脚本子进程跟踪与退出码传播、CORS 与相对 `/api`、路径穿越防护、`task_dir` 非可变性、鉴权中间件与 `/api/health`。范围限于**已确认的审计条目**，不含新功能。实施栈：跨栈（后端为主 + 前端消费点）。
- **交付基线**（`specs/audit-fixes/spec.md` 对应实现，逐项带证据）：
  - 标签容错解析：`src/api_server.py:242`（`utf-8-sig` + `errors="replace"`）、`:250-262`（逐行跳过 + warning）、`:492-495`（`OSError` → 422）
  - 回归测试：`tests/test_api_server.py:212`（BOM）、`:223`（非法字节）、`:235`（坐标越界，硬约束 1）、`:246`（类别过滤，硬约束 3）、`:423-433`（IO 失败 → 422）
  - 读侧只读保证：`tests/test_api_server.py:258`、`:270`
  - CORS 与探活：`tests/test_api_server.py:160`、`src/api_server.py:287-294`
  - 路径穿越：`src/api_server.py:485`（`_safe_child`）、`tests/test_api_server.py:411`（`%5c` 编码变体）
- **变更影响**：影响 API 契约（新增 422 语义）、`tasks/<task>/` 磁盘产物（明确 `ai_labels/` 只读、修正只写 `candidate_labels/`）、启动脚本行为（端口/健康检查/退出码传播）。`CHANGELOG.md` 的「Fixed (audit follow-ups)」段即本批记录。
- **SDD 合规**：spec 与实现均已完成；本次结构改造将其从旧中间态目录迁为 `specs/audit-fixes/spec.md`（标注已归档），无独立 `design.md` —— 该批是既有设计的修复而非新设计，补 `design.md` 会写成事后追认，违反「禁止虚构未实现功能」。
- **遗留债务**：本批未覆盖的同类问题留在活跃区 —— 「IO 异常冒泡成 500」两处（`inspect_agent.py:188`、`model_client.py:158`）与「F2 后端 `detail` 到不了前端」。详见 [`bug-investigation-memory.md`](bug-investigation-memory.md) 排查记录 #1。
