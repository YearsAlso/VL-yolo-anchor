# VL-YOLO-Anchor

通用工业缺陷 **YOLO-OBB 训练计划生成与智能标注平台**。

通过自然语言描述任务 → **PlanAgent** 生成结构化 YOLO-OBB 训练计划（类别、标注规则、超参数、质检规则）→ **AnnotateAgent** 调用本地 VL 模型（Qwen2.5-VL int4）自动旋转框标注 → **InspectAgent** 质检标签（漏标、重复、类别错误、越界、尺寸异常）并输出候选修正 → 导出可直接训练的 YOLO-OBB 数据集 + yaml + 训练命令。

## 核心约束

- OBB 输出为 **4 顺时针归一化角点** `[x1,y1,x2,y2,x3,y3,x4,y4]`，范围 `[0,1]`，不使用水平矩形。
- **永不覆盖原始标签**；所有修正写入 `candidate_labels/`。
- 任务间规则隔离：每个任务有独立的 classes/rules，agent 不跨任务混用规则。
- 显存目标：RTX 3060Ti（8GB），VL 模型 4-bit 加载，支持 CPU 回退。
- Python 后端负责全部 CV/推理/文件 IO；前端只做画布渲染 + UI。
- 所有 agent 系统提示词外置于 `prompts/*.yaml`，不硬编码。

## 目录结构

```
pyproject.toml          uv 包配置（ruff/mypy/pytest）
config/
  global.yaml           全局配置（模型、推理、路径、服务端口）
  task_template.yaml    任务配置模板（新建任务时拷贝）
  overrides.yaml        设置页/CLI 的写回结果（**运行时生成，不提交**）
  secrets.db            加密密钥库（**运行时生成，不提交**）
prompts/
  plan_agent.yaml       PlanAgent 提示词
  annotate_agent.yaml   AnnotateAgent 提示词
  inspect_agent.yaml    InspectAgent 提示词
  prompt_versions/      提示词版本归档
tasks/                  各任务目录（task.yaml / images / ai_labels /
                        candidate_labels / dataset / inspection_report.yaml / plan.yaml /
                        run_history.jsonl / model_calls.jsonl）
src/
  agents/               base_agent, plan_agent, annotate_agent, inspect_agent
  core/                 task_manager, pipeline, config_store, metadata_store, diagnostics
  api_server.py         FastAPI 后端（127.0.0.1:8765）
  utils/                obb_utils, image_utils, file_utils, yaml_utils, secrets
gui/                    Tauri + React + TypeScript + Ant Design 前端
run.py                  CLI 入口
start_gui.ps1 / .sh     一键启动后端 + GUI
index.db                SQLite 元数据索引（**可重建的投影，不提交**）
logs/diagnostics.jsonl  自检运行记录（不提交）
```

## 安装

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 同步基础依赖 + 开发工具
uv venv
uv sync --extra dev

