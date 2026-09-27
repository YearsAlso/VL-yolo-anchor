# Spec: platform-config（初始化引导：配置读取/写回、密钥加密落盘、连通性探测、doctor 自检）

> **状态**：已实施（as-built，从 `specs/platform-config.spec.md` 迁移并重组为六要素）
> **设计**：[`design.md`](design.md)（吸收了原 `2026-09-27-config-onboarding` 提案的交付物与验收对照）
> **实施栈**：后端 + 前端（跨栈，契约冻结点见 design）
> **最后核对**：2026-09-27

## 一、业务背景

平台默认 `provider: stub`（离线确定性假框），但**首次使用者无从知道**：界面上画出来的框看起来
是真的，模型端点是否配通没有任何可见反馈，`api_key` 该怎么填也没有指导。现场反复出现同一类问题
——「框画出来了，但质量不对」「改了 `.env` 没反应（其实是被环境变量锁住了）」「把 key 填进
`global.yaml` 后连同文件一起提交了」。

本能力要解决的正是这三件事：

1. **可见**：把「当前生效的模型配置及其来源」摊开给用户看，并明确标注「现在是假模型」。
2. **可配**：在 UI 内改掉未被环境变量锁定的字段，且**密钥以密文落盘**（用户硬性要求：绝不
   明文写入任何文件）。
3. **可诊断**：一条命令 / 一个面板回答「哪里没配好、怎么修」，并给出机器可读的退出码供脚本使用。

## 二、功能范围

**包含**

- `src/core/config_store.py`：读取生效配置并标注**每个字段的来源**（env / overrides / yaml /
  secrets / default）与**是否被环境变量锁定**；在未被锁定时把非密字段写回 `config/overrides.yaml`。
- `src/utils/secrets.py`：`read_secret()`（env 与 `*_FILE` 间接）与 `SecretStore`
  （`config/secrets.db` 中的 Fernet 密文），使密钥可经设置页落盘而磁盘上无明文。
- `src/core/diagnostics.py` + `src/agents/model_client.probe_endpoint()`：配置完整性、存储可写性、
  提示词完整性、明文密钥与密文库可用性检查，以及 llm/vl 两角色的端点连通性探测。
- HTTP：`GET/PUT /api/config`、`POST /api/config/test`、`GET /api/diagnostics`。
- CLI：`run.py doctor [--deep] [--json]`、`run.py secret-keygen`、`run.py secret-set NAME`。
- GUI：第 5 个「设置」Tab、`provider=stub` 常驻警告 banner、`Authorization` 头注入。

**不包含**

- 不引入 ORM / 数据库迁移框架（密钥库只用标准库 `sqlite3`，不建表版本链）。
- 不改 VL/LLM 推理实现、不改 OBB 数据格式、不新增任何写 `ai_labels/` 的路径。
- 不改 `/api/health` 的响应契约（`specs/deployment/spec.md` 已固化）。
- 不做密钥托管/轮换编排：本能力只提供 `secret-keygen` 打印密钥 + env 注入，**不**自动生成密钥文件。
- 不含用户账号体系：鉴权仍是单 token。

## 三、接口契约

### 3.1 HTTP（全部受鉴权保护，**不**加入 `_PUBLIC_PATHS`）

| 方法 | 路径 | 响应 | 错误 |
|------|------|------|------|
| GET | `/api/config` | `EffectiveConfig` | — |
| PUT | `/api/config` | `WriteResult` | 422 校验失败 / 409 无任何字段可写 |
| POST | `/api/config/test` | `ProbeResult` | 404 未知角色 / 422 缺 base_url / 503 |
| GET | `/api/diagnostics?deep=false` | `DiagnosticReport` | — |

`CORSMiddleware.allow_methods` 必须包含 `PUT`（否则浏览器预检失败，`PUT` 根本发不出去）。

**`EffectiveConfig`**：`llm`/`vl` 两个 `RoleView`（每个含 `provider`/`base_url`/`model`/`timeout_s`
的 `FieldView`）、`storage`、`server`、`inference`、`secrets`。

