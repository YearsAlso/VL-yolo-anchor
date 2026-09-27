# Design: metadata-store

> **状态**：已实施（as-built）
> **关联 spec**：[`spec.md`](spec.md)
> **关联 ADR**：—
> **实施栈**：后端（`src/core`、`src/agents`、`src/api_server.py`、`run.py`）+ 前端（质检报告页）
> **契约冻结点**：`TaskHistory` / `CallEntry` / `RunEntry` / `IndexStats` / `RebuildResult` 五个
> 结构 + 两份 JSONL 的行字段 + `index.db` 的列名，同属一份跨栈契约；`CheckResult.id` 不在本能力内
> **最后核对**：2026-09-27
> **来源**：吸收原变更提案 `2026-09-27-config-onboarding/proposal.md` 中元数据层的交付物与验收对照
> （该提案随 SDD 结构改造并入 `specs/platform-config/design.md` 与本文件，`changes/` 中间态已废弃）

## 一、执行摘要

给平台补上跨任务的可回溯性：**三份 append-only JSONL 审计日志落磁盘作为真源**，`index.db` 是其
**可随时删除重建的 SQLite 投影**。核心手段是「先落磁盘、再进库」的写入顺序 + `UNIQUE` 幂等 +
`rebuild()` 全表清空重放。

代价：每次 `run_step` 与每次模型调用都多两次磁盘写（JSONL + DB），且新增一份需要在任务删除后
主动收敛的索引。这两项都用 **best-effort** 化解——写失败只 warning，绝不影响流水线结果与退出码。

## 二、交付物（as-built）

| 类别 | 内容 |
|------|------|
| 审计日志 | `src/utils/audit_log.py`（新）——JSONL 追加写（单行 `write()` + `flush()`、按路径 `threading.Lock`）；`tasks/<name>/{run_history,model_calls}.jsonl`、`<data_dir>/logs/diagnostics.jsonl` |
| 索引 | `src/core/metadata_store.py`（新）——四表 SQLite 派生索引、`_SCHEMA`、`rebuild()`、`stats()`、`read_task_history()`、`host_of()` |
| 接线 | `src/core/pipeline.py` 的 `run_step` 记 run 审计 + `index_task`；`src/agents/model_client.py` 增 `RecordingClient` / `ModelCallRecord` / `host_of()`，`OpenAICompatClient` 增 `last_http_status`；`StubLLMClient.generate` 接受并忽略 `image_path` |
| API | `GET /api/tasks/{name}/history`（读磁盘）、`GET /api/index/stats`（读 SQLite，503 语义） |
| CLI | `run.py index --rebuild` |
| 配置 | `config/global.yaml` 增 `database: {enabled, path}`；`.env.example` 增 `VL_ANCHOR_DB_PATH`；`src/config.py` 的 `Settings` 增 `db_path` / `db_enabled` |
| GUI | `gui/src/pages/InspectionReportPage.tsx` 追加「执行历史」区块 |
| 测试 | `tests/test_metadata_store.py`（新，23 例）；`tests/test_pipeline.py` / `test_api_server.py` 增用例 |

## 三、关键设计决策