# （可选）启用本地 VL 模型 GPU 推理
uv sync --extra gpu
```

## 初始化引导

首次运行（或换机器、换模型端点）时，先用自检确认配置状态：

```bash
uv run python run.py doctor          # 静态自检（不联网）
uv run python run.py doctor --deep   # 额外探测 llm/vl 两个端点的连通性
uv run python run.py doctor --json   # 机器可读，便于 CI / 脚本
```

退出码：`0` = 无失败项（允许 warn/skip）；`1` = 至少一项 fail；`2` = 用法错误。
`start_gui.ps1` / `start_gui.sh` 在后端健康后自动执行一次 `doctor`，出现 warn/fail 时打印
醒目警告，但**不阻断启动** —— 恰恰是配置有问题的时候，最需要能打开 GUI 去修。

### 设置页（GUI 第 5 个标签页）

- 两个模型角色（`llm` → PlanAgent、`vl` → AnnotateAgent）各自一行表单：`provider` /
  `base_url` / `model` / `api_key`，改完点「保存」写入 `config/overrides.yaml`。
- 每个字段都带**来源徽标**，说明这个值的出处：

  | 来源 | 含义 |
  | --- | --- |
  | 环境变量 | 由 env 提供，**已锁定**：置灰 + 🔒 + 显示变量名，写库无效 |
  | 设置页 | 来自 `config/overrides.yaml`（设置页 / CLI 写回） |
  | global.yaml | 来自 `config/global.yaml` |
  | 密文库 | 仅 `api_key` 可能来自 `config/secrets.db` |
  | 内置默认 | 代码内置默认值 |

  优先级：环境变量 > `overrides.yaml` > `global.yaml` > 内置默认（分角色 > 共享块）。
- **保存后需重启后端进程才生效**：配置在进程启动时计算并缓存，接口返回的
  `restart_required` 恒为 `true`。保存成功后设置页常驻这条提示，并把表单回填为**实际落盘的
  值** —— 不要用"回读接口看值变没变"判断是否保存成功。
- 「测试连接」按角色发一次最小请求，返回延迟、HTTP 状态码与修复建议，不发真实标注任务。
- `provider: stub` 时顶部常驻警告条（在任意标签页可见）：此时产出的是**确定性假框**，
  平台可跑通但结果不能用于训练。
- 配置目录只读时「保存」置灰并提示改用环境变量；容器内未挂载 `config/` 时提示写回不持久
  （见下文 Docker 一节）。

## 密钥管理

API key 与访问 token 都存在 `config/secrets.db` 中（**独立于索引库 `index.db`**），值由主密钥
对称加密（Fernet：AES-128-CBC + HMAC）后落盘 —— 库里 grep 不到任何明文。

主密钥**只**从 `VL_ANCHOR_SECRET_KEY` / `VL_ANCHOR_SECRET_KEY_FILE` 读取，不会写进 `.env`、
YAML 或命令行历史：

```bash
uv run python run.py secret-keygen                          # 只打印到 stdout，不写任何文件
uv run python run.py secret-keygen > secrets/vl_master_key   # 自行落盘并妥善保管
uv run python run.py secret-set VL_VL_API_KEY                # 值从 stdin 读，不回显
uv run python run.py secret-set VL_VL_API_KEY </dev/null     # 传空行 = 删除该密钥
```

Docker 部署用 `*_FILE` + Docker secret 把主密钥做成挂载文件（`docker-compose.yml` 里已备好
注释段，取消注释即可）：

```yaml
secrets:
  vl_master_key:
    file: ./secrets/vl_master_key
services:
  backend:
    environment:
      VL_ANCHOR_SECRET_KEY_FILE: /run/secrets/vl_master_key
    secrets: [vl_master_key]
```

三件必须先知道的事：

1. **主密钥丢失 = 密文不可恢复**。没有后门、没有重置；删除 `config/secrets.db` 重新录入即可，
   影响仅限于密钥本身（标注数据、任务配置都不在这个库里）。
2. 无主密钥**不是坏部署**：平台照常启动，`doctor` 该项为 SKIP，设置页密钥框禁用并说明原因，
   API key 只能来自 `VL_LLM_API_KEY` / `VL_VL_API_KEY`（含 `*_FILE`）。
3. `global.yaml` 里 `model.*.api_key` **会被忽略** —— 明文 key 写进 YAML 正是本机制要避免的事。

密钥读取优先级：`VL_{ROLE}_API_KEY[_FILE]` > `VL_MODEL_API_KEY[_FILE]` >
`secrets.db[role]` > `secrets.db[shared]`。注意**空字符串不算锁定**（与其他字段不同），
所以清掉环境变量后就能用设置页覆盖。

## 元数据与审计日志

**真相在磁盘 JSONL，不在数据库。** 所有运行记录都是只追加的文本日志：

| 文件 | 内容 |
| --- | --- |
| `tasks/<task>/run_history.jsonl` | 每次 plan/annotate/inspect/split 的起止时间、耗时、产出计数、失败原因 |
| `tasks/<task>/model_calls.jsonl` | 每次模型调用的角色 / provider / 模型 / 主机 / 状态 / HTTP 码 / 耗时（**不含** prompt 全文与密钥） |
| `logs/diagnostics.jsonl` | 每次 `doctor` 自检的结果 |

`index.db` 是这些日志的**可重建投影**，只为跨任务聚合查询（`GET /api/index/stats`）而存在：

```bash
uv run python run.py index --rebuild      # 按日志重放索引
```

- **删库无损**：任务流程（`/api/tasks`、plan/annotate/inspect/split、images/labels/history）
  全部不读数据库，删掉 `index.db` 平台照常运行，最多 `/api/index/stats` 返回 503。
  设 `VL_ANCHOR_DB_ENABLED=0` 可完全不建库。任务历史走 `/api/tasks/{name}/history`，直接读
  磁盘日志，与索引库无关。
- 密钥库 `config/secrets.db` 与索引库是**两个独立文件**：`index --rebuild` 不碰密钥库，
  重建才不会把加密的 key 抹掉。

## CLI 使用

```bash
# 创建任务
uv run python run.py create --task demo --description "光伏 EL 图像：crack / break_grid 缺陷"

