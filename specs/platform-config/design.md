# Design: platform-config

> **状态**：已实施（as-built）
> **关联 spec**：[`spec.md`](spec.md)
> **关联 ADR**：ADR-001（LLM/VL 双模型配置拆分 —— 本设计的 `base`/`role` 继承链直接建在它之上）
> **实施栈**：后端（`src/**`、`run.py`）+ 前端（`gui/src/**`）
> **契约冻结点**：`EffectiveConfig` / `WriteResult` / `ProbeResult` / `DiagnosticReport` 四个
> Pydantic 模型在 M1 定稿后方可动 `types/index.ts` 与 `SettingsPage.tsx`；`CheckResult.id`
> 清单（13 个）同属契约，增删即跨栈变更
> **最后核对**：2026-09-27
> **来源**：吸收原变更提案 `2026-09-27-config-onboarding/proposal.md` 的交付物清单、已确认决策与
> 验收对照（该提案随 SDD 结构改造并入本文件，`changes/` 中间态已废弃）

## 一、执行摘要

让用户在首次使用与部署时能（a）看到当前生效模型配置及其来源，（b）在 UI 中修改未被 env 锁定的
字段，（c）一键验证模型端点连通性，（d）被明确告知「当前是 stub 假模型」，（e）通过
`run.py doctor` 拿到可脚本化的自检结论。

手段是三件事：**配置收口**（在 env 与 `global.yaml` 之间插入 `overrides.yaml`，每个字段带来源与
锁定标记）、**密钥与密文分离**（Fernet 密文库 + 只从 env 读的主密钥）、**检查项编排**
（13 个稳定 id 的检查 + 两角色端点探测，聚合为一个可退出码化的报告）。

代价：`config/` 下多出两个运行时文件（`overrides.yaml`、`secrets.db`），且密钥库要求部署方提供
主密钥——没有主密钥时密文库整体不可用（按设计降级为 env-only，不报错）。

## 二、交付物（as-built）

| 类别 | 内容 |
|------|------|
| 配置层 | `src/core/config_store.py`（新）——读取生效配置 + 逐项来源标记（env/overrides/yaml/secrets/default）、env 锁定判定、非密字段原子写回 `config/overrides.yaml` |
| 密钥层 | `src/utils/secrets.py`（新）——`read_secret()`（env 与 `*_FILE` 间接）+ `SecretStore`（`config/secrets.db`，Fernet 密文，主密钥来自 `VL_ANCHOR_SECRET_KEY[_FILE]`）；`config.py` 的 `read_secret` 迁入此处并再导出 |
| 自检层 | `src/core/diagnostics.py`（新）——配置完整性、按角色连通性、存储可写性与容器持久性、prompts 完整性、明文密钥与密文库状态，输出结构化报告 |
| 探测 | `src/agents/model_client.py` 增 `probe_endpoint()` / `ProbeResult`（含测试缝 `transport`） |
| API | `src/api_server.py`：`GET/PUT /api/config`、`POST /api/config/test`、`GET /api/diagnostics`；CORS `allow_methods` 加 `PUT`；`_is_preflight()` 豁免；新增端点受鉴权保护 |
| CLI | `run.py`：`doctor [--deep] [--json]`、`secret-keygen`、`secret-set NAME`；`--task` 对这四个命令放宽为非必填 |
| 配置 | `config/global.yaml` 增 `database:` 段；`.env.example` 增 `VL_ANCHOR_DB_PATH` / `VL_ANCHOR_SECRET_KEY_FILE` / `VITE_AUTH_TOKEN`；`pyproject.toml` 增 `cryptography>=43.0`（主依赖区） |
| 启动脚本 | `start_gui.ps1` / `.sh` 后端就绪后调一次 doctor，有 warn/fail 时打印醒目警告（**不阻断启动**），探活改打 `/api/health` |
| GUI | `gui/src/pages/SettingsPage.tsx`（新）、`components/{StubWarningBanner,DiagnosticsPanel}.tsx`（新）、`App.tsx`（第 5 个 Tab）、`services/api.ts`（`Authorization` 注入）、`types/index.ts`（对齐 Pydantic） |
| 文档 | `README.md` 增「初始化引导 / 密钥管理」章节；`CHANGELOG.md`；`.gitignore` 忽略 `overrides.yaml` / `secrets.db*` |

## 三、关键设计决策