| 决策 | 选项 | 选择 | 理由 | 被否选项的代价 |
|------|------|------|------|---------------|
| 真源 | SQLite / 磁盘 JSONL | **磁盘 JSONL 是唯一真源，DB 只是索引** | 满足「磁盘是唯一可信源」的项目基线；`index.db` 可在任何时候删除并由 `rebuild` 完整重建 | DB 当真源 ⇒ 引入第二个真源，与 `tasks/**` 必然漂移，且删库即丢历史 |
| DB 的必要性 | 读路径改读 DB / 一律继续读磁盘 | **所有既有读路径继续读磁盘** | 让「索引坏了」退化为「没有历史查询」，而不是「平台不可用」 | 改读 DB ⇒ DB 成为单点，与上一行的真源约定自相矛盾 |
| 索引写失败 | 抛异常 / best-effort | **只 `logger.warning` 后返回，签名不抛** | `run_step` 的成败不该由索引决定；只读目录、库损坏都必须是可运行状态 | 抛异常 ⇒ 一次磁盘配额问题让整条流水线失败 |
| `stats()` 的失败表达 | 抛异常 / 返回值 | **返回 `available=false` 的零值对象，由 API 层映射 503** | 让「索引坏了」成为**返回值**而不是异常，API 层才不会漏掉某个调用点忘记 try | 抛异常 ⇒ 每加一个调用点就多一次漏 try 的机会 |
| 重建幂等手段 | 只靠 `UNIQUE` + `INSERT OR IGNORE` / 先清空四表再重放 | **清空 `tasks`/`runs`/`model_calls`/`diagnostics` 后重放** | `UNIQUE` 只能防重复插入，防不住**孤儿行**：日志行消失后旧行仍在，索引会报告磁盘上已不存在的历史 | 只靠 UNIQUE ⇒ 不删库的 rebuild 不收敛（实测 10/4 ↔ 4/1），实现初期即踩中 |
| 历史读取放哪 | `MetadataStore.task_history()` / 模块级 `read_task_history()` | **模块级函数，只读磁盘** | store 只持有 `db_path`、没有 `tasks_root`；让索引对象去找磁盘会把「索引」与「真源」两个角色混在一起，也让 DB 坏掉时历史功能一并失效 | 放进 store ⇒ DB 不可用即历史不可用，与「DB 非必要」冲突 |
| `rebuild()` 入参 | `tasks_root` + `logs_dir` / `task_manager` + `logs_dir` | **后者** | `task_manager.tasks_root` 就是遍历起点；两个入参指向同一处会给调用方传错的机会 | 分开传 ⇒ 两次遍历不同根，产生无法解释的计数 |
| 统计口径 | `upsert_task` 与 `rebuild` 各写一份 / 共用实现 | **共用同一实现** | 两处口径必然漂移（一个数空标签文件、一个不数），而 `stats` 正是用来对账的 | 各写一份 ⇒ 对账时无法判断哪个数才对 |
| `host` 存法 | 原样存 `base_url` / 剥离 userinfo 与 path | **`scheme://host:port`** | `http://u:p@h:11434/v1` 里内嵌着凭据，原样落库等于把密钥写进索引 | 原样 ⇒ 凭据泄漏进 `index.db` 与 `model_calls.jsonl` |
| stub 调用是否记录 | 不记 / 记 `status="stub"` | **记** | 默认部署走 stub，`StubLLMClient.generate` **真的被调用了**；漏掉它，`model_calls.jsonl` 在默认部署下是空文件，而这个文件存在的意义恰恰是回答「这批标注是模型产出的吗」 | 不记 ⇒ 最常见的部署形态下审计日志为空 |
| 内联 stub 是否伪造一行 | 补一行 `stub` / 不产生记录 | **不产生记录** | `annotate` 的 `vl_client is None` 路径没有发生任何「调用」，为它编造一行等于往审计日志里写假数据 | 伪造 ⇒ 审计日志失去可信度 |
| 当前任务名怎么传 | 实例属性 / `threading.local` | **`threading.local`** | agent 与其客户端在 `__init__` 时就构造好了，而任务名要到 `run_step` 才知道；实例属性会在 FastAPI 线程池里把并发任务的记录串到彼此头上（`model_calls.task` 就错了） | 实例属性 ⇒ 并发下记录归属错乱 |
| 连接策略 | 长连接共享 / 短连接 | **短连接 `timeout=5.0`，用完即关** | 不跨线程共享连接，因此无需 `check_same_thread=False`；WAL 让并发写不互相阻塞 | 共享连接 ⇒ 线程安全与事务边界问题成倍 |
| 外键 | 建 FK / 不建 | **不建** | `runs` / `model_calls` / `diagnostics` 均以 `task TEXT` 裸列关联；索引是可丢弃投影，FK 级联只会让「删任务遗留孤儿行」更难发现——而收敛性由显式清空保证 | 建 FK ⇒ 把可丢弃投影当成有完整性契约的库 |
| 深度自检是否写诊断日志 | 浅层也写 / 只在 `deep=true` 写 | **只在 `deep=true`** | 浅层自检每小时被 GUI 轮询几十次，写进去的只会是噪音，并让「审计日志」变成运行日志 | 都写 ⇒ 审计日志被轮询噪音淹没 |
| `diagnostics.jsonl` 的包裹层次 | `DiagnosticReport` 再包一层字符串 / 平铺 | **平铺** | 审计日志要能直接 `grep`；DB 的 `report_json` 列存的就是这一行本身 | 再包一层 ⇒ `grep` 需要两次解析，DB 列语义也变模糊 |