# 逐步执行
uv run python run.py plan     --task demo
uv run python run.py annotate --task demo
uv run python run.py inspect  --task demo
uv run python run.py split    --task demo

# 或一次性全流程
uv run python run.py full     --task demo

# 平台维护（不需要 --task）
uv run python run.py doctor [--deep] [--json]   # 配置自检（见「初始化引导」）
uv run python run.py index --rebuild            # 按审计日志重建元数据索引
uv run python run.py secret-keygen              # 生成主密钥（只打印，不写文件）
uv run python run.py secret-set NAME            # 录入/删除密钥（值从 stdin 读）
```

图片放置在 `tasks/<task>/images/` 后执行 `annotate`。`split` 会在
`tasks/<task>/dataset/` 生成 `train/val/test` 目录、`data.yaml` 与
`train_command.txt`（可直接粘贴运行的 `yolo obb train ...` 命令）。

## FastAPI 后端

```bash
uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8765
```

主要端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/tasks` | 列出 / 创建任务 |
| POST | `/api/tasks/{name}/step` | 运行单步（plan/annotate/inspect/split） |
| POST | `/api/tasks/{name}/plan` `…/annotate` `…/inspect` | 单步快捷端点 |
| GET | `/api/tasks/{name}/plan` | 当前训练计划 |
| GET | `/api/tasks/{name}/report` | 最新质检报告 |
| GET | `/api/tasks/{name}/export` | 数据集导出摘要 |
| GET | `/api/tasks/{name}/labels` | 有标签文件的图片名列表 |
| GET | `/api/tasks/{name}/labels/{img}` | 单图 OBB 标签（candidate_labels 优先） |
| GET | `/api/tasks/{name}/images` `/images/{img}` | 图片列表 / 图片文件 |
| GET | `/api/tasks/{name}/history` | 执行历史（读磁盘 JSONL，不依赖数据库） |
| GET | `/api/config` | 生效配置 + 每个字段的来源/锁定信息（**永不返回 api_key 明文**） |
| PUT | `/api/config` | 写回 `overrides.yaml`；422 校验失败 / 409 无可写字段 |
| POST | `/api/config/test` | 按角色探测端点连通性（404 未知角色 / 422 缺 base_url） |
| GET | `/api/diagnostics` | 自检报告（`?deep=true` 附带端点探测） |
| GET | `/api/index/stats` | 跨任务索引计数（库不可用 → 503） |

除 `/api/health` 外，全部端点都受 `VL_ANCHOR_AUTH_TOKEN` 保护（设置后需带
`Authorization: Bearer <token>`）。

交互式文档：<http://127.0.0.1:8765/docs>

## GUI 启动

```powershell
# Windows
./start_gui.ps1
```

```bash
# Linux / macOS
./start_gui.sh
```

脚本会启动 FastAPI 后端（8765）、跑一次 `doctor` 自检，再在 `gui/` 中启动 Vite 开发服务器
（Tauri 打包用 `npm run tauri dev`）。GUI 含五个标签页：任务与计划、标注审核（OBB 画布，
缩放/平移）、质检报告（含执行历史）、训练导出、**设置**（模型配置 / 密钥 / 平台自检）。

开了 `VL_ANCHOR_AUTH_TOKEN` 时，在「设置」页粘贴 token 即可（只存本浏览器 localStorage）；
也可在构建期通过 `VITE_AUTH_TOKEN` 固定。

