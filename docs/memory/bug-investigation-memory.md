# Bug 排查记忆

> **定位**：把每次 bugfix 的**根因 + 同类排查结论**沉淀为可检索的模式知识，用于下次遇到同类信号时直接命中。
> **维护者**：`bug-investigation-memory` skill（bugfix workflow 阶段 2.5 调用）；结构规范源 = [`.claude/skills/bug-investigation-memory/SKILL.md`](../../.claude/skills/bug-investigation-memory/SKILL.md)，记录格式 = [`docs/templates/template-Bug排查记忆.md`](../templates/template-Bug排查记忆.md)。
> **写入纪律**：**只追加，不覆盖不删除**；结论被推翻时新增记录并引用旧编号。
> **读取方式**：`Grep(regex='## 排查记录 #\d+')` 定位后按行区间 `Read`，禁止全文读取。

---

## 排查记录 #1

### 元信息

- **排查日期**：2026-09-27
- **根因文件**：`src/api_server.py:242`（读取点）／`src/api_server.py:492-495`（异常分类点）
- **根因类型**：`资源泄漏` 分支 —— 文件 IO 异常处理粒度不足（未区分「文件不可读」与「服务器内部错误」）
- **实施栈**：后端（含测试侧）
- **触发场景**：GUI 在标注审核页读取某张图的 OBB 标签时，该 `.txt` 标签文件正被编辑器/同步盘独占（Windows sharing violation），后端 `Path.read_text` 抛 `OSError`
- **排查人**：本次迁移取证（审计编号 `H4` 的回归沉淀，修复清单归组见 `specs/audit-fixes/spec.md`）
- **状态**：**已修复**，回归测试在位（`tests/test_api_server.py:423-433`）

### 问题概要

「标签文件读不出来」被当成 500 服务器内部错误抛给前端，而它本质是**客户端可恢复的数据问题**，应当返回 422 并带上文件名；顺带暴露出「用真实文件锁写测试」在跨平台下不可靠。

### 根因分析

**调用链**（`GET /api/tasks/{name}/labels/{image_name}`）：

```
api_server.py:469 get_label_boxes
  → :484 _require_task(name)
  → :485 _safe_child(task_dir / "images", image_name)      # 路径穿越防护
  → :488 _find_label_file(task_dir, image_name)             # 只做存在性检查，顺序 candidate_labels > ai_labels
  → :493 _parse_label_file(label_path, _allowed_classes(task_dir))
        → :242 path.read_text(encoding="utf-8-sig", errors="replace")   # ← OSError 从这里逃逸
```

修复前 `:493` 外层没有 `except OSError`，异常一路冒泡到 FastAPI 默认处理 → **HTTP 500**，前端只能显示「服务器错误」，用户不知道是哪一个标签文件读不了、也不知道该去关谁。

**关键代码（现状，已修复）**：

```python
# src/api_server.py:492-495
    try:
        boxes = _parse_label_file(label_path, _allowed_classes(task_dir))
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Unreadable label file: {label_path.name}") from exc
```

`_parse_label_file` 的 docstring 明确把这一契约写进接口说明（`:238-239`）：

```python
    Raises:
        OSError: If the file cannot be read at all (surfaced as HTTP 422).
```

注意它**只**对逐行内容问题（`ValueError` / 坐标越界 / 类别不在 plan）做跳过 + warning（`:250-262`），对「整个文件读不出来」刻意放行 OSError，由调用方统一分类 —— 这是本次修复的**正确样板**。

**测试侧的教训（本条记录的核心沉淀）**：

最初的用例试图「真的把文件独占打开」来复现，实测不可靠：

- Windows 下独占打开确实会让后续读抛 `OSError`（sharing violation）
- Linux / macOS **没有强制文件锁**，同样的写法在 CI（ubuntu-latest）上根本不会失败 ⇒ 测试**看起来在测，实际永远通过**

改为 `monkeypatch` 注入 IO 异常，跨平台一致且秒级：

```python
# tests/test_api_server.py:423-433
def test_unreadable_label_file_returns_422(client: TestClient, monkeypatch) -> None:
    """H4: an OSError while reading a label surfaces as 422, never 500."""
    _make_task_with_image_and_label(client)

    def _boom(self, *args, **kwargs):
        raise OSError("sharing violation")

    monkeypatch.setattr(Path, "read_text", _boom)
    res = client.get("/api/tasks/demo/labels/img_01.png")
    assert res.status_code == 422
    assert "Unreadable label file" in res.json()["detail"]
```

两条断言都有效：改坏 `:494` 的状态码 → 第一条红；改坏 `detail` 文案 → 第二条红。符合「若把实现改错这条断言会红吗」判据。

### 受影响的文件

| 文件 | 行号 | 变更说明 |
|------|------|---------|
| `src/api_server.py` | 492-495 | `except OSError` → `HTTPException(422)`，detail 带文件名，`from exc` 保留链 |
| `src/api_server.py` | 238-239 | docstring 补 `Raises: OSError` 契约说明 |
| `src/api_server.py` | 242 | 读取改 `encoding="utf-8-sig", errors="replace"`，消除 BOM / 非法字节导致的静默丢行（同属 H4，另见 `tests/test_api_server.py:212`、`:223`） |
| `tests/test_api_server.py` | 423-433 | 新增 monkeypatch 用例（替代真实文件锁写法） |

### 同类排查结果

