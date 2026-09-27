# Proposal: 审核问题修复（audit-fixes）

- 日期：2026-09-26
- 来源：CodeReview 报告（HEAD `16b5e14` 的 33 文件 / +11183 -1563），13 个问题（1 Blocker / 1 Critical / 2 High / 5 Medium / 4 Low）。
- 范围：按依赖合并为 6 个批次，逐批修复并复跑基线（ruff/mypy/pytest/tsc/bash -n）。

## 批次概览

| 批次 | 问题 | 目标 |
|------|------|------|
| 1 | B1 | CORS + api.ts 相对 baseURL + Tauri http.request，让 GUI 真正拿到数据 |
| 2 | C2, M9 | 路径遍历守卫 + task_dir 去建目录副作用 + 任务名校验 |
| 3 | H4, M5, M6 | 标签解析鲁棒性（BOM / 非 UTF-8 / 越界坐标 / 非法 cls） |
| 4 | H3 | 启动脚本直接跟踪 python 子进程 + 端口/健康检查 + 退出码传播 |
| 5 | M7, M8, M10 | UI effect 竞态 + source 字段 + listLabeledImages 消费 |
| 6 | L11, L12, L13 | changes/ 归档 + Tauri CSP + 守护用例 |

## 对正式 spec 的 delta

### per-image-label-api.spec.md

- 输出约束新增：`GET /labels/{image}` 响应补 `source: "candidate_labels" | "ai_labels"`。
- 新增约束：越界坐标（不满足 `is_coords_in_range`）与不在 `training_plan.classes` 的 cls 按畸形行同等处理（跳过 + warning）；非 UTF-8 标签文件返回 422（不 500）。
- 新增验收：CORS 允许 5173 与 Tauri origin；`_parse_label_file` 支持 UTF-8 BOM。

### gui-launch-scripts.spec.md

- 边界条件细化：脚本须直接跟踪 `.venv` 的 `python -m uvicorn` 进程（而非 uv 父进程），退出时树杀；启动前探测 8765 占用、启动后做健康检查，失败以非零码退出。

### tauri-shell.spec.md

- 输出约束新增：`allowlist.http` 含 `"request": true`；`security.csp` 设置最小 CSP（允许 127.0.0.1:8765 connect 与 img、Ant Design inline style）。

## Verify（逐批追加）

- [x] Batch 1：CORS 生效（`test_cors_allows_gui_origin` 断言 `access-control-allow-origin` 回显 + preflight 200）；api.ts dev 走相对 proxy，prod/Tauri 走绝对；tauri http `request: true`。（浏览器 5173 实测出框为手工项，逻辑与契约已由用例覆盖）
- [x] Batch 2：`test_task_name_rejects_traversal`（422）、`test_get_image_rejects_unsafe_name`（`_safe_child` 拒绝 `..`/`a/b`/`.`）、`test_get_report_does_not_create_missing_task_dir`（GET 不建目录）全绿。
- [x] Batch 3：`test_label_file_with_bom_keeps_first_box`、`test_non_utf8_label_returns_200_not_500`、`test_out_of_range_coords_skipped`、`test_invalid_cls_filtered_by_task_plan` 全绿。
- [x] Batch 4：`bash -n start_gui.sh` 与 PowerShell AST 解析均通过；跟踪 `.venv` python PID、端口占用检查、健康检查、退出码传播已写入两脚本。
- [x] Batch 5：`test_label_response_reports_source`；`AnnotationReviewPage` effect 竞态加 `cancelled` 守卫 + `listTasks().catch` + `listLabeledImages` 徽标 + 来源 Tag；`npx tsc --noEmit` 0 错误。
- [x] Batch 6：`changes/.gitkeep` 与本 proposal；`tauri.conf.json` CSP 非 null；`test_label_endpoints_never_write`、`test_list_labeled_images_excludes_unlabeled`。

## 基线复跑结果

- `uv run ruff check src/ run.py tests/`：All checks passed
- `uv run mypy src/`：Success, 15 files
- `uv run pytest -q`：49 passed（37 → +12）
- `cd gui; npx tsc --noEmit`：ExitCode 0
- `bash -n start_gui.sh` / PowerShell AST：通过
- `tauri.conf.json` JSON 合法：OK