| 决策 | 选项 | 选择 | 理由 | 被否选项的代价 |
|------|------|------|------|---------------|
| 写回目标 | 改 `global.yaml` / 另立 `overrides.yaml` | **另立 `config/overrides.yaml`** | `yaml.safe_dump` 往返会抹掉 `global.yaml` 全部注释与注释掉的示例（该文件 69 行里大半是文档）；独立文件同时保住注释与用户手改内容，「恢复默认」退化为删文件 | 直接改 `global.yaml` ⇒ 一次保存毁掉配置文件里的全部文档 |
| 密钥落盘 | `.env` 明文 / YAML / 密文库 | **Fernet 密文库 `config/secrets.db`**（用户明确要求） | 明文凭据一旦落地就等于泄漏：`.env` 会被提交、YAML 会被 `grep` 到、镜像会被烤进去 | 明文 ⇒ 一份 README 教不会所有人「别提交这个文件」 |
| 主密钥来源 | 自动生成到 `config/` / 只从 env 读 | **只从 `VL_ANCHOR_SECRET_KEY[_FILE]` 读，绝不自动生成** | 密钥与密文同目录同权限等于混淆而非加密，反而给人「已加密」的错觉 | 自动生成 ⇒ 加密沦为形式，`secrets.db` 被拷走即等于明文 |
| 密钥库位置 | 复用 `index.db` / 独立文件 | **`config/secrets.db` 独立** | `index.db` 被定义为「可随时删库重建」，凭据放进去会被 `index --rebuild` 或一次 `rm` 销毁；两者路径冲突时**拒绝写入** | 复用 ⇒ 一次例行维护销毁所有凭据 |
| 密文库不可用时 | 启动失败 / 降级 env-only | **降级：不建库、照常以 env 密钥运行** | 密文库是便利层而非启动必要条件；强制要求会让每个不用它的部署都起不来 | 强制 ⇒ 默认部署直接失败，劝退 |
| env 覆盖语义 | 有变量即锁定 / 非空才锁定 | **非空才算锁定**（对齐 `_env()`） | `VL_VL_PROVIDER=""` 是常见的「清空变量」写法，若算锁定会让用户在 UI 上永远改不了这个字段 | 有即锁 ⇒ 「改了没用」的幽灵 bug |
| 无任何字段可写 | 200 + `ignored` / 409 | **409 携带 `ignored`** | 「点保存但什么都没发生」是最难自查的失败；409 让前端能明确报错 | 200 ⇒ 用户以为保存成功 |
| `restart_required` | 恒 `true` / `bool(written)` | **`bool(written)`** | `Settings` 经 `lru_cache` 缓存，确有字段落盘才需要重启；`written` 为空时报 `true` 会让人为一个未发生的变更去重启后端 | 恒 true ⇒ 409 时给出错误的操作指引 |
| 鉴权 token 写入路径 | 也开 `PUT /api/config` / 只开 CLI | **只开 `secret-set` 与 env** | token 一旦入库又丢失客户端副本（清 localStorage / 换机器），全部 API 401，解锁只能改 env 或删库；路径越窄误配越少 | 双路径 ⇒ 自锁风险显著上升 |
| `secret-set` 取值 | argv / stdin | **stdin** | argv 在同机 `ps` 里对任何用户可见 | argv ⇒ 密钥进进程列表 |
| `config_persistent` | 只看 `os.access` / 加容器挂载判定 | **容器内比对 `st_dev`** | `COPY --chown` 让 `/app/config` 可写，一次「保存成功」在容器重建后消失——比直接报错更误导 | 只看可写 ⇒ 用户以为改了、实际没改 |
| 探测粒度 | 带图请求 / 极短纯文本 | **`max_tokens=1` 的纯文本 ping，无图像** | 目的是验证「端点可达 + 鉴权有效 + 模型名存在」，不需要样本图；也让 8GB 机器上代价可忽略 | 带图 ⇒ 探测慢、耗显存、依赖样本 |
| 探测重试 | 复用 `OpenAICompatClient` 重试 / `max_retries=0` | **`max_retries=0`，超时 `min(timeout_s, 15s)`** | 探测要快；重试只会把延迟放大数倍而结论不变 | 重试 ⇒ UI 挂起，用户以为卡死 |
| stub 的 doctor 结论 | `fail` / `warn` | **`warn`，退出码 0** | 默认配置就是 stub；若判 `fail` 则 doctor 在任何默认部署下恒失败，启动脚本误报，用户从此忽略它 | 判 fail ⇒ 自检被当成噪音 |
| 深度自检默认值 | `deep=true` / `deep=false` | **默认 `false`，零网络调用、零日志写入** | GUI 会轮询它；浅层自检每小时几十次，写进 `diagnostics.jsonl` 只会把审计日志变成运行日志 | 默认 deep ⇒ 轮询即打网络、审计日志被噪音淹没 |
| 探测两次是否并发 | 并发 / 串行 | **串行，各自计时** | 并发会让 8GB 机器上两个端点互相干扰，`latency_ms` 也失去意义 | 并发 ⇒ 延迟数字不可用 |

## 四、架构与依赖

```
config/{global.yaml, overrides.yaml}  ← overrides.yaml 为 UI 写回目标（非密字段）
config/secrets.db                     ← Fernet 密文库（仅 api_key，需外部主密钥）
        │
        ▼
src/core/config_store.py    读取/校验/写回配置 + 来源与锁定判定
src/core/diagnostics.py     检查项编排 + 端点探测 → DiagnosticReport
src/utils/secrets.py        read_secret（env/*_FILE）+ SecretStore（密文库）

主密钥（不在磁盘树内）：VL_ANCHOR_SECRET_KEY_FILE / VL_ANCHOR_SECRET_KEY ← Docker secret
```

**依赖方向**：`api_server` / `run.py` → {`config_store`, `diagnostics`} → `config` / `task_manager` /
`utils`。`diagnostics` 只接收 `ConfigStore` 实例做只读查询，不构造它。

`src/utils/secrets.py` **不得 import `src.config`**（否则与 `config.py → utils.secrets` 构成循环）；
`read_secret` 因此从 `config.py` 迁出到 `utils/secrets.py`，`config.py` 反向 import 它，并保留
`src.config.read_secret` 作为再导出别名，现有调用点与测试不受影响。

## 五、配置层设计（`src/core/config_store.py`）

### 5.1 优先级与来源判定

现状（`src/config.py:120-152`）是 `role env > role yaml > shared env > shared yaml > default`。
本设计在 env 与 yaml 之间插入 `overrides.yaml`：

```
base  = env VL_MODEL_*  >  overrides.model.*        >  global.yaml model.*        >  default
role  = env VL_{ROLE}_* >  overrides.model.{role}.* >  global.yaml model.{role}.* >  base
```

判定必须与 `src/config.py` 的 `_env()` 语义**逐字对齐**：`_env` 把空字符串视为未设置
（`src/config.py:45-46`），因此 `VL_VL_PROVIDER=""` **不构成锁定**。

| 命中层 | `source` | `locked` |
|--------|----------|----------|
| `VL_{ROLE}_*` 或 `VL_MODEL_*`（值非空） | `env` | `true` |
| `overrides.yaml` | `overrides` | `false` |
| `global.yaml` | `yaml` | `false` |
| 无 | `default` | `false` |