```
FieldView  = {value, source: "env"|"overrides"|"yaml"|"secrets"|"default",
              locked: bool, locked_by: str, editable: bool}
SecretsView= {available: bool, reason: str, master_key_source: str, store_path: str}
```

- **`api_key` 恒不回明文**：其 `FieldView.value` 恒为 `null`，另出 `has_api_key: bool` 与
  `api_key_masked: str`（形如 `****3f2a`，key 长度 < 4 时为空串）；`api_key.source == "env"`
  时 `locked=true`（环境变量优先于密文库，改库无效）。
- `secrets.available=false` 时 `reason` 说明是「未配置主密钥」还是「主密钥无法解密现有密文」，
  GUI 据此禁用密钥输入框；`master_key_source` 为命中的变量名（**不是值**）。
- `RoleView.api_key.editable` 当且仅当 `not locked and secrets.available and config_writable`。
- `storage: {config_dir, config_writable, config_persistent, tasks_root, prompts_dir,
  overrides_path, db_enabled, db_path}`。

**`WriteResult`**：`{written: {role: {field: value}}, ignored: [{role, field,
reason: "env_locked"|"read_only"|"not_writable"|"no_secret_store"}], overrides_path,
restart_required: bool}`。

- 写 YAML 为**原子替换**（临时文件 + `os.replace`），合并保留未提交的既有键；写密文库为
  `INSERT OR REPLACE`。
- `api_key` 字段在 `written` 中恒以 `"***"` 回显，**绝不回明文**。
- `restart_required = bool(written)`（`Settings` 经 `lru_cache` 缓存、启动时求值，故只要有字段
  落盘即为 `true`，GUI 据此提示「需重启后端生效」）；`written` 为空（409 路径）时为 `false`
  —— 一个字段都没写就没有「重启才生效」可言，报 `true` 只会让用户为一个未发生的变更去重启后端。

**`ProbeResult`**：`{role, status, latency_ms, http_status, message}`；探测发**单个极短纯文本请求**
（`max_tokens=1`，不携带图像），`max_retries=0`，超时取 `min(timeout_s, 15s)`。

**`DiagnosticReport`**：`{status, generated_at, checks, probes}`；`status` 为聚合结果，优先级
`fail > warn > ok > skip`。检查项 id 为稳定契约：

```
config.{role}.provider / base_url / model / api_key
config.paths.config_writable / config_persistent / overrides_plaintext_secret / secret_store
config.server.auth_token
storage.tasks_root / storage.prompts / storage.db
model.{role}.connectivity
```

每个 `CheckResult` 含 `id/status/summary/detail/hint`（`hint` 为可操作的中文修复建议）。

默认 `deep=false`：只做本地检查，**不产生任何网络调用**，也不写诊断日志；`deep=true` 才探测
并追加 `<data_dir>/logs/diagnostics.jsonl`。

### 3.2 CLI（`run.py`）

| 命令 | 语义 |
|------|------|
| `doctor [--deep] [--json]` | 自检。退出码：存在任一 `fail` → **1**；否则（含仅 `warn`）→ **0**。`--json` 输出 `DiagnosticReport` |
| `index --rebuild` | 见 [`specs/metadata-store/spec.md`](../metadata-store/spec.md) |
| `secret-keygen` | **只把新生成的 Fernet 主密钥打到 stdout**（附一行使用说明），**不写任何文件、不修改环境** |
| `secret-set NAME` | 从 **stdin** 读一行值写入密文库；`NAME ∈ {VL_ANCHOR_AUTH_TOKEN, VL_LLM_API_KEY, VL_VL_API_KEY, VL_MODEL_API_KEY}`，其他名字退出 2；空行 = 删除该键；无主密钥 → 退出 1 并提示；成功与失败都**不回显值** |

`plan|annotate|inspect|split|full` 仍要求 `--task`；`doctor` / `index` / `secret-keygen` /
`secret-set` 下 `--task` **非必需**，缺失时不得报错。

### 3.3 GUI

- `gui/src/services/api.ts` 请求拦截器注入 `Authorization: Bearer`（`VITE_AUTH_TOKEN` 构建期 →
  localStorage `vl_auth_token`），401 抛出可识别错误。