## 四、数据流与拓扑

```
磁盘（唯一可信源）
  tasks/<name>/{task.yaml, plan.yaml, inspection_report.yaml, ai_labels, candidate_labels, dataset}
  tasks/<name>/run_history.jsonl       ← 新增，append-only（步骤执行）
  tasks/<name>/model_calls.jsonl       ← 新增，append-only（模型调用）
  <data_dir>/logs/diagnostics.jsonl    ← 新增，append-only（doctor 历史）
        │
        │ 纯索引（可随时删库重建，绝不反向成为真源）
        ▼
  <data_dir>/index.db  (SQLite, WAL)   ← 新增
```

**依赖方向**：`api_server` / `run.py` → `metadata_store` → (`task_manager`, `utils`)。三个新模块
之间无相互依赖。`pipeline` 只持有 `MetadataStore` **实例**（可选，默认 `None`），不 import 具体实现
之外的东西。

## 五、写入侧设计

### 5.1 核心原则：审计日志落磁盘，SQLite 只是索引

为严格满足「磁盘是唯一可信源、DB 可随时重建」，**所有被索引的数据都先落磁盘**，DB 内容 100% 可
从磁盘重建。写入顺序因此固定：**先追加 JSONL，再写 DB**——反过来会出现「库里有一行、磁盘上没有」
的不可恢复状态。

| 审计日志（磁盘，append-only JSONL） | 内容 | 写入方 |
|-------------------------------------|------|--------|
| `tasks/<name>/run_history.jsonl` | 每次 `run_step`：step/status/起止/duration_ms/items/message | `Pipeline.run_step` |
| `tasks/<name>/model_calls.jsonl` | 每次带任务的模型调用：role/provider/model/host/status/http_status/duration_ms | `Pipeline` 注入到 agent 的调用录制器 |
| `<data_dir>/logs/diagnostics.jsonl` | 每次 doctor 的完整 `DiagnosticReport` | `Doctor` 调用方 |

JSONL 追加写：单行一次 `write()` + `flush()`，进程内按路径加 `threading.Lock` 串行化。
**写入 best-effort**：失败只 `logger.warning`，绝不让 `run_step` 或 doctor 失败。

### 5.2 Pipeline 的审计接线

`Pipeline.__init__` 增加可选参数 `metadata_store: MetadataStore | None = None`：`None` 时**不写
DB、仍写 JSONL**——磁盘是真源，DB 只是可丢的投影，因此「没有索引」必须是完全可用的运行模式
（`tests/test_pipeline.py` 等既有构造方式因此无需改动，行为逐字不变）。

- `run_step`：进入时记 `started_at`（UTC ISO8601）与 `perf_counter`，`try/except/finally` 包住
  **含 `save_task_config` 在内的整段**，`finally` 里写 `run_history.jsonl` + `record_run` +
  `index_task`（后者 = 磁盘计数 + `upsert_task`，两处共用同一实现以免口径漂移）。`status` 取
  `ok`/`error`，`message` 为异常文本（`ok` 时 `""`），**异常照原样抛出**（签名与异常语义不变）。
  `items` 口径：`annotate` → 写出的标签数、`inspect` → 问题总数、`plan`/`split` → `None`。
  `run_history.jsonl` 的行由 `RunEntry` 序列化而来，字段即 spec 契约，不手写字面量 dict。
- **模型调用录制**：`RecordingClient`（`src/agents/model_client.py`）包住 agent 实际持有的客户端，
  实现与 `VLClient` 相同的 `generate` 签名并把每次调用上报给 hook。之所以要包装器而不只依赖
  `OpenAICompatClient` 内部钩子：默认部署走 stub，plan 步的 `StubLLMClient.generate` **真的被调用
  了**，把它记成 `status="stub"` 才能让「这次运行的框是假的」在事后仍可查。一个包装器同时服务两种
  角色，因此 `StubLLMClient.generate` 也接受并忽略 `image_path`。