`locked == true` ⇒ GUI 对应输入框只读 + 🔒 标注命中变量名；`PUT /api/config` 拒绝改写该字段。

### 5.2 `ConfigStore` 接口

```python
class ConfigStore:
    def __init__(self, settings: Settings, config_dir: Path) -> None: ...
    def effective(self) -> EffectiveConfig: ...
    def is_writable(self) -> bool: ...
    def write(self, patch: ConfigPatch) -> WriteResult: ...
    def validate_patch(self, patch: ConfigPatch) -> list[str]: ...   # 返回错误消息列表
```

- `effective()` 纯读，无副作用（不建目录、不建文件、**不创建 `secrets.db`**）。
- `is_writable()`：`config_dir` 存在且 `os.access(config_dir, os.W_OK)`；不存在时判定父目录可写性。
- `write()`：
  1. `validate_patch` 不过 → 由调用方转 422，**不写任何文件**。
  2. 非密字段（`provider`/`base_url`/`model`）：`locked` → 记入 `ignored(env_locked)`；
     `not_writable` → 记入 `ignored(not_writable)` 且整体不写 YAML。
  3. `api_key`：`locked`（env 已提供）→ `ignored(env_locked)`；密文库不可用 →
     `ignored(no_secret_store)`；否则写入 `SecretStore`（`""` = 删除该行）。
  4. 合并现有 `overrides.yaml`（保留未提交字段），**原子写**（临时文件 + `os.replace`）。
  5. `restart_required = bool(written)`。
- `write()` 返回前**必须**用 `find_plaintext_secrets()` 复查一遍 `overrides.yaml`，确保新写内容中
  无 `api_key` 键（自证不落明文，兼作回归防线）。
- 校验规则：`provider == "remote"` ⇒ `base_url` 非空且 scheme ∈ {http, https}，`model` 非空；
  否则 422。`provider == "stub"` ⇒ 清空 `base_url`/`model` 的 override（写 `null` 语义 = 删除该键）。
  校验只看**合并后的最终值**：只提交 `provider="remote"` 而 `base_url` 已在 `global.yaml` 中时
  不应报错。

### 5.3 数据结构

```python
ConfigSource = Literal["env", "overrides", "yaml", "secrets", "default"]
ConfigValue = str | int | float | bool | None      # 不用裸 Any

class FieldView(BaseModel):
    value: ConfigValue        # 密钥类字段恒为 None
    source: ConfigSource
    locked: bool
    locked_by: str = ""       # 锁定的环境变量名，如 "VL_VL_PROVIDER"
    editable: bool            # not locked and writable

class RoleView(BaseModel):
    role: Literal["llm", "vl"]
    provider: FieldView
    base_url: FieldView
    model: FieldView
    timeout_s: FieldView
    api_key: FieldView        # value 恒为 None；source 可为 "env"/"secrets"/"default"
    has_api_key: bool
    api_key_masked: str       # "" | "****3f2a"（仅当 key 长度 >= 4）

class SecretsView(BaseModel):
    available: bool           # 有合法主密钥且能解密已有密文
    reason: str = ""          # available=False 时的中文原因
    master_key_source: str = ""   # 命中的变量名，如 "VL_ANCHOR_SECRET_KEY_FILE"
    store_path: str = ""          # config/secrets.db

class RolePatch(BaseModel):
    provider: Literal["stub", "remote"] | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None    # None = 不改动；"" = 删除密钥；"***" = 不修改（回显占位）

class ConfigPatch(BaseModel):
    llm: RolePatch | None = None
    vl: RolePatch | None = None

class IgnoredField(BaseModel):
    role: str
    field: str
    reason: Literal["env_locked", "read_only", "not_writable", "no_secret_store"]

class WriteResult(BaseModel):
    written: dict[str, dict[str, str]]   # role -> field -> value（api_key 恒为 "***"）
    ignored: list[IgnoredField]
    overrides_path: str
    restart_required: bool
```

### 5.4 密钥处理（硬约束落地）

| 位置 | 是否允许 api_key 明文 |
|------|----------------------|
| 环境变量 / `*_FILE` 挂载文件 | ✅ 唯一推荐的部署方式 |
| `config/secrets.db` | 仅 Fernet 密文 |
| `config/global.yaml` / `config/overrides.yaml` | ❌ 被忽略并告警（文件受版本控制） |
| `<data_dir>/index.db` | ❌ 从不写入 |
| 日志（含 `model_calls.jsonl`、`run_history.jsonl`） | ❌ 恒为 `***` |
| HTTP 响应 | ❌ 只有 `has_api_key` + `api_key_masked`（末 4 位） |

解析优先级：`VL_{ROLE}_API_KEY[_FILE]` > `VL_MODEL_API_KEY[_FILE]` > `secrets.db[role]` >
`secrets.db[shared]` > 空。env 命中即 `locked=true`（改库无效，避免「改了没反应」的困惑）。

设置页输入的临时 key 通过 `POST /api/config/test` 的请求体**仅内存透传**给探测函数，用完即弃。
明文 key 的传输安全由部署方在 GUI 前置 TLS 反代保证（README 已要求）。

### 5.5 密文库设计（`src/utils/secrets.py`）

```python
class SecretStoreState(StrEnum):
    OK = "ok"                           # 可读写
    NO_MASTER_KEY = "no_master_key"
    BAD_MASTER_KEY = "bad_master_key"   # 能读到主密钥，但解不开已有密文
    UNAVAILABLE = "unavailable"         # sqlite 打不开 / 路径冲突

class SecretStore:
    def __init__(self, db_path: Path, *, index_db_path: Path | None = None) -> None: ...
    def state(self) -> SecretStoreState: ...      # 缓存；含一次 key_check 试解密
    def get(self, name: str) -> str: ...          # 未配置/不可用 → ""
    def set(self, name: str, value: str) -> bool: ...   # "" = 删除
    def path(self) -> Path: ...
    def master_key_source(self) -> str: ...
```

