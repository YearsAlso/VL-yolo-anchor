# Spec: audit-fixes（已实施，归档）

> **状态**：**已归档**（实施完成于 2026-09-26；2026-09-27 随 SDD 结构改造从变更提案目录迁为本 spec，提案原文已随中间态目录一并废弃）
> **类型**：修复批次规格（**不是新能力**，故无独立 `design.md` —— 事后补设计会写成对既有实现的追认，违反「禁止虚构未实现内容」）
> **来源**：CodeReview 报告（HEAD `16b5e14`，33 文件 / +11183 -1563），**13 个问题**：1 Blocker / 1 Critical / 2 High / 5 Medium / 4 Low
> **归档记录**：需求四件套见 `docs/memory/pm-memory-archive.md`
> **最后核对**：2026-09-27

## 一、业务背景

一期主体完成后的一次系统性代码审核产出 13 个问题。本规格把修复按**依赖关系**合并为 6 个批次，逐批修复并复跑基线（ruff / mypy / pytest / tsc / bash -n），目标是让 GUI 真正拿到数据、堵住路径与标签解析的健壮性缺口、修正进程跟踪与 UI 竞态。

## 二、功能范围（批次与目标）

| 批次 | 问题编号 | 目标 |
|------|---------|------|
| 1 | B1 | CORS + `api.ts` 相对 baseURL + Tauri `http.request`，让 GUI 真正拿到数据 |
| 2 | C2, M9 | 路径遍历守卫 + `task_dir` 去建目录副作用 + 任务名校验 |
| 3 | H4, M5, M6 | 标签解析鲁棒性（BOM / 非 UTF-8 / 越界坐标 / 非法 cls） |
| 4 | H3 | 启动脚本直接跟踪 python 子进程 + 端口/健康检查 + 退出码传播 |
| 5 | M7, M8, M10 | UI effect 竞态 + `source` 字段 + `listLabeledImages` 消费 |
| 6 | L11, L12, L13 | 变更归档 + Tauri CSP + 守护用例 |

## 三、接口契约（对正式 spec 的 delta，已生效）

### `specs/per-image-label-api/`

- `GET /api/tasks/{name}/labels/{image_name}` 响应补 `source: "candidate_labels" | "ai_labels"`
- 越界坐标（不满足 `is_coords_in_range`）与不在 `training_plan.classes` 的 cls，按畸形行同等处理（跳过 + warning）
- 标签文件整体不可读 → **422，不 500**
- 新增验收：CORS 允许 `localhost:5173` 与 Tauri origin；`_parse_label_file` 支持 UTF-8 BOM

### `specs/gui-launch-scripts/`

- 脚本须直接跟踪 `.venv` 的 `python -m uvicorn` 进程（**而非 uv 父进程**），退出时树杀
- 启动前探测 8765 占用、启动后健康检查，失败以非零码退出

### `specs/tauri-shell/`

- `allowlist.http` 必须含 `"request": true`（否则 `scope` 不生效）
- `security.csp` 设为最小 CSP（放行 `127.0.0.1:8765` 的 connect 与 img、Ant Design inline style），**不得为 `null`**

## 四、数据模型影响

无新增磁盘产物；变更集中在**标签解析行为**（BOM 剥离、非 UTF-8 替换、越界与非法类别过滤）与 `LabelBoxesResponse` 的 `source` 字段。

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 1 OBB 格式 | 越界坐标从「未知行为」明确为**丢弃该行**（`tests/test_api_server.py:235` 锚定） |
| 2 标签不可覆盖 | 新增守护用例 `test_label_endpoints_never_write`（`:258`）—— 读端点绝不写标签目录 |
| 3 任务规则隔离 | 类别过滤依据取自**当前任务** `plan.yaml`（`:246` 锚定） |
| 5 职责边界 | Tauri `http.request: true` 与 CSP 修正，使前端确实能取到后端数据（此前 GUI 拿不到数据） |
| 7 全相对路径 | `_safe_child` 路径穿越防护落地（`:411` 含 `%5c` 变体锚定） |

## 六、边界条件

- 未知任务的 GET **只判定不建目录**（`test_get_report_does_not_create_missing_task_dir`）
- 非 UTF-8 字节：替换后继续解析，**不因单文件编码问题让整请求 500**
- BOM 存在时**不得静默丢弃第一行**

## 七、验收与基线（实施当时的实测结果）

| 批次 | 验收 |
|------|------|
| 1 | ✅ CORS 生效（断言 `access-control-allow-origin` 回显 + preflight 200）；api.ts dev 相对 / prod 与 Tauri 绝对。<br>⚠️ **浏览器 5173 实测出框为手工项**，逻辑与契约已由用例覆盖 |
| 2 | ✅ `test_task_name_rejects_traversal`（422）、`test_get_image_rejects_unsafe_name`（拒绝 `..`/`a/b`/`.`）、GET 不建目录 |
| 3 | ✅ BOM 保首框、非 UTF-8 返回 200 不 500、越界坐标跳过、非法 cls 按任务计划过滤 |
| 4 | ✅ `bash -n start_gui.sh` 与 PowerShell AST 解析通过；PID/端口/健康检查/退出码传播已写入两脚本 |
| 5 | ✅ `test_label_response_reports_source`；`AnnotationReviewPage` effect 加 `cancelled` 守卫 + `listTasks().catch` + 已标注徽标与来源 Tag；`tsc --noEmit` 0 错 |
| 6 | ✅ CSP 非 null；`test_label_endpoints_never_write`、`test_list_labeled_images_excludes_unlabeled` |

**基线复跑（2026-09-26 当时）**：

| 命令 | 结果 |
|------|------|
| `uv run ruff check src/ run.py tests/` | All checks passed |
| `uv run mypy src/` | Success，15 files |
| `uv run pytest -q` | **49 passed**（37 → +12） |
| `cd gui && npx tsc --noEmit` | ExitCode 0 |
| `bash -n start_gui.sh` / PowerShell AST | 通过 |
| `tauri.conf.json` JSON 合法性 | 通过 |

> 现基线 **99 passed / 4.40s**、mypy 覆盖 17 文件 —— 后续轮次（补测与双模型配置拆分）新增，不属本批。

## 迁移说明

1. 批次 6 的交付物含「变更归档（中间态目录与 proposal 文件）」，该做法已于 2026-09-27 的 SDD 结构改造中**废弃** —— 现约定为 `specs/<feature>/` 目录制，不存在中间态。本条作为历史记录保留，不再作为流程范例。
2. 本批**未覆盖**的同类问题仍在新账本中开放：IO 异常冒泡成 500（`inspect_agent.py:188`、`model_client.py:158`）、前端不读 `detail`（F2）。详见 [`bug-investigation-memory`](../../docs/memory/bug-investigation-memory.md) 排查记录 #1 与 `docs/audit-reports/README.md`。