- `gui/src/types/index.ts` 新增与后端 Pydantic 模型**一一对应**的 TS 接口（**Pydantic 是唯一事实源**，
  TS 向它对齐；跨栈契约变更必须同批改齐 `api_server.py` + `types/index.ts` + `api.ts`）。
- 设置页：每字段显示来源徽标与 🔒 变量名；密钥框恒为密码型空输入；「测试连接」按角色单独触发。

## 四、数据模型

### 4.1 配置优先级（相对现状新增 `overrides.yaml` 一层）

```
base = VL_MODEL_* env > overrides.model.*        > global.yaml model.*        > 内置默认
role = VL_{LLM,VL}_* env > overrides.model.{role}.* > global.yaml model.{role}.* > base
```

**空字符串环境变量视为未设置**，与 `src/config.py` 的 `_env()` 语义逐字一致；因此
`VL_VL_PROVIDER=""` 不构成锁定。

| 命中层 | `source` | `locked` |
|--------|----------|----------|
| `VL_{ROLE}_*` 或 `VL_MODEL_*`（值非空） | `env` | `true` |
| `overrides.yaml` | `overrides` | `false` |
| `global.yaml` | `yaml` | `false` |
| 无 | `default` | `false` |

### 4.2 磁盘产物

| 路径 | 可写性 | 说明 |
|------|--------|------|
| `config/global.yaml` | **只读**（系统永不写入） | 用 `yaml.safe_dump` 往返会抹掉其全部注释与注释掉的配置示例（该文件 69 行里大半是文档），故写回目标另立文件 |
| `config/overrides.yaml` | 设置页写回目标 | 列入 `.gitignore`（与 `.env` 同类）。**恢复默认 = 删除该文件** |
| `config/secrets.db` | 仅 Fernet 密文 | 独立于可重建的 `index.db`，不参与 `rebuild()`、永不被其删除 |

`config/secrets.db` 的 schema（标准库 `sqlite3`，短连接 + `journal_mode=WAL`）：

```sql
secrets(name TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, updated_at TEXT NOT NULL);
meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- 存 key_check 哨兵
```

`key_check` 是一条用当前主密钥加密的常量；启动时试解密它，即可区分 `NO_MASTER_KEY`（未配）与
`BAD_MASTER_KEY`（配了但换了密钥、现有密文全部读不出）。没有哨兵就只能把「解不开」误报成「没配」。

### 4.3 密钥写入与解析（硬约束）

**原则**：密钥可以落盘，但只以密文落盘；主密钥与密文分离存放，且主密钥永不自行生成到磁盘。

| 位置 | 是否允许 api_key 明文 |
|------|----------------------|
| 环境变量 / `*_FILE` 挂载文件 | ✅ 唯一推荐的部署方式 |
| `config/secrets.db` | 仅 Fernet 密文 |
| `config/global.yaml` / `config/overrides.yaml` | ❌ 被忽略并告警（文件受版本控制） |
| `<data_dir>/index.db` | ❌ 从不写入 |
| 日志（含 `model_calls.jsonl`、`run_history.jsonl`） | ❌ 恒为 `***` |
| HTTP 响应 | ❌ 只有 `has_api_key` + `api_key_masked`（末 4 位） |

解析优先级：`VL_{ROLE}_API_KEY[_FILE]` > `VL_MODEL_API_KEY[_FILE]` > `secrets.db[role]` >
`secrets.db[shared]` > 空。env 命中即 `locked=true`。

- `*_FILE` 变体（如 `VL_VL_API_KEY_FILE=/run/secrets/vl_key`）**优先于**直接变量，用于 Docker/K8s
  secret；内容按 UTF-8 读、去 BOM 与首尾空白。
- **按名字取值的通用库**：`get`/`set` 以名字为键，键名即被替代的环境变量名，因此「哪一层命中了」
  可直接由名字推出，无需额外元数据。
- 主密钥只从环境读取：`VL_ANCHOR_SECRET_KEY_FILE`（推荐）或 `VL_ANCHOR_SECRET_KEY`，必须是合法
  Fernet key。**主密钥不得来自 `secrets.db` 自身或同目录自动生成文件**——那等价于混淆而非加密。