- **按名字取值的通用库**：`get`/`set` 以名字为键，键名即被替代的环境变量名
  （`VL_LLM_API_KEY` / `VL_VL_API_KEY` / `VL_MODEL_API_KEY` / `VL_ANCHOR_AUTH_TOKEN`）。
  读取时逐名回退，因此「哪一层命中了」可直接由名字推出，无需额外元数据。
- **`key_check` 哨兵**：`meta` 表存一条用当前主密钥加密的常量。启动时试解密它，即可区分
  `NO_MASTER_KEY`（未配）与 `BAD_MASTER_KEY`（配了但换了密钥、现有密文全部读不出）。
  没有哨兵就只能把「解不开」误报成「没配」。
- **路径冲突拒绝写入**：`db_path` 与 `index_db_path` 解析到同一文件时**拒绝写入并告警，不覆盖**。
  写入影响的是凭据，宁可失败也不能赌哪份数据更重要。
- 连接策略：短连接 + `timeout=5.0`；`PRAGMA journal_mode=WAL`。写操作 `try/except` 后返回 bool。

### 5.6 config 目录持久性判定（`config_persistent`）

`Dockerfile` 用 `COPY --chown=app:app config ./config` 把配置烤进镜像层，因此容器内 `/app/config`
是**可写的**（`os.access` 返回 True），设置页会允许一次「保存成功」的写入，而容器重建即丢失
——用户以为改了、实际没改，比直接报错更糟。判定：

```python
def _config_persistent(config_dir: Path) -> bool:
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not in_container:
        return True                       # 裸机/虚拟机：目录就是真实文件系统
    parent = config_dir.parent
    return config_dir.stat().st_dev != parent.stat().st_dev   # 设备号不同 = 是挂载点
```

不持久时 `config.paths.config_persistent` 为 `warn`（hint 指 `docker-compose.yml` 的
`./config:/app/config` 挂载），GUI 在保存按钮上方给出常驻提示。**不**因此禁止保存——本地
`docker run` 无挂载时，用户仍可能只想临时改一下并接受重建后丢失。

## 六、自检层设计（`src/core/diagnostics.py`）

### 6.1 检查项清单（id 即契约）

| id | 条件 | 状态 |
|----|------|------|
| `config.llm.provider` / `config.vl.provider` | `provider == "stub"` | `warn`（"离线 stub，标注为确定性假框，禁止用于训练"） |
| `config.{role}.base_url` | `remote` 且 `base_url` 空 | `fail` |
| `config.{role}.model` | `remote` 且 `model` 空 | `fail` |
| `config.{role}.api_key` | `remote` 且无 key | `warn`（自建端点常无需 key） |
| `config.paths.config_writable` | 配置目录不可写 | `warn`（设置页保存不可用） |
| `config.paths.config_persistent` | 容器内且 config 非挂载点 | `warn`（写回重建即丢） |
| `config.paths.overrides_plaintext_secret` | YAML 中出现 `api_key` | `warn`（该键被忽略） |
| `config.paths.secret_store` | 无主密钥 → `skip`；主密钥解不开密文或密文库不可用（损坏 / 与索引库同路径）→ `fail`；其余 `ok` | 见 5.5 |
| `config.server.auth_token` | 无 token 且 `host` 回环 → `skip`；无 token 且 `host` 非回环 → `warn`；token 来自密文库 → `warn`（自锁风险）；token 来自 env → `ok` | 永不为 `fail` |
| `storage.tasks_root` | 不存在或不可写 | `fail` |
| `storage.prompts` | `prompts_dir` 下三个 agent 提示词缺失或缺少 `system_prompt` | `fail` |
| `storage.db` | `db_enabled=false` → `skip`；路径不可写 → `warn`（best-effort）；否则 `ok` | — |
| `model.{role}.connectivity` | `deep=True` 时实际探测；`provider == "stub"` → `skip` | 见 6.2 |

注意 `config.paths.secret_store` 的 `fail` 只在「配了主密钥但解不开」或「密文库本身不可用」时出现
——这是真实的配置损坏，必须让 doctor 退出 1；而「没配主密钥」是合法默认，只能是 `skip`，否则每个
不用密文库的部署都会 doctor 失败。`_compute_state()` 中**路径冲突先于主密钥判定**：两者路径相同是
比「没配密钥」更严重、也更需要立刻知道的错误（此时 `secrets.available=false`，`PUT` 带 `api_key`
落到 `ignored(no_secret_store)`，真正的解释在 `secrets.reason` 中）。

### 6.2 探测实现

`probe(role, api_key_override=None) -> ProbeResult`：向 `base_url.rstrip("/") + "/chat/completions"`
发**单个极短文本请求**（`max_tokens=1`，`messages=[{"role":"user","content":"ping"}]`），**不携带
图像**——目的是验证端点可达 + 鉴权有效 + 模型名存在，不需要样本图。

- 超时取 `min(settings.timeout_s, 15.0)`，保证 UI 不挂起；`max_retries=0`。
- 状态映射：2xx → `ok`；401/403 → `fail`（hint: 检查 api_key）；404 → `fail`（hint: 检查 base_url
  与 model 名）；超时/连接错误 → `fail`（hint: 检查网络与端点是否启动）；其他 4xx/5xx → `fail`。
