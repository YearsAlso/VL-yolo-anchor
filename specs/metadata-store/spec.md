# Spec: metadata-store（磁盘审计日志 + SQLite 派生索引）

> **状态**：已实施（as-built，从 `specs/metadata-store.spec.md` 迁移并重组为六要素）
> **设计**：[`design.md`](design.md)（吸收了原 `2026-09-27-config-onboarding` 提案中元数据层的交付物与验收对照）
> **实施栈**：后端（`src/core`、`src/agents`、`src/api_server.py`、`run.py`）+ 前端（质检报告页）
> **最后核对**：2026-09-27

## 一、业务背景

平台此前只有「当前状态」：`/api/tasks` 遍历目录给出任务清单，`/api/health` 回一句
`{"status":"ok"}`。用户答不出三个回溯性问题：

1. 这批标注是**模型产出的还是 stub 假框**？（默认部署走 stub，事后无从查证）
2. 这个任务跑过几次、每步耗时多少、哪一步失败了？
3. 这个端点最近通不通、延迟多少？

本能力补上跨任务的可回溯性，同时**不因此引入第二个真源**——这是本设计最核心的约束：磁盘
（`tasks/**` 与 JSONL 审计日志）是唯一可信源，SQLite 只是**可随时删除重建**的派生索引。

## 二、功能范围

**包含**

- 三份 append-only JSONL **审计日志落磁盘**：`tasks/<name>/run_history.jsonl`（步骤执行）、
  `tasks/<name>/model_calls.jsonl`（模型调用）、`<data_dir>/logs/diagnostics.jsonl`（自检历史）。
- `src/core/metadata_store.py` 提供 SQLite **派生索引**（`tasks`/`runs`/`model_calls`/`diagnostics`
  四表），内容 100% 可从上述磁盘日志 + `tasks/**` 重建。
- `GET /api/tasks/{name}/history` 读**磁盘 JSONL**（真源在手边，绝不因索引陈旧而失真）；
  `GET /api/index/stats` 读 **SQLite**（跨任务聚合，用于证明索引有效）。
- `run.py index --rebuild` 全量重建；索引写入全部 best-effort。
- `Pipeline.run_step` 与模型调用录制器接入审计写入。

**不包含**

- 不做全文检索、不做日志轮转与归档策略、不做跨主机的集中式日志收集。
- 不引入 ORM / 迁移框架；不做表结构版本迁移链。
- **不改任何既有读路径**：`TaskManager`、`/api/tasks`、`/api/tasks/{name}/*` 一律继续读磁盘。
- 不新增任何写 `ai_labels/` 的路径。
- 不落 prompt 文本、不落图像内容、不落 `api_key`。

## 三、接口契约

### 3.1 HTTP

| 方法 | 路径 | 响应 | 错误 |
|------|------|------|------|
| GET | `/api/tasks/{name}/history` | `TaskHistory` | 404 任务不存在 |
| GET | `/api/index/stats` | `IndexStats` | 503 索引不可用或已停用 |

两者均受鉴权保护（不加入 `_PUBLIC_PATHS`）。

### 3.2 数据结构（字段名即 API/TS 契约，不得增删）

```python
TaskCounts = {image_count: int, ai_label_count: int, candidate_label_count: int,
              has_plan: bool, has_report: bool, has_dataset: bool}

RunEntry  = {step: str, status: str, started_at: str, finished_at: str, duration_ms: int,
             items: int | None, message: str}
            # 与 run_history.jsonl 行字段一致；finished_at 仅存磁盘，索引表不存

CallEntry = {role: str, provider: str, model: str, host: str, status: str,
             http_status: int | None, duration_ms: int, started_at: str}

TaskHistory = {task: str, runs: list[RunEntry], calls: list[CallEntry], source: "jsonl"}
              # 按 started_at 倒序，limit 默认 50、上限 500

IndexStats  = {enabled: bool, available: bool, db_path: str, db_size_bytes: int,
               last_indexed_at: str, tasks: int, runs: int, calls: int, diagnostics: int}
              # 计数为库内行数

RebuildResult = {tasks: int, runs: int, calls: int, diagnostics: int}   # 索引到的行数
```

### 3.3 模块接口

```python
def read_task_history(task_dir: Path, *, task: str, limit: int = 50) -> TaskHistory: ...
    # 模块级函数：只读磁盘 JSONL，不碰 DB，因此 DB 坏掉时历史功能照常

class MetadataStore:
    def __init__(self, db_path: Path, *, enabled: bool = True) -> None: ...
    def available(self) -> bool: ...                 # enabled 且能连上并能建表
    def record_run(self, task, step, status, started_at, duration_ms, items, message) -> None: ...
    def record_model_call(self, task, role, provider, model, base_url, status,
                          http_status, duration_ms, started_at) -> None: ...
    def record_diagnostic(self, report: DiagnosticReport) -> None: ...
    def upsert_task(self, task: str, description: str, counts: TaskCounts) -> None: ...
    def rebuild(self, task_manager: TaskManager, logs_dir: Path) -> RebuildResult: ...
    def stats(self) -> IndexStats: ...               # 不可用时返回 available=False，不抛
    def close(self) -> None: ...
```