- **鉴权 token 的写入路径只开 CLI**：`PUT /api/config` 不接受该键。因为一旦客户端的 token 丢失
  （清 localStorage、换机器），全部 API 都会 401，而解锁只能靠改 env 或删除 `secrets.db`；
  写入路径越窄，误配概率越低。
- **无主密钥时优雅降级**：平台照常以 env 密钥运行，`config/secrets.db` **不创建**，设置页密钥框
  禁用并说明原因。密文库是便利层，不是启动的必要条件。

### 4.4 `config_persistent`（容器内持久性判定）

`Dockerfile` 用 `COPY --chown=app:app config ./config` 把配置烤进镜像层，因此**容器内
`/app/config` 是可写的**（`os.access` 返回 True），设置页会允许一次「保存成功」的写入，而容器
重建即丢失——用户以为改了、实际没改，比直接报错更糟。判定：

```python
def _config_persistent(config_dir: Path) -> bool:
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not in_container:
        return True                       # 裸机/虚拟机：目录就是真实文件系统
    parent = config_dir.parent
    return config_dir.stat().st_dev != parent.stat().st_dev   # 设备号不同 = 是挂载点
```

不持久时 `config.paths.config_persistent` 为 `warn`，GUI 在保存按钮上方给出常驻提示。
**不**因此禁止保存——本地 `docker run` 无挂载时，用户仍可能只想临时改一下并接受重建后丢失。

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 1 OBB 输出格式 | **不触及**。本能力不产生、不解析、不转换任何标注坐标 |
| 2 标签不可覆盖 | **不触及（结论：符合）**。本能力不新增任何写路径；`ai_labels/` 与 `candidate_labels/` 均不涉及。`secret-set` / `PUT /api/config` 只写 `config/` 下的文件 |
| 3 任务规则隔离 | **不触及**。不读任务级类别映射与质检阈值 |
| 4 提示词外置 | **不触及**。`prompts/*.yaml` 与其占位符集合未变；doctor 的 `storage.prompts` 只**检查存在性**与 `system_prompt` 键是否存在，不改内容 |
| 5 前后端职责边界 | **触及，须遵守**。新端点的 Pydantic 模型为唯一事实源，TS 接口向它对齐；栈锁 Tauri+React+TS+AntD 不变；前端只经 `gui/src/services/api.ts` 访问后端，全部 IO 留在 Python 侧 |
| 6 资源目标 | **触及，须遵守**。探测为单次极短请求（`max_tokens=1`、无图像、`max_retries=0`、超时 ≤15s），8GB/CPU 亦可；本能力不加载任何权重、不占用显存；密文库用标准库 `sqlite3` + `cryptography`（纯 CPU） |
| 7 Python 工程约束 | **触及，须遵守**。新模块全类型注解、禁裸 `Any`（用 `ConfigValue = str\|int\|float\|bool\|None`）、Google-style docstring、ruff+mypy 零错、全相对路径；新增主依赖 `cryptography>=43.0` |

## 六、边界条件

- `config/overrides.yaml` 不存在 → 行为与新增该层之前**逐字节一致**（现有 `test_config.py` 用例
  必须原样通过）。
- 字段被 env 锁定 → `FieldView.locked=true`、`locked_by` 为命中的变量名、GUI 置灰只读；
  `PUT` 把该字段记入 `ignored(env_locked)` 且**不改写磁盘值**。
- 全部提交字段均不可写 → **409**，body 携带 `ignored`；部分可写 → **200**，写出可写字段并返回
  `ignored`。校验失败 → **422**，且**不写任何文件**。
- 校验只看**合并后的最终值**：只提交 `provider="remote"` 而 `base_url` 已在 `global.yaml` 中时
  不应报错。`provider == "stub"` ⇒ 清空 `base_url`/`model` 的 override。
- 配置目录不可写（Docker 非 root + 只读挂载）→ `storage.config_writable` 为 `warn`、
  `FieldView.editable=false`、GUI「保存」禁用并提示「请改用环境变量」。
- 未配置主密钥 → `secrets.available=false`、`config.paths.secret_store` 为 **`skip`**（不是 fail，
  env 密钥完全可用）、密钥输入框禁用并附 reason；`config/secrets.db` **不被创建**。