- `http_status` 由 `OpenAICompatClient.last_http_status` 提供（每次 `generate` 重置，收到响应即写入，
  连不上服务器则为 `None`），包装器用 `getattr(inner, "last_http_status", None)` 读取：只有 HTTP
  客户端知道状态码，只有录制器知道归属，与其把两者揉在一起，不如让各自只回答自己知道的部分。
- `annotate` 的 stub 路径（`vl_client is None`）**不产生记录**（理由见决策表）。
- hook（`Pipeline._record_model_call`）自身也是 best-effort：`RecordingClient` 在调用 hook 时再包一层
  `try/except`，保证审计写失败绝不会把一次成功的模型调用变成失败的步骤（store 内部已不抛异常，
  这层是防止 hook 里 `append_record` 之外的意外）。
- **当前任务用 `threading.local` 存放**（理由见决策表）。
- `host` 一律经 `host_of(base_url)`（`urlsplit` → `scheme://host:port`）**剥离 userinfo 与 path**：
  `http://u:p@h:11434/v1` 落库只能是 `http://h:11434`。记录中不含 prompt、图像与 `api_key`。

## 六、读路径设计

### 6.1 两个读路径的分工（刻意的）

- `GET /api/tasks/{name}/history` → 读**磁盘 JSONL**（模块级 `read_task_history(task_dir, task=…,
  limit=…)`），因为真源就在手边，绝不因索引陈旧而失真。任务不存在 → 404；目录里没有 JSONL
  （老任务 / 从未运行）→ 空数组，不是错误。
- `GET /api/index/stats` → 读 **SQLite**，返回跨任务聚合计数 + `last_indexed_at` + `db_size_bytes`。
  这是「证明索引有效」的手段：可与 JSONL 行数比对，也可在删库重建前后比对是否一致。
  `available=false`（库损坏 / 目录只读 / `enabled=false`）→ **503**。

### 6.2 「哪些行算记录」的判定必须唯一

磁盘读取与索引回放**共用同一必需字段判定**（run：`step`/`started_at`；call：`role`/`started_at`，
均要求非空）。两处口径不同会让同一个日志在索引里和界面里给出不同的历史，而这正是对账功能最不能
容忍的行为。

排序以 `started_at` 为主键、**文件行号为次级键**：`started_at` 精度为秒，同一次快速运行的全部步骤
会撞在同一秒，没有次级键时「最新在前」在秒级时钟下不成立（日志按时间追加，行号即真实顺序）。

## 七、重建语义

`rebuild()`：清空四张数据表（含 `sqlite_sequence`）→ 遍历 `task_manager.list_tasks()` 从磁盘统计并
重写 `tasks`；把三个 JSONL 全量重放（`INSERT OR IGNORE`）进 `runs`/`model_calls`/`diagnostics`。
返回 `RebuildResult(tasks=…, runs=…, calls=…, diagnostics=…)`。

**清空是关键**：`UNIQUE` + `INSERT OR IGNORE` 只能防重复插入，防不住孤儿行。实现初期只
`DELETE FROM tasks`，实测旧索引 10 runs / 4 calls ↔ JSONL 4 / 1——索引报告了磁盘上已不存在的历史。
修法是在 `_connect()` 内一次性清空四表并重置自增序列，使「在旧库上 rebuild」与「删库后 rebuild」
收敛到同一结果。回归用例 `test_rebuild_drops_rows_whose_log_lines_are_gone` 钉住该行为。

## 八、既有文件改动与兼容性