- 复用 `OpenAICompatClient` 的请求构造思路但**不复用其重试逻辑**，新增独立函数置于
  `src/agents/model_client.py`（与 HTTP 客户端同处，避免 diagnostics 直接依赖 httpx 细节）：

  ```python
  def probe_endpoint(
      settings: ModelSettings,
      *,
      role: Literal["llm", "vl"] = "vl",
      api_key_override: str | None = None,
      base_url_override: str | None = None,
      model_override: str | None = None,
      transport: httpx.BaseTransport | None = None,
  ) -> ProbeResult: ...
  ```

  **`transport` 是测试缝**：默认 `None` 即真实网络；测试注入 `httpx.MockTransport`，使探测分支
  （2xx / 401 / 404 / 超时 / 连接错误）无需起服务、无需联网即可覆盖。它不是给生产调用的参数，
  生产路径一律不传。

  **`ProbeResult` 定义在 `src/agents/model_client.py`**（探测函数的返回值类型应与函数同居），由
  `src/core/diagnostics.py` 以 `from src.agents.model_client import ProbeResult` 再导出，供 API 层
  与其他调用方使用。若定义在 diagnostics 则 `diagnostics → model_client → diagnostics` 构成循环
  import。签名中的三个 `*_override` 是本设计对 spec「临时覆盖」语义的落地点
  （`POST /api/config/test` 的请求体即映射到它们），`role` 参与返回值的 `role` 字段填充。

### 6.3 退出码语义（`run.py doctor`）

- 存在任一 `fail` → 退出码 **1**。
- 仅 `warn` / `ok` / `skip` → 退出码 **0**。
- 关键推论：**默认 `provider=stub` 只产生 `warn`，退出码为 0**。否则默认配置下 doctor 永远失败，
  启动脚本会误报，doctor 将被用户忽略。stub 的危险性通过 `warn` 的醒目文案 + GUI banner 传达。

```python
class Doctor:
    def __init__(self, settings, task_manager, config_dir, *,
                 transport: httpx.BaseTransport | None = None) -> None: ...
    def run(self, *, deep: bool = False,
            roles: Sequence[Literal["llm", "vl"]] = ("llm", "vl")) -> DiagnosticReport: ...
```

`Doctor` 内部按需构造 `ConfigStore(settings, config_dir)` 与 `SecretStore(config_dir / "secrets.db")`
（只读查询，不写文件）；`deep=True` 时每个角色一次 `probe_endpoint`，两次探测**串行**执行并各自
计时——并发会让 8GB 机器上的两个端点互相干扰，也让 `latency_ms` 失去意义。

`deep=False` 必须**零网络调用、零日志写入**（GUI 会轮询它）：`Doctor` 因此不缓存任何状态，
`logs/diagnostics.jsonl` 只在 `deep=True` 时追加——浅层自检每小时被调用几十次，写进去的只会是
噪音，并且会让「审计日志」变成运行日志。

## 七、API / CLI / GUI

### 7.1 API

```
GET  /api/config                -> EffectiveConfig            200
PUT  /api/config                -> WriteResult                200 | 422 | 409
POST /api/config/test           -> ProbeResult                200 | 404(未知角色) | 503
GET  /api/diagnostics           -> DiagnosticReport           200  (query: deep: bool = false)
```

- **全部受鉴权保护**：不加入 `_PUBLIC_PATHS`（`src/api_server.py:53`）。它们即使不回显密钥，也
  暴露 base_url/model，不应匿名可读。
- `PUT /api/config` 校验失败 → **422**；配置目录不可写 / 字段被 env 锁定导致**无任何字段可写**
  → **409**，body 里带 `ignored` 列表说明原因。部分可写时写成功字段并返回 `ignored`（200）。
- **`CORSMiddleware.allow_methods` 必须从 `["GET","POST"]` 扩为 `["GET","POST","PUT"]`**
  （`src/api_server.py:37`），否则浏览器预检失败。curl/TestClient 不受影响，故此项必须由
  浏览器实测覆盖。
- **鉴权中间件必须豁免预检**：浏览器**从不**给预检请求带 `Authorization`，因此启用
  `VL_ANCHOR_AUTH_TOKEN` 的部署里 `PUT /api/config` 根本发不出去。`_is_preflight()` 判定
  `request.method == "OPTIONS"` 且带 `Access-Control-Request-Method` 头即放行。这是验证期发现并
  修掉的真实缺陷（见「基线复跑」）。
- `POST /api/config/test` body：`{"role": "llm"|"vl", "base_url"?: str, "model"?: str,
  "api_key"?: str}` —— 三者均为临时覆盖，缺省用当前生效值；用于「填了 key 先测再存」。
- `GET /api/diagnostics` 默认 `deep=false`（只做本地检查，不产生网络调用），`deep=true` 时才探测
  并写入 `logs/diagnostics.jsonl`。避免 GUI 每次轮询都打网络。
- `/api/health` **保持不变**。

### 7.2 CLI（`run.py`）

现有 `command` 为位置参数 + `--task` 必填（`run.py:31-33`）。新增命令时需放宽 `--task` 的必填性：

- `run.py doctor [--deep] [--json] [--task T]`：自检。`--deep` 触发端点探测；`--json` 输出机器可读
  报告；退出码按 6.3。**`--task` 变为非必填**，仅 `plan|annotate|inspect|split|full` 要求提供
  （对缺失者报错退出 2，保持既有人机语义）。
- `run.py index --rebuild`：全量重建索引，打印 `RebuildResult`。
- `run.py secret-keygen`：**仅**把 `generate_master_key()` 的结果与两行使用说明打到 stdout，
  **不写任何文件**。让 CLI 代写密钥文件会立刻把「主密钥必须与密文分离」这条约束破坏掉——用户的
  下一步必须是把这串值交给 Docker secret 或 `VL_ANCHOR_SECRET_KEY_FILE` 所指的文件。