- 主密钥存在但无法解密已有密文（轮换了密钥）→ `config.paths.secret_store` 为 **`fail`**，hint
  指明要么恢复旧主密钥、要么清空 `secrets.db` 后重新录入；平台仍以 env 密钥运行，
  **不得在启动时抛异常或崩溃**。
- 密文库文件损坏（存在但不是可读的 SQLite）或与索引库解析为同一路径 → `config.paths.secret_store`
  为 **`fail`**，hint 给出恢复步骤；路径冲突判定**先于**主密钥判定，此时即使未配主密钥也是 `fail`
  （比「没配密钥」更需要立刻知道）。两者路径相同时**拒绝写入并告警，不覆盖**。
- 主密钥存在且密文库为空/不存在 → `available=true`（可写入），`config.paths.secret_store` 为 `ok`；
  首次写入时才创建文件。
- 密文库中的密钥与 env 同时存在 → env 胜出（`api_key.source=="env"`、`locked=true`）；密文库不被
  删除，env 撤下后它重新生效。
- `global.yaml` / `overrides.yaml` 中出现 `api_key` → `config.paths.overrides_plaintext_secret`
  为 `warn`，该键被忽略（不读、不改写、不删除——是否清理由用户决定）。
- **密钥不会出现在日志**：审计日志中的模型调用记录只记 `base_url` 的 host 与模型名，不含
  `Authorization` 头与密钥。
- `config.server.auth_token` 的取值：无 token 且 `host` 为回环地址（127.0.0.1/localhost/::1）→
  `skip`（本机部署的合理默认）；无 token 且 `host` 非回环（如 0.0.0.0）→ `warn`；token 来自密文库
  → `warn`（hint 说明自锁风险与解锁步骤）；token 来自 env → `ok`。**任何分支都不产生 `fail`**
  （否则默认本机部署下 doctor 恒失败）。
- `provider == "stub"` → 该角色 `config.{role}.provider` 为 `warn`（文案须明确「确定性假框、禁止
  用于训练」），`model.{role}.connectivity` 为 `skip`；**不产生 `fail`，doctor 退出码仍为 0**。
  这是刻意的：默认配置下 doctor 若恒失败，启动脚本会误报，doctor 将被用户忽略。
- `provider == "remote"` 且 `base_url` 或 `model` 缺失 → `fail`（并保留现有「回退 stub + warning」
  的运行时行为与日志）。
- 探测结果映射：2xx → `ok`；401/403 → `fail`（hint 指向 api_key）；404 → `fail`（hint 指向
  base_url/model 名）；超时或连接错误 → `fail`（hint 指向网络/端点未启动）；其他 4xx/5xx → `fail`。
- `POST /api/config/test` 未知 `role` → 404；探测所需的 `base_url` 为空 → 422。
- 日志中请求体的 `api_key` 字段恒为 `***`；`api_key` 不出现在任何响应、文件或数据库列中。
- `PUT` 的 CORS 预检必须通过（同源与跨源两条路径都要可用）。
- 启动脚本在后端健康后调用 doctor，**打印警告但不阻断启动**。

## 七、验收标准

1. `uv run ruff check src/ run.py tests/` 与 `uv run mypy src/` 零错误；`uv run pytest` 全绿
   （现有用例不回归 + 新增 `test_config_store.py` / `test_diagnostics.py` / `test_secrets.py`）。
2. `uv run python run.py doctor` 在默认 stub 配置下退出码 **0**，输出中明确标注两角色处于 stub
   假模型状态；`--json` 输出可被解析且含 `checks` 数组。
3. `provider=remote` 且端点不可达时 `run.py doctor --deep` 退出码 **1**，`model.{role}.connectivity`
   为 `fail` 且 hint 给出可操作建议。
4. `GET /api/config` 响应全文（含 JSON 序列化后）**搜不到 api_key 明文**；仅 `has_api_key` 与
   `api_key_masked` 可见。
5. 设置页保存 → `config/overrides.yaml` 出现对应键、重启后端后生效；`global.yaml` 的注释与
   注释示例**逐字未变**。