- `MetadataStore` **没有 `task_history()`**：store 只持有 `db_path`、没有 `tasks_root`，让它去磁盘找
  任务会把「索引」和「真源」两个角色混在一个对象里。历史读取是磁盘函数 `read_task_history()`，
  API 层用 `TaskManager.task_dir(name)` 定位目录后直接调用。
- `rebuild()` 只收 `task_manager`（它的 `tasks_root` 就是遍历起点）与 `logs_dir`
  （`diagnostics.jsonl` 所在），不再单独收 `tasks_root`——两个入参指向同一处会让调用方有机会传错。

### 3.4 两个读路径的分工（刻意的）

- `GET /api/tasks/{name}/history` → 读**磁盘 JSONL**，因为真源就在手边，绝不因索引陈旧而失真。
  任务不存在 → 404；目录里没有 JSONL（老任务 / 从未运行）→ 空数组，不是错误。
- `GET /api/index/stats` → 读 **SQLite**，返回跨任务聚合计数 + `last_indexed_at` + `db_size_bytes`。
  这是「证明索引有效」的手段：可与 JSONL 行数比对，也可在删库重建前后比对是否一致。
  `available=false`（库损坏 / 目录只读 / `enabled=false`）→ **503**。

## 四、数据模型

### 4.1 磁盘产物（唯一可信源）

| 审计日志（append-only JSONL） | 每行字段 | 写入方 |
|------------------------------|----------|--------|
| `tasks/<name>/run_history.jsonl` | `step`/`status`(`ok`\|`error`)/`started_at`/`finished_at`/`duration_ms`/`items`(int\|null)/`message` | `Pipeline.run_step` |
| `tasks/<name>/model_calls.jsonl` | `role`/`provider`/`model`/`host`/`status`(`ok`\|`error`\|`stub`)/`http_status`(int\|null)/`duration_ms`/`started_at` | `Pipeline` 注入到 agent 的调用录制器 |
| `<data_dir>/logs/diagnostics.jsonl` | **平铺的** `DiagnosticReport`：`generated_at`/`status`/`checks`/`probes` | `Doctor` 调用方 |

- `diagnostics.jsonl` 不把报告再包一层字符串字段——审计日志要能直接 `grep`，且 DB 的 `report_json`
  列存的就是这一行本身。
- JSONL 追加写：单行一次 `write()` + `flush()`，进程内按路径加 `threading.Lock` 串行化。
  **写入 best-effort**：失败只 `logger.warning`，绝不让 `run_step` 或 doctor 失败。
- JSONL 中的 `host` 与库内一致：`scheme://host:port`（剥离 userinfo 与 path）。

### 4.2 SQLite 派生索引（可重建，`index.db`）

位置由 `VL_ANCHOR_DB_PATH` 决定，默认 `<data_dir>/index.db`；由 `config/global.yaml` 的
`database: {enabled, path}` 段配置，缺段时取内置默认（`enabled=true`, `<data_dir>/index.db`）。
`enabled=false` → 索引整体停用（`skip`），平台照常运行。

```sql
CREATE TABLE IF NOT EXISTS tasks (
  name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL, image_count INTEGER NOT NULL DEFAULT 0,
  ai_label_count INTEGER NOT NULL DEFAULT 0, candidate_label_count INTEGER NOT NULL DEFAULT 0,
  has_plan INTEGER NOT NULL DEFAULT 0, has_report INTEGER NOT NULL DEFAULT 0,
  has_dataset INTEGER NOT NULL DEFAULT 0, indexed_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, step TEXT NOT NULL,
  status TEXT NOT NULL, started_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
  items INTEGER, message TEXT NOT NULL DEFAULT '',
  UNIQUE(task, step, started_at));
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task, started_at DESC);

CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, role TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, http_status INTEGER, duration_ms INTEGER NOT NULL,
  started_at TEXT NOT NULL, UNIQUE(task, role, started_at));

CREATE TABLE IF NOT EXISTS diagnostics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL,
  status TEXT NOT NULL, report_json TEXT NOT NULL, UNIQUE(generated_at));

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

- 连接策略：每次操作用短连接 `sqlite3.connect(path, timeout=5.0)`，用完即关。**不跨线程共享连接**，
  因此无需 `check_same_thread=False`。初始化时 `PRAGMA journal_mode=WAL`、`synchronous=NORMAL`、
  `PRAGMA foreign_keys=ON`。
- **外层无外键**：`runs` / `model_calls` / `diagnostics` 均以 `task TEXT` 裸列关联，不建 FK
  ——索引是可丢弃投影，FK 级联只会让「删任务遗留孤儿行」这类问题更难发现，而 `rebuild()` 的
  收敛性由显式清空保证（见边界条件）。
- **隐私**：不落 prompt、不落图像、不落 `api_key`。`host` 只存 `scheme://host:port`（用
  `urllib.parse.urlsplit` 剥离 userinfo 与 path），避免 URL 内嵌凭据泄进库。