- `run.py secret-set NAME`：`--name` 取四个白名单键名（其余退出 2），值从 **stdin** 读一行
  （`getpass`-style 不可行——它需要 tty，而 Docker/CI 里没有；直接读 stdin 更通用），空行 = 删除。
  成功/失败均只打印键名与结果，**永不回显值**。无主密钥 → 退出 1 并给配置指引，
  **不自动生成主密钥**（否则主密钥会落到 `config/` 下，与密文同目录同权限）。

### 7.3 GUI

| 文件 | 改动 |
|------|------|
| `gui/src/pages/SettingsPage.tsx` | 新增：两角色表单 + 锁定标注 + 测试连接 + 保存 + 自检面板 + 配置不可写时的置灰与说明 |
| `gui/src/components/StubWarningBanner.tsx` | 新增：`provider == "stub"` 时常驻 `Alert type="warning"`；`/api/config` 读取失败时降级为「无法确认模型配置」警告 |
| `gui/src/components/DiagnosticsPanel.tsx` | 新增：渲染 `DiagnosticReport`，按 fail/warn/ok/skip 着色 + hint |
| `gui/src/App.tsx` | 加第 5 个 Tab「设置」；顶部渲染 `StubWarningBanner` |
| `gui/src/services/api.ts` | 请求拦截器注入 `Authorization: Bearer`（`VITE_AUTH_TOKEN` → localStorage `vl_auth_token`）；新增 5 个调用函数；401 时抛出可识别错误 |
| `gui/src/types/index.ts` | 新增与后端 pydantic 模型一一对应的 TS 接口 |
| `gui/src/pages/InspectionReportPage.tsx` | 追加「执行历史」区块（见 [`specs/metadata-store/design.md`](../metadata-store/design.md)） |

设置页行为要点：

- 每个字段旁显示来源徽标（`env` / `overrides` / `yaml` / `secrets` / `default`）；`locked` 时输入框
  `disabled` 并显示 🔒 + `locked_by` 变量名。
- **`api_key` 输入框单独处理**：恒为密码型空输入（后端只回 `has_api_key` + `api_key_masked`，
  占位符显示 `****3f2a` 或「未设置」）。`secrets.available == false` 或 `api_key.locked` 时禁用，
  并在下方直接显示 `secrets.reason` / `locked_by`。提交时：留空 = 不改动；输入 `***` = 不改动；
  点「清除」= 提交 `""`。
- `config_writable == false` 时「保存」按钮禁用，`Alert` 说明「配置目录只读，请改用环境变量」。
- `config_persistent == false` 时在保存按钮上方常驻 `Alert`：「容器内写回不持久，容器重建即丢失；
  如需持久请挂载 `./config:/app/config` 或改用环境变量」。
- 保存成功后若 `restart_required`，提示「已写入，需重启后端生效」。
- 「测试连接」按角色单独触发，展示 `latency_ms` / `http_status` / hint；用户刚输入的 key 一并随
  请求体发出（仅内存透传，不入库）。
- 顶部显示 `secrets.master_key_source` 与密文库状态；未启用密文库时说明「密钥只能来自环境变量」。

## 八、既有文件改动与兼容性

| 文件 | 改动 | 兼容性 |
|------|------|--------|
| `src/config.py` | `load_settings()` 增加 `overrides.yaml` 层；密钥回退到密文库；`Settings` 增 `db_path` / `db_enabled`；`read_secret` 迁往 `src/utils/secrets.py` 后在此再导出 | 无 overrides 文件、无密文库时行为与改造前**逐字节一致**（现有 `test_config.py` 5 个用例原样通过） |
| `src/utils/secrets.py` | 新增：`read_secret`（自 `config.py` 迁入）+ `SecretStore` + `generate_master_key` + `is_valid_master_key` | 纯新增；`src.config.read_secret` 保留为别名，调用点不改 |
| `src/agents/model_client.py` | 增 `probe_endpoint()` / `ProbeResult`；`OpenAICompatClient` 增 `last_http_status` | 不调用探测时行为不变 |
| `src/api_server.py` | CORS 加 `PUT`；4 个新端点；`_is_preflight()` 豁免；注入 `ConfigStore` / `Doctor` | 现有 14 个端点契约不变 |
| `config/global.yaml` | 增 `database: {enabled, path}` 段 | 缺该段时用内置默认 |
| `pyproject.toml` | 主依赖增 `cryptography>=43.0`（`sqlite3`/`json` 为标准库） | 纯新增依赖，进主依赖区而非 `[gpu]` |
| `.env.example` / `.gitignore` / `README.md` / `Makefile` | 新增变量（含主密钥两项）、忽略 `overrides.yaml` / `secrets.db*`、文档章节 | 纯增量 |
| `start_gui.ps1` / `.sh` | 后端就绪后调 `doctor`，有 warn/fail 时打印醒目警告；探活改打 `/api/health` | **不阻断启动**（与「健康检查失败才退出」区分） |

## 九、测试策略

- `tests/test_config_store.py`（新）：来源判定矩阵（env/overrides/yaml/secrets/default × 共享/分角色）、
  空字符串 env **不**锁定、`locked_by` 变量名正确、`write()` 忽略 env 锁定字段、api_key 响应中
  无明文、`validate_patch` 对 remote 缺 base_url/model 报错、原子写不损坏既有 overrides、
  配置目录只读时 `is_writable()==False` 且 `write()` 不改文件。
- `tests/test_secrets.py`（新）：`read_secret` 的 `*_FILE` 优先与 BOM/换行处理、文件不可读时回退、
  `SecretStore` 无主密钥时不建文件且 `get()` 返回空、写入后库内**搜不到明文**、
  错误主密钥 → `BAD_MASTER_KEY`、`set("")` 删除、`db_path == index_db_path` 时拒绝写入、
  `is_valid_master_key` 对随机串为 False。