6. 设 `VL_VL_PROVIDER=remote` 后：`GET /api/config` 中 `vl.provider.locked=true` 且
   `locked_by=="VL_VL_PROVIDER"`，设置页该字段置灰；`PUT` 该字段返回 `ignored(env_locked)` 且
   `overrides.yaml` 不含该键。
7. 默认配置下 GUI 显示常驻 stub 警告；读取 `/api/config` 失败时降级为「无法确认模型配置」警告
   （不得静默无提示）。
8. 设 `VL_ANCHOR_AUTH_TOKEN` 后，GUI 全流程（含设置页保存、测试连接、自检）可用 —— 即
   `Authorization` 注入生效；未带 token 时新端点返回 401。
9. 浏览器实测 `PUT /api/config` 与 `GET /api/diagnostics?deep=true` 无 CORS 预检错误。
10. `cd gui && npx tsc --noEmit` 与 `npm run build` 通过。
11. **密钥不落明文**：设主密钥后经 `PUT /api/config` 写入 `api_key`，则 `config/secrets.db`
    存在、`grep -a "<原 key>" config/secrets.db` **搜不到**，且 `config/overrides.yaml` /
    `config/global.yaml` 中**不含非空 `api_key` 值**（`global.yaml` 保留 `api_key: ""` 占位键
    作为文档说明，明文判定只看非空值 —— 见 `find_plaintext_secrets()`）；
    随后的 `GET /api/config`、`GET /api/diagnostics` 与 `logs/*.jsonl` 全文同样搜不到该明文。
12. `run.py secret-keygen` 输出的密钥可被 `VL_ANCHOR_SECRET_KEY_FILE` 直接使用，且该命令运行
    前后工作区**无任何新增/修改文件**。
13. 未设主密钥时：`run.py doctor` 退出码 0、`config.paths.secret_store` 为 `skip`、
    `config/secrets.db` 不被创建、`PUT` 带 `api_key` 返回 `ignored(no_secret_store)`、
    设置页密钥输入框禁用且显示原因。
14. 主密钥与既有密文不匹配时：`run.py doctor` 的 `config.paths.secret_store` 为 `fail` 且 hint
    给出两条可选修复路径；进程**不崩溃**，`GET /api/diagnostics` 仍返回 200。
15. `index.db` 与 `secrets.db` 解析到同一路径（如用户把 `VL_ANCHOR_DB_PATH` 指向
    `config/secrets.db`）时，写密文被拒绝并记 warning，**不覆盖**索引库；
    `config.paths.secret_store` 为 `fail`（此判定先于主密钥判定，故未配主密钥时同样为 `fail`）。
16. `run.py secret-set VL_ANCHOR_AUTH_TOKEN`（值从 stdin）后：重启后端鉴权生效、
    `config/secrets.db` 中搜不到明文、`config.server.auth_token` 为 `warn` 且 hint 含解锁步骤；
    空行输入删除该键后鉴权不再生效。`secret-set` 的输出与错误信息中**不含值**。
17. 无主密钥时 `run.py secret-set X` 退出码 1、提示先配置主密钥，且**不创建** `config/secrets.db`。

## 实现位置

`src/core/config_store.py`、`src/core/diagnostics.py`、
`src/utils/secrets.py`（`read_secret` / `SecretStore` / `generate_master_key`）、
`src/agents/model_client.py`（`probe_endpoint`）、`src/config.py`（overrides 层 + 密文库回退）、
`src/api_server.py`、`run.py`、`config/overrides.yaml`（运行时生成）、
`config/secrets.db`（运行时生成，需外部主密钥）、
`gui/src/pages/SettingsPage.tsx`、`gui/src/components/{StubWarningBanner,DiagnosticsPanel}.tsx`、
`gui/src/services/api.ts`、`gui/src/App.tsx`、`start_gui.ps1`、`start_gui.sh`、`.env.example`。

## 已知缺口

- 验收 9（浏览器 CORS 预检）与验收 7、8 的界面部分**只在 TestClient / 单元层验证过**，真机浏览器
  实测未闭环 —— 见 [`design.md`](design.md) 的「未闭环项」。
- `timeout_s` 等三字段无 env 覆盖（ADR-001 已知限制），容器化部署无法按角色调超时。