### 4.3 `tasks` 表的磁盘统计口径

`upsert_task` 与 `rebuild` **共用同一实现**，否则两处会漂移：

| 列 | 来源 |
|----|------|
| `image_count` | `len(list_images(task_dir / "images"))` |
| `ai_label_count` | `task_dir/"ai_labels"` 下 `*.txt` 个数（含空文件：空标签也是产出的结果） |
| `candidate_label_count` | `task_dir/"candidate_labels"` 下 `*.txt` 个数 |
| `has_plan` | `task_dir/"plan.yaml"` 存在 |
| `has_report` | `task_dir/"inspection_report.yaml"` 存在 |
| `has_dataset` | `task_dir/"dataset"` 是目录 |
| `description` | `task.yaml` 的 `task.description`；缺失/不可读 → `""`（**不**让索引写入失败） |

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 1 OBB 输出格式 | **不触及**。不读写任何坐标值，`ai_label_count` 只数文件个数，不解析内容 |
| 2 标签不可覆盖 | **触及（结论：符合）**。`ai_labels/` 全程**只读**（仅 `glob("*.txt")` 计数）；本能力不新增任何写路径。索引本身不落在 `tasks/<name>/` 的标签目录下 |
| 3 任务规则隔离 | **不触及**。不读类别映射与质检阈值 |
| 4 提示词外置 | **不触及**。不读 `prompts/*.yaml`，也不记录 prompt 文本（隐私约束） |
| 5 前后端职责边界 | **触及，须遵守**。历史读取与聚合全部在 Python 侧完成，前端只渲染；TS 类型向 Pydantic 对齐；前端只经 `services/api.ts` 访问 |
| 6 资源目标 | **触及，须遵守**。SQLite 为标准库，**无新增依赖**、不占显存；`enabled=false` 或无 DB 时流水线行为与今天一致，CPU/4-bit 回退路径不受影响 |
| 7 Python 工程约束 | **触及，须遵守**。全类型注解、禁裸 `Any`、Google-style docstring、ruff+mypy 零错；`src/utils` ← `src/agents` ← `src/core` 的依赖方向不变（`pipeline` 只依赖 `metadata_store` 的实例，不反向） |

## 六、边界条件

- **`rebuild()` 幂等**：连续执行两次，各表行数与 `RebuildResult` 完全一致（依赖**清空 `tasks`/
  `runs`/`model_calls`/`diagnostics` 四张表后按磁盘日志重放**）。
- **`rebuild()` 收敛于磁盘状态**：不得只在 `tasks` 上 `DELETE` 而让其余表依赖 `UNIQUE` +
  `INSERT OR IGNORE` —— 日志行消失（任务被删、JSONL 被截断/轮转）后那些行会成为孤儿，索引便开始
  报告磁盘上已不存在的历史。判据：**不先删库**连续 rebuild 两次，计数必须等于 JSONL 的实际行数
  （「先 `rm index.db` 再 rebuild」会掩盖这一点，因为空库本就无孤儿）。此路径在实现初期不收敛
  （实测旧索引 10 runs / 4 calls ↔ JSONL 4 / 1），已修并加回归用例。
- 删库后 `rebuild()` 能从磁盘完整恢复 `runs`/`calls`/`diagnostics` 计数；`stats` 与 JSONL 行数一致。
- 任务目录无 JSONL（老任务 / 从未运行）→ `history` 返回空数组而非 404/500。
- JSONL 存在畸形行（非 JSON、字段缺失）→ 跳过该行并 `logger.warning`，不影响其余行；文件整体
  不可读 → 视为空历史，不 500。**磁盘读取与索引回放共用同一「必需字段」判定**（run：
  `step`/`started_at`；call：`role`/`started_at`，均要求非空），因此 `read_task_history` 与
  `rebuild` 对「哪些行算记录」永远一致；两处口径不同会让同一个日志在索引里和界面里给出不同的历史。
- **`started_at` 精度为秒**，同一次快速运行的全部步骤会撞在同一秒，故排序以文件行号为次级键
  （日志按时间追加），保证「最新在前」在时钟精度不足时依然成立。