| 文件 | 改动 | 兼容性 |
|------|------|--------|
| `src/core/pipeline.py` | `run_step` 记录 run 审计（磁盘 JSONL + 索引 upsert）；`__init__` 增可选 `metadata_store` | 返回类型 / 异常语义不变；不传 `metadata_store` 时行为与改造前逐字一致 |
| `src/agents/model_client.py` | 增 `RecordingClient` / `ModelCallRecord` / `host_of()`；`OpenAICompatClient` 增 `last_http_status`；`StubLLMClient.generate` 接受并忽略 `image_path` | 不包装时行为不变 |
| `src/config.py` | `Settings` 增 `db_path` / `db_enabled`（读 `database:` 段） | 缺段时用内置默认 |
| `src/api_server.py` | 2 个新端点 | 现有 14 个端点契约不变 |
| `config/global.yaml` | 增 `database: {enabled, path}` 段 | 缺该段时用内置默认（`enabled=true`, `<data_dir>/index.db`） |

## 九、测试策略

`tests/test_metadata_store.py`（新，**23 例**）：

- 建表幂等；`rebuild` 幂等（跑两次行数不变）。
- 删库后 `rebuild` 能从 JSONL 完整恢复 runs/calls 计数；`stats` 与 JSONL 行数一致。
- **不先删库**、旧索引残留更多行时 rebuild 仍收敛到 JSONL 行数
  （`test_rebuild_drops_rows_whose_log_lines_are_gone`）。
- 只读目录下 `record_*` 不抛异常；库无法打开时写入降级
  （`test_writes_degrade_when_db_cannot_be_opened`）。
- `host` 剥离 userinfo（`http://u:p@h:1/v1` → `http://h:1`）。
- 隐私：全表 dump 文本搜不到 `api_key` / `Bearer` / `Authorization` / `system_prompt` /
  `user_prompt` / PNG 魔数。
- `enabled=false` → 不建文件、不写入。
- 索引文件不存在但父目录存在 → `available=true`、计数 0、**不创建文件**；父路径不可能建立 →
  `available=false`。
- 畸形行跳过、文件不可读视为空历史；`read_task_history` 与 `rebuild` 口径一致。

`tests/test_pipeline.py` / `tests/test_api_server.py`（改）：`run_step` 写 JSONL 的字段、
`/api/tasks/{name}/history` 的 404 与空数组路径、`/api/index/stats` 的 503 路径。

## 十、基线复跑

| 命令 | 结果 |
|------|------|
| `uv run ruff check src/ run.py tests/` | All checks passed |
| `uv run mypy src/` | Success，22 files |
| `uv run pytest -q` | **203 passed**（改动前基线 **99 passed** ⇒ 净增 104 例，无回归） |
| `cd gui && npx tsc --noEmit` | 0 错误 |
| `cd gui && npm run build` | 3055 modules（14.30s），ExitCode 0 |

反向验证（**DB 非必要**）：删除 `index.db` 后 9 个既有端点响应逐字节相同，且读路径不重建库文件。

## 十一、验收对照

`spec.md` 八条验收标准的逐条证据：

- [x] **1** ruff / mypy / pytest 全绿（203 passed）。
- [x] **2** `run.py full --task demo` → `run_history.jsonl` **4 行**（plan/annotate/inspect/split，
  `items` = null/8/0/null）、`model_calls.jsonl` **1 行**（`role=llm`、`status=stub`——annotate 的
  内联 stub 不产生记录）；`GET /api/tasks/demo/history` 返回对应条目。
- [x] **3** `rm index.db && run.py index --rebuild` → `tasks=1 runs=4 calls=1 diagnostics=0`，与 JSONL
  行数一致；再执行一次计数不变。**另补测「不先删库」**：旧索引残留 10 runs / 4 calls 时 rebuild
  也必须收敛到 4 / 1 —— 此路径原先不收敛，已修并加回归用例，spec 边界条件同步补写该判据。
- [x] **4** 删除 `index.db` 后 `/api/tasks`、`/api/tasks/{name}/plan|report|images|labels` 等
  9 个端点响应**逐字节相同**，且读路径不重建库文件。
- [x] **5** 库无法打开时（`VL_ANCHOR_DB_PATH` 的父路径是一个文件）跑 `run.py full --task demo`：
  4 步全部成功、**退出码 0**、仅出现 warning；JSONL 仍写满 4 + 1 行，库文件未生成。用例
  `test_writes_degrade_when_db_cannot_be_opened` 覆盖同类失败。*注*：POSIX 只读挂载未在本机
  （Windows）复现，改用「路径不可能建立」这一等价且更强的失败注入（见「未闭环项」）。