- `tests/test_diagnostics.py`（新）：stub → warn 且 `status=="warn"`、remote 缺 base_url → fail、
  prompts 缺失 → fail、`deep=True` 且 stub → probe 为 skip、探测超时/401/404 → fail 且 hint 正确、
  退出码语义（有 fail → 1；仅 warn → 0）、无主密钥 → `secret_store` 为 skip 且退出码 0、
  `BAD_MASTER_KEY` → fail、`auth_token` 四分支（回环无 token → skip；0.0.0.0 无 token → warn；
  库中 token → warn 且 hint 含解锁步骤；env token → ok）。探测用 `httpx.MockTransport`，不打真实网络。
- `tests/test_api_server.py`（改）：新端点 200/404/409/422/503 路径；`/api/config` 响应不含
  api_key 明文；开 token 后新端点 401；**预检在开启鉴权时仍放行**（回归用例）。
- `tests/test_cli.py`（改）：`doctor` 退出码、`index --rebuild` 输出、`secret-keygen` 不落文件、
  `secret-set` 的 stdin 与退出码、`--task` 缺省时的报错语义。
- **浏览器实测**（不可由 TestClient 替代）：`PUT` 的 CORS 预检、设置页保存生效、stub banner 出现。
- `gui`：`npx tsc --noEmit` + `npm run build` 通过。

## 十、基线复跑

后端：

| 命令 | 结果 |
|------|------|
| `uv run ruff check src/ run.py tests/` | All checks passed |
| `uv run mypy src/` | Success，22 files |
| `uv run pytest -q` | **203 passed** |
| 改动前基线（`git archive HEAD` 解到临时目录跑同一套命令） | **99 passed**（含 `test_config.py` 5 passed）⇒ 净增 104 例，**无回归** |

GUI：

| 命令 | 结果 |
|------|------|
| `cd gui && npx tsc --noEmit` | 0 错误 |
| `cd gui && npm run build` | 3055 modules transformed（14.30s），ExitCode 0 |

反向验证：

- **DB 非必要**：删除 `index.db` 后 9 个既有端点响应逐字节相同，且读路径不重建文件。
- **配置层零影响**：无 `overrides.yaml` / `secrets.db` 时全量用例与改动前一致。
- **降级可用**：无主密钥、主密钥不匹配两条降级路径下平台均可启动、可读写配置、退出码符合 spec。

验证期发现并修掉两个真实缺陷（均加回归用例）：

1. **CORS 预检被鉴权拦掉**：预检请求不带 `Authorization`，开了 `VL_ANCHOR_AUTH_TOKEN` 的部署里
   `PUT /api/config` 根本发不出去 —— 既有测试因未配 token 而恰好通过。
   → `src/api_server.py` 的 `_is_preflight()` 豁免 + `test_config_put_preflight_is_allowed_with_auth_enabled`。
2. **`index --rebuild` 不收敛**：原实现只 `DELETE FROM tasks`，其余表靠 `UNIQUE` +
   `INSERT OR IGNORE`，日志行消失后残留孤儿行（实测旧索引 10 runs / 4 calls ↔ JSONL 4 / 1）。
   → 四张数据表一并清空后重放 + `test_rebuild_drops_rows_whose_log_lines_are_gone`。
   （归属元数据层，详见 [`specs/metadata-store/design.md`](../metadata-store/design.md)。）

## 十一、验收对照

`spec.md` 十七条验收标准的逐条证据：

- [x] **1** ruff / mypy / pytest 全绿；**203 passed**（基线 99，无回归）。
- [x] **2** 默认 stub 配置下 `run.py doctor` 退出 **0**，两角色均出明确的 stub 假模型警告；
  `--json` 输出可解析且含 `checks`（`tests/test_cli.py`）。
- [x] **3** `provider=remote` 且端点不可达时 `run.py doctor --deep` 退出 **1**，
  `model.vl.connectivity` 为 `fail`，hint 指向「查看服务端日志、确认模型已加载且显存充足」。
- [x] **4** `GET /api/config` 响应全文（含 JSON 序列化）搜不到 api_key 明文，只出
  `has_api_key=true` 与 `api_key_masked="****4321"`。
- [x] **5** `PUT` 后 `config/overrides.yaml` 出现对应键；`global.yaml` 逐字节未变
  （`tests/test_api_server.py` 钉住）；重启后端后 `source` 变为 `overrides`，值生效。
- [x] **6** 设 `VL_LLM_PROVIDER=remote` 启动 → `llm.provider.locked=true`、
  `locked_by=="VL_LLM_PROVIDER"`、`source=="env"`；`PUT` 该字段 → **409** +
  `ignored[{role:"llm", field:"provider", reason:"env_locked"}]`，`overrides.yaml` 中不含该键。
- [ ] **7（GUI 半边）** stub 常驻警告条与「无法确认模型配置」降级 —— 组件已实现、`tsc`/`build`
  通过，**浏览器实测未执行**（见「未闭环项」）。
- [x] **8** 未带 token 时新端点 **401**；带 token 时全部 **200**；`api.ts` 的 `Authorization`
  注入按 7.3 实现（构建期 `VITE_AUTH_TOKEN` 优先于 localStorage）。
- [ ] **9（浏览器实测）** 未执行，改用等价的 HTTP 层验证 —— `OPTIONS /api/config` 返回 **200**、
  `access-control-allow-methods: GET, POST, PUT`、`access-control-allow-origin:
  http://localhost:5173`，正是浏览器判定预检成功所看的三项。此项实测暴露了「预检被鉴权拦掉」的
  真实缺陷，已修。
- [x] **10** `npx tsc --noEmit` 0 错误；`npm run build` 成功（3055 modules / 14.30s）。
- [x] **11** 密文库内 `grep -a` 搜不到原 key；扫描整个工作区（含 `data/`、全部 JSONL、
  `GET /api/config` 与 `GET /api/diagnostics` 响应）无任何明文。措辞按实际修订为「不含**非空**
  `api_key` 值」（`global.yaml` 保留 `api_key: ""` 作文档占位）。