## Docker 自部署（Linux）

推荐的生产部署方式：后端镜像为纯 CPU（VL/LLM 通过可配置的远程 OpenAI 兼容端点调用，
镜像不含模型权重、无需 GPU），前端为 nginx 托管的静态 SPA 并反向代理 `/api`。

```bash
cp .env.example .env    # 按需修改 VL_MODEL_* / VL_ANCHOR_*
docker compose up -d --build
# 浏览器打开 http://localhost:8080
```

- `backend`：FastAPI，仅回环暴露 `127.0.0.1:8765`；任务与日志写入命名卷 `vl-data:/data`。
- `gui`：nginx 提供静态站（`:8080`），`/api` 反代到 backend，同源免 CORS。
- 对外访问请用带 TLS 的反向代理置于 `gui` 前，并设置 `VL_ANCHOR_AUTH_TOKEN` 开启鉴权。
- **容器内写回不持久**：镜像自带 `config/`（镜像层），设置页保存的 `config/overrides.yaml`
  会随容器重建而丢失。设置页会检测到这一点并把「保存」置灰、提示改用环境变量；要持久化
  配置，取消 `docker-compose.yml` 中 `- ./config:/app/config` 的注释（挂上 bind mount 后
  检测为持久）。
- **密钥库**：镜像里不含任何密钥（`.dockerignore` 排除了 `config/secrets.db` 与
  `overrides.yaml`）。按「密钥管理」一节取消 `secrets:` / `VL_ANCHOR_SECRET_KEY_FILE`
  的注释即可；不配主密钥平台也能跑，只是密钥只能来自环境变量。

### 模型后端配置

平台区分两类模型角色（由 `model:` 共享默认 + `llm:`/`vl:` 分角色覆盖，
或环境变量 `VL_MODEL_*` 共享 + `VL_LLM_*`/`VL_VL_*` 分角色）：

| 角色 | 驱动的 Agent | 模型类型 | 用途 |
| --- | --- | --- | --- |
| `llm` | PlanAgent | 文本 LLM | 自然语言描述 → 结构化训练计划 |
| `vl` | AnnotateAgent | 视觉语言模型 | 图像 → OBB 检测框 |

InspectAgent 为纯规则质检，不调用任何模型。`provider` 取值：

| provider | 行为 |
| --- | --- |
| `stub`（默认） | 离线确定性伪输出，无需模型/网络，用于演示与 CI |
| `remote` | 调用对应 `base_url` 的 OpenAI 兼容 `/chat/completions` 端点 |

未单独配置的会自动回退：`llm`/`vl` 缺字段时继承共享 `model:` 块，因此单端点部署
无需逐角色配置；而典型生产部署可让 plan 走便宜文本模型、annotate 走自建
Qwen2.5-VL。配置优先级：分角色 env (`VL_LLM_*`/`VL_VL_*`) > 分角色 yaml >
共享 env (`VL_MODEL_*`) > 共享 yaml `model:` > 内置默认（见 `src/config.py`）。

常用镜像标签：`ghcr.io/<owner>/vl-yolo-anchor-backend` 与 `…-gui`（`latest` / `v*` / sha）。

## 质量检查

```bash
uv run ruff check src/ run.py   # 零告警
uv run mypy                     # strict 模式，零错误
uv run pytest                   # 测试（tests/）
make doctor                     # 配置自检（= uv run python run.py doctor）
```

## 当前状态 / 已知限制

- **默认 `provider: stub` 使用确定性推理**（`StubLLMClient` / 固定伪检测框），用于离线
  跑通全流程与 CI；设 `provider: remote` 后 `PlanAgent`/`AnnotateAgent` 经
  `OpenAICompatClient` 调用可配置的远程 VL/LLM 端点（自定义 base_url / api_key / model）。
- `gui/` 与 `gui/src-tauri/` 依赖未随仓库提交，需先 `npm install`
  （`npm run tauri dev` 另需 Rust toolchain）。
- SDD 流程文档见 `docs/sdd-workflow.md`；正式 spec 位于 `specs/`，
  变更历史位于 `changes/`。