- [x] **6** `database.enabled=false` → 不建文件、不写入、`doctor` 的 `storage.db` 为 `skip`、平台
  功能完整（`tests/test_metadata_store.py` / `tests/test_diagnostics.py`）。
- [x] **7** 把 `index.db` 全表 dump 成文本后搜不到 `api_key` / `Bearer` / `Authorization` /
  `system_prompt` / `user_prompt` / PNG 魔数；两份 JSONL 同样干净。含凭据的 `base_url` 落库后
  `host` 不含 userinfo（`tests/test_metadata_store.py`）。
- [ ] **8（GUI 半边）**「质检报告」页展示执行历史、503 时不崩溃 —— 代码与构建通过，
  **浏览器实测未执行**。

## 十二、未闭环项（诚实登记）

| 项 | 状态 | 说明 |
|----|------|------|
| 验收 8 的**浏览器半边** | ⚠️ open | 执行历史区块渲染与 503 降级只在组件 + `tsc`/`build` 层验证；本机无浏览器自动化环境 |
| 验收 5 的 **POSIX 只读挂载** | ⚠️ 等价复现 | 本机为 Windows，无法复现只读挂载；改用「路径不可能建立」的失败注入（更强：连 `connect` 都会失败）。CI 上可用 `chmod -w` 目录补验 |
| 日志**轮转** | open | JSONL 无轮转/上限策略，长期运行会持续增长；`rebuild` 已能正确处理截断（清空重放），但磁盘占用无治理 |
| 跨任务**删除**时索引收敛 | open | 任务被删除后需手动 `index --rebuild` 才会从 `tasks` 表消失；未做删除钩子（属有意的保守选择：删库重建成本极低） |

## 十三、风险与回退

| 风险 | 等级 | 缓解 |
|------|------|------|
| SQLite 在 Docker 卷上并发/损坏 | 🟡 | WAL + 短连接 + `UNIQUE` 幂等 + 全部写操作 best-effort；`rebuild()` 显式清空四表保证收敛；删库不影响任何流水线 |
| 索引写放大拖慢流水线 | 🟢 | 每次 `run_step` 一次 JSONL 追加 + 若干次短连接写；实测 203 例测试套件无超时（单文件 ≤10s 约束保持） |
| 审计日志泄进 prompt / 图像 / 凭据 | 🟡 | `host` 剥离 userinfo；不记 prompt 与图像；验收 7 以全表 dump 文本扫描钉住 |
| 索引成为隐性真源 | 🟢 | 所有既有读路径继续读磁盘；验收 4 逐字节比对响应证明 DB 非必要 |
| 日志无限增长 | 🟡 | 已登记为未闭环项；当前靠外部磁盘管理 |

**回退**：删除 `index.db` 与三份 JSONL 即回到改造前的行为；两个新端点为纯增量；`Pipeline` 不传
`metadata_store` 时行为逐字不变。

## 附录：关键位置速查

| 位置 | 用途 |
|------|------|
| `src/core/metadata_store.py` `_SCHEMA` | 四表 + `idx_runs_task` 索引定义 |
| `src/core/metadata_store.py` `rebuild()` | 清空四表 + `sqlite_sequence` 后重放（收敛性修复点） |
| `src/core/metadata_store.py` `read_task_history()` | 模块级磁盘读取（不依赖 DB） |
| `src/core/metadata_store.py` `host_of()` | `urlsplit` → `scheme://host:port`（凭据剥离） |
| `src/core/pipeline.py` `run_step` | `try/except/finally` 包整段；`finally` 写 JSONL + 索引 |
| `src/core/pipeline.py` `_record_model_call` | 模型调用 hook（best-effort 双层兜底） |
| `src/agents/model_client.py` `RecordingClient` | 包住真实客户端并把调用上报（含 stub） |
| `src/agents/model_client.py` `last_http_status` | 状态码来源（只有 HTTP 客户端知道） |
| `src/api_server.py` | 两个新端点与 503 映射 |
| `config/global.yaml` `database:` | `enabled` / `path` |