- [x] **12** `secret-keygen` 输出可被 `VL_ANCHOR_SECRET_KEY_FILE` 直接使用，且运行前后工作区无
  任何新增/修改文件（`tests/test_cli.py`）。
- [x] **13** 无主密钥时 `doctor` 退出 **0**、`config.paths.secret_store` 为 `skip`、
  `config/secrets.db` 不被创建、`PUT` 带 `api_key` → `ignored(no_secret_store)`。
- [x] **14** 主密钥与既有密文不匹配时 `doctor` 退出 **1**、`secret_store` 为 `fail` 且 hint 给出
  两条修复路径；进程不崩溃，`GET /api/diagnostics` 与 `GET /api/config` 均 200。
- [x] **15** `VL_ANCHOR_DB_PATH` 指向 `config/secrets.db` 时拒绝写入、不覆盖索引库、
  `secret_store` 为 `fail`（判定先于主密钥）：`tests/test_secrets.py` / `tests/test_diagnostics.py`。
- [x] **16** `secret-set` 从 stdin 读值、空行删除、成功与失败均不回显值；token 入库后
  `config.server.auth_token` 为 `warn` 且 hint 含解锁步骤（`tests/test_cli.py` /
  `tests/test_diagnostics.py`）。
- [x] **17** 无主密钥时 `secret-set` 退出 **1**、提示先配主密钥、不创建 `config/secrets.db`。

**归档期按实测回改 spec 的三处**：`restart_required`（`bool(written)`，409 时为 false）、
验收 11 的「非空 `api_key` 值」、`rebuild()` 的收敛性判据（后者属 metadata-store）。

## 十二、未闭环项（诚实登记）

| 项 | 状态 | 说明 |
|----|------|------|
| 验收 7 / 8 / 9 的**浏览器半边** | ⚠️ open | stub banner、设置页保存后生效、「无法确认模型配置」降级这三条只在组件 + 构建层验证过；预检改用 `OPTIONS` HTTP 实测等价覆盖（正是该实测暴露了真实缺陷）。**本机无浏览器自动化环境**，下次人工走查必须补验 |
| `timeout_s` 等三字段无 env 覆盖 | open | ADR-001 已知限制：容器化部署无法按角色调超时 |
| L1 一键脚本探活撞 401 | **本次已修** | `start_gui.*` 探活改打 `/api/health`（原打 `/api/tasks`，启用 token 后 401 导致误判启动失败）。`specs/deployment/spec.md` 的 L1 条目待同步标注 |
| S4 gui 端口绑全网卡 | open | 属部署 spec，不在本能力范围 |

## 十三、风险与回退

| 风险 | 缓解 |
|------|------|
| 新增端点 + 模块冲击既有 99 个测试 | `src/config.py` 的 overrides 层在文件缺失时零行为变化；分阶段 apply（先配置/密钥层，再元数据层，最后 GUI）；实测净增 104 例无回归 |
| 配置写回引入安全面 | 非密字段才写 YAML；api_key 只进密文库且需外部主密钥；端点受鉴权；校验前置；`write()` 内自证复查 |
| 密文库被误当「免费加密」 | 主密钥绝不自动生成到磁盘；README 明写「无主密钥即降级为 env-only」；`doctor` 对 `BAD_MASTER_KEY` 报 fail |
| `cryptography` 依赖与容器构建 | 主依赖区新增；官方 wheel 覆盖 win/linux/manylinux，`uv sync` 与 `Dockerfile` 均无需额外系统包 |
| 主密钥轮换导致密文不可读 | `key_check` 哨兵把「解不开」与「没配」区分开，hint 给出「恢复旧密钥」或「清空重录」两条路径；**不阻断启动** |
| doctor 产生网络调用 | 默认 `deep=false`；探测 `max_retries=0` + ≤15s 超时；两次探测串行 |
| 容器内写回不持久被误认为已保存 | `config_persistent` 判定 + 常驻提示（不禁止保存） |

**回退**：删除 `config/overrides.yaml` 与 `config/secrets.db` 即回到改造前的配置行为；4 个新端点为
纯增量，不删不改既有端点；GUI 第 5 个 Tab 独立，移除不影响其余 4 个页面。

## 附录：关键位置速查

| 位置 | 用途 |
|------|------|
| `src/config.py:45-46` | `_env()` 空字符串视为未设置 —— 锁定语义的对齐基准 |
| `src/config.py` `load_settings()` | 三层加载 + `overrides.yaml` 层 + 密文库回退；`@lru_cache(maxsize=1)` |
| `src/core/config_store.py` | `ConfigStore.effective/write/validate_patch`；`_config_persistent()` |
| `src/utils/secrets.py` | `read_secret` / `SecretStore` / `generate_master_key` / `is_valid_master_key` |
| `src/core/diagnostics.py` | `Doctor.run()`、13 个 `CheckResult.id`、`_compute_state()`（路径冲突先于主密钥） |
| `src/agents/model_client.py` | `probe_endpoint()` / `ProbeResult`（`transport` 测试缝） |
| `src/api_server.py:37` | CORS `allow_methods`（含 `PUT`） |
| `src/api_server.py:53` | `_PUBLIC_PATHS = {"/api/health"}` |
| `src/api_server.py` `_is_preflight()` | 预检豁免（缺陷 1 的修复点） |
| `run.py` | `doctor` / `secret-keygen` / `secret-set` 与 `--task` 放宽 |
| `gui/src/services/api.ts` | `Authorization` 注入（`VITE_AUTH_TOKEN` → localStorage） |
| `gui/src/pages/SettingsPage.tsx` | 设置页全部行为要点（见 7.3） |