- 索引文件不存在时：父目录存在 → 视作**空索引**（`available=true`、计数为 0，且不建文件）；
  父目录不存在或不是目录（路径本身不可能建立）→ `available=false`，API 返回 503。
  把后者当作「空索引」会用一个 200 + 全 0 掩盖真实故障。
- **读路径不得创建文件**：`available()` / `stats()` 在 `index.db` 不存在时把它当作「空索引」，
  而不是先建库再读。`available()` 只回答「现在能不能读索引」，不保证可写；可写性由 `doctor` 的
  `storage.db` 判定。
- DB 目录只读 / 库损坏 / `sqlite3.Error` → 所有写操作静默降级（仅 warning），**`run_step` 与
  doctor 仍成功、退出码不变**；`GET /api/index/stats` 返回 **503**，其余所有端点不受影响。
- `enabled=false` → 索引不建文件、不写入；`doctor` 的 `storage.db` 为 `skip`；平台功能完整。
- 空 `tasks_root` → `rebuild()` 返回全 0，不报错。
- `base_url` 含凭据（`http://u:p@h:11434/v1`）→ 落库 `host` 为 `http://h:11434`，不含 `u:p`。
- 并发：多个步骤同时写 JSONL/DB 不丢行（`threading.Lock` + `UNIQUE` 幂等兜底）。
- `Pipeline.__init__` 增可选参数 `metadata_store: MetadataStore | None = None`：`None` 时**不写 DB、
  仍写 JSONL**（磁盘是真源，无索引必须是完全可用的运行模式），既有构造方式行为逐字不变。
- 模型调用只在**真的调用发生**时记录：plan 步的 `StubLLMClient.generate` 被调用 → 记
  `status="stub"`；`annotate` 的 `vl_client is None` 内联 stub 路径**不发生调用、因此不产生记录**
  （不伪造行）。当前任务名以 `threading.local` 传给录制器，避免并发任务互相串记录。
- `run_step` 签名与返回类型、异常语义不变（`src/core/pipeline.py:103-135`）；审计写入失败不得把
  一次成功的模型调用变成失败的步骤。

## 七、验收标准

1. `uv run ruff check src/ run.py tests/` 与 `uv run mypy src/` 零错误；`uv run pytest` 全绿
   （含新增 `tests/test_metadata_store.py`）。
2. 跑一次 `run.py full --task demo` 后：`tasks/demo/run_history.jsonl` 有 4 行（plan/annotate/
   inspect/split）、`model_calls.jsonl` 存在；`GET /api/tasks/demo/history` 返回对应条目。
3. `rm <data_dir>/index.db && uv run python run.py index --rebuild` 后，`GET /api/index/stats`
   的 `tasks`/`runs`/`calls` 计数与 `run_history.jsonl` 行数一致；再执行一次 rebuild，计数不变。
   **不先删库**、在旧索引已有更多行的情况下 rebuild，计数同样必须收敛到 JSONL 行数。
4. **删除 `index.db` 后，`/api/tasks`、`/api/tasks/{name}/plan|report|images|labels` 全部行为不变**
   —— 证明 DB 不是任何既有读路径的必要条件。
5. 将 `index.db` 所在目录设为只读后跑 `run.py full --task demo`：步骤仍全部成功、退出码 0，
   仅出现 warning 日志。
6. `config/global.yaml` 设 `database.enabled=false` 后：不生成 `index.db`，`run.py doctor` 的
   `storage.db` 为 `skip`，全流程正常。
7. `grep` 全库确认无 `api_key` 明文、无 prompt 文本、无图像二进制进入 `index.db` 与三份 JSONL；
   含凭据的 `base_url` 落库后不含 userinfo。
8. GUI「质检报告」页展示执行历史；后端返回 503（DB 不可用）时不导致页面崩溃。

## 实现位置

`src/core/metadata_store.py`、`src/core/pipeline.py`（`run_step` 审计）、
`src/agents/model_client.py`（`RecordingClient` / `ModelCallRecord` / `host_of`）、
`src/utils/audit_log.py`（JSONL 追加写）、`src/api_server.py`、
`src/config.py`（`db_path` / `db_enabled`）、`config/global.yaml`（`database:` 段）、
`run.py`（`index --rebuild`）、`gui/src/pages/InspectionReportPage.tsx`、`README.md`。

## 已知缺口

- 验收 8（GUI 展示与 503 降级）只在组件 + 构建层验证，浏览器实测未执行 —— 见
  [`design.md`](design.md) 的「未闭环项」。
- 验收 5 的「POSIX 只读挂载」未在本机（Windows）复现，改用「路径不可能建立」这一等价且更强的
  失败注入（见 design）。