| 排查维度 | 文件 | 行号 | 是否存在同类问题 | 说明 |
|---------|------|------|----------------|------|
| 同模块 | `src/api_server.py` | 211-215 | 否 | `_allowed_classes` 用 `except Exception` 兜底 plan 读取（注释「a broken plan must not break label reads」），已覆盖 OSError 家族 |
| 同模块 | `src/api_server.py` | 447-466 | 否 | `list_labeled_images` 只调 `_find_label_file` 做存在性检查，**不读文件内容**，不受影响 |
| 同模式 | `src/agents/inspect_agent.py` | 188 | **是** | `label_path.read_text(encoding="utf-8")` 既无 `except OSError`，又用**严格** utf-8（不像样板用 `errors="replace"`）。链：`:188` ← `:92` ← `:37 run()` → `pipeline.run_step` → `api_server.py:342-346` 的 `except Exception` → **HTTP 500 + `detail=str(exc)`**。非 UTF-8 标签文件还会抛 `UnicodeDecodeError`（`ValueError` 子类，**不是** `OSError`），即便照抄 `:494` 的捕获也拦不住 |
| 同模式 | `src/agents/model_client.py` | 158 | **是** | `_data_url` 的 `path.read_bytes()` 无保护；图片在 annotate 期间被移走/独占 → 同样经 `api_server.py:342-346` 变成 500 |
| 同类型 | `src/utils/image_utils.py` | 64-67 | 疑似 | `validate_image` 只捕 `(FileNotFoundError, ValueError)`；`load_image:30` 的 `np.fromfile` 在文件被独占时抛的是 `PermissionError`/`OSError`，**不在捕获列表**。未实测独占场景下的实际异常类型，故标「疑似」不计入需修复 |
| 同类型（测试侧） | `tests/*.py` | — | 否 | 全仓无「真实文件锁 / `tempfile` 独占 / `fcntl` / `msvcrt`」写法，IO 失败一律靠 monkeypatch 注入 |

### 同类问题统计

- **总排查文件数**：6（`src/api_server.py`、`src/agents/inspect_agent.py`、`src/agents/model_client.py`、`src/utils/image_utils.py`、`src/core/pipeline.py`（调用链验证）、`tests/test_api_server.py`）
- **发现同类问题数**：4（含 1 条「疑似」）
- **需修复的同类问题数**：2（`inspect_agent.py:188`、`model_client.py:158`）

### 修复建议

按 P0/P1/P2 分级，均为**建议**，本次迁移未改动业务代码：

| 优先级 | 项 | 策略 | 建议执行 agent |
|--------|----|------|---------------|
| P1 | `inspect_agent.py:188` | 对齐 `api_server.py:242` 样板：`read_text(encoding="utf-8-sig", errors="replace")` 消除解码逃逸；解析失败的**单个文件**记录进 `report` 的 issues 而不是中断整个 inspect 步骤 | `backend-engineer` |
| P1 | `model_client.py:158` | `except OSError` → 抛带图片名的明确异常，由 annotate 步骤汇总；不要在 HTTP 层暴露 `str(exc)` 原文 | `backend-engineer` |
| P2 | `api_server.py:342-346` | `except Exception` 作为最后兜底可以保留，但 `detail=str(exc)` 会把内部异常文本直接透给 GUI；建议按异常类型分类（IO 类 → 422，其余 → 500 且只回摘要），并与 `.claude/rules/api-response-contract.md` 的 `{detail}` 约定对齐 | `backend-engineer` |
| P3（观察） | `image_utils.py:66` | 先在真实 Windows 独占场景取证 `np.fromfile` 抛的异常类型，再决定是否把 `OSError` 加进捕获列表；不要凭推测扩大捕获面 | 需人工确认 |

登记位置：以上均需同步登记 `docs/audit-reports/README.md` 发现账本与 `docs/memory/pm-memory.md` 的「遗留待办与技术债」。

### 预防措施

- **编码规范补充**：`.claude/skills/python-code-review/SKILL.md` 维度 5（异常处理）与维度 6（路径与文件 IO）—— 新增检查项：任何**用户可恢复**的文件 IO 失败，最近的 HTTP 边界必须分类为 4xx，禁止让 `OSError` 冒泡成 500
- **编码规范补充（对既有第 86 行的精确化）**：skill 现写「文件读写显式 `encoding="utf-8"`」；对**外部来源**文件（用户放进 `images/`、别的工具产出的 `ai_labels/`）应进一步写 `encoding="utf-8-sig", errors="replace"`，否则 BOM 会静默吞掉第一行、非 UTF-8 字节会抛 `UnicodeDecodeError`（它属于 `ValueError` 家族，`except OSError` 拦不住）。内部自产文件仍用严格 `utf-8` 以便尽早暴露写入 bug
- **测试补充**：凡「文件读不了 / 解码失败 / 权限不足」的用例，一律 `monkeypatch.setattr(Path, "read_text", _boom)` 或 `monkeypatch.setattr(Path, "read_bytes", _boom)` 注入，**禁止**依赖真实文件锁；单文件用例 ≤10s
- **断言有效性**：照此用例保持「状态码 + `detail` 文案」双断言，单断状态码会让文案改错检测不到（`.claude/rules/assertion-integrity.md`）
- **审查清单补充**：`python-code-review` 的「异常处理」维度新增检查项 —— `read_text` / `read_bytes` / `open` 调用点上溯，确认最近 HTTP 边界有对应异常分类
- **静态分析局限**：ruff / mypy **拦不住**这类问题（异常是否被捕获属运行时行为，`mypy --strict` 不做异常流分析），只能靠审查 + 测试覆盖

---
