# Design: deployment

> **状态**：已实施（as-built）
> **关联 spec**：[`spec.md`](spec.md)
> **关联 ADR**：ADR-001（LLM/VL 配置拆分 —— 本设计的前置）
> **实施栈**：后端 + 运维
> **契约冻结点**：env 变量集合与 `/api/health` 探活语义已冻结
> **最后核对**：2026-09-27
> **来源**：吸收原变更提案 `2026-09-27-docker-deployment/proposal.md` 的交付物清单与验收对照（该提案随 SDD 结构改造并入本文件，`changes/` 中间态已废弃）

## 一、执行摘要

把"必须装 CUDA + 下模型权重"的交付，换成"compose 起两个容器 + 填一个远程端点"。核心手段是**配置层收口**（`src/config.py` 三层优先级）+ **模型客户端可替换**（`VLClient` 协议的 stub/remote 双实现）+ **同源反代**（nginx 消掉 CORS）。代价：真实推理链路的质量只能在客户侧端到端验证，本机无法闭环。

## 二、交付物（as-built）

| 类别 | 内容 |
|------|------|
| 配置层 | `src/config.py`（env > `config/global.yaml` > 默认）；`api_server.py` / `run.py` / `pipeline.py` 全面接入；`config/global.yaml` 扩展 `model.provider/base_url/...` 与 `paths.data_dir` |
| 模型 | `src/agents/model_client.py`（`OpenAICompatClient` + `build_model_client`）；`annotate_agent` / `plan_agent` 支持 `provider=stub|remote` |
| 安全 | `/api/health` 探活；可选 Bearer 鉴权中间件；CORS 由 env 驱动 |
| 容器 | `Dockerfile`（后端多阶段、非 root）、`gui/Dockerfile`（vite → nginx）、`gui/nginx.conf`、`docker-compose.yml`、`.dockerignore`、`gui/.dockerignore`、`.env.example`、`Makefile`；`api.ts` 支持 `VITE_API_BASE` |
| CI/CD | `.github/workflows/{ci,docker,release}.yml` |
| 文档 | `LICENSE`、`CHANGELOG.md`、`SECURITY.md`、README 的 Docker 章节、`.gitignore` |

## 三、关键设计决策

| 决策 | 选项 | 选择 | 理由 | 被否选项的代价 |
|------|------|------|------|---------------|
| 模型形态 | 镜像内置权重 / 远程 API | **远程 OpenAI 兼容端点为主** | 镜像不含权重 ⇒ 纯 CPU、体积小、无 CUDA 依赖，客户自选供应商 | 内置 ⇒ 镜像数十 GB、绑死单一模型、客户换模型要重建镜像 |
| 离线可用性 | 必须配端点 / stub 兜底 | **`provider: stub` 默认，remote 缺配置时回退 stub** | 演示与 CI 无需任何密钥即可跑通 | 强制配端点 ⇒ 首次体验失败，CI 依赖外部服务 |
| 配置优先级 | 隐式（`BaseSettings` 嵌套分隔符）/ 显式 `os.environ.get` + yaml 叠加 | **显式 `load_settings()`** | 优先级与 fail-fast 校验点可读可查；见 ADR-001 被否方案 | 隐式 ⇒ 排查"这个值到底从哪来"成本高 |
| 前后端通信 | 跨域 + CORS / 同源反代 | **同源 `/api` + nginx 反代** | 浏览器只见一个 origin ⇒ 免 CORS 预检、免 tainted canvas | 跨域 ⇒ CORS 配置错误变成"图看不到/框画不出"，现场难归因 |
| base 地址注入 | 运行期读配置 / 构建期 `VITE_API_BASE` | **构建期 ARG** | SPA 是静态产物，只能构建期定 | 运行期 ⇒ 静态文件里没有这个值，改地址要重新构建，本来就是这么做的 |
| 后端绑定 | 容器内 `127.0.0.1` / `0.0.0.0` | **容器内 `0.0.0.0` + 端口映射收口** | 容器内回环无法被 nginx 网内访问 | 绑回环 ⇒ 反代 502 |
| 8765 暴露 | 直接发布 / 仅回环 | **`"127.0.0.1:8765:8765"`** | 原始 API 只供本机测试，远程访问统一走反代 | 直接发布 ⇒ 绕过 nginx 与客户侧 TLS |
| 鉴权默认 | 强制 / 默认关闭 | **默认关闭**（`auth_token=""`） | 本地桌面与测试套件免配 token | 强制 ⇒ 一期所有测试与 `start_gui` 全部失败 |
| 构建工具链 | 镜像内 `pip install -e .` / `uv export` + `pip install --prefix` | **后者** | 复用 `uv.lock` 的确定性，同时不在运行层保留 requirements 作为依赖源 | 直接 pip 解析 ⇒ 与本地 uv 环境漂移 |
| `.env` 强制性 | 必需 / 可选 | **`required: false`** | 未复制 `.env` 也能起（走 stub），新用户友好 | 必需 ⇒ `docker compose up` 直接报错，劝退 |

## 四、拓扑

```
浏览器 ──▶ nginx :8080（唯一对外，S4：当前绑全网卡）
             ├─ location /api/ ─▶ backend:8765（反代，read timeout 300s，body 100m）
             └─ location /     ─▶ SPA 静态 + fallback
开发者 ──▶ 127.0.0.1:8765（compose 仅回环映射）
backend ─▶ /data（命名卷 vl-data，属主 app，非 root 运行）
        ─▶ 远程 OpenAI 兼容端点（VL_LLM_* / VL_VL_* / VL_MODEL_*）
```

## 五、基线复跑（M1 后端改动后，提案记录）

| 命令 | 结果 |
|------|------|
| `uv run ruff check src/ run.py tests/` | All checks passed |
| `uv run mypy src/` | Success，17 files |
| `uv run pytest -q` | **当时 94 passed**（现基线 **99 passed / 4.40s**，本次实测） |
| `cd gui && npx tsc --noEmit` | ExitCode 0 |
| `cd gui && npm run build` | ExitCode 0（dist 产出） |
| `docker compose config -q` | ExitCode 0 |

## 六、未闭环项（诚实登记）

| 项 | 状态 | 说明 |
|----|------|------|
| **spec 验收 2**：`docker compose up` 后在 `:8080` 看到**真实 OBB 框** | ⚠️ **从未在目标机验证** | 本机只验证到 `compose config` + 构建步骤。**这是端到端链路的空档**：nginx 反代、卷权限、远程端点联通性都只有真实部署才会暴露。下次部署演练必须补验并回填 |
| **S4** gui 端口绑全网卡 | open | `"8080:80"` 缺 `127.0.0.1:` 前缀；与 backend 的收口做法不一致 |
| **L1** 一键脚本探活撞 401 | open | 启用 `VL_ANCHOR_AUTH_TOKEN` 后 `start_gui.*` 会误判启动失败 |
| **D2** `timeout_s` 等三字段无 env 覆盖 | open | 容器化部署**无法按角色调超时**（ADR-001 已知限制） |
| **S5** 日志可能含提示词全文 | open | 部署侧日志若外发即泄露用户 description |

## 七、风险与回退

| 风险 | 等级 | 缓解 |
|------|------|------|
| `/data` 卷不可写 | 🟡 | 已在文档要求卷可写；容器以 `app` 用户运行，挂载时须对齐属主 |
| 远程端点超时被 nginx 截断 | 🟡 | `proxy_read_timeout 300s`；但 `timeout_s` 无 env 覆盖（D2），后端侧超时只能改 yaml 重建镜像 |
| 客户误配 `provider: remote` 却未填 base_url | 🟢 | 回退 stub + warning，不崩溃（但结果不是真检测，须在交付说明中强调） |

回退：compose 与镜像是纯增量资产，回退只需不再使用；`src/config.py` 的三层加载在无任何 env 时行为与改造前一致（走默认值 + yaml）。

## 附录：关键位置速查

| 位置 | 用途 |
|------|------|
| `Dockerfile:7-16` | builder：uv + `uv export --frozen` + `pip install --prefix=/install` |
| `Dockerfile:20` | `groupadd`/`useradd app` |
| `Dockerfile:33-38` | 运行期 env（`VL_ANCHOR_HOST=0.0.0.0`、`DATA_DIR=/data`、`CONFIG_DIR=/app/config`） |
| `Dockerfile:41` | `USER app` |
| `Dockerfile:43-44` | HEALTHCHECK 打 `/api/health` |
| `docker-compose.yml:29` | backend `"127.0.0.1:8765:8765"`（仅回环） |
| `docker-compose.yml:43` | gui `"8080:80"`（S4 现场） |
| `gui/nginx.conf:14-22` | `/api/` 反代与超时 |
| `gui/src/services/api.ts:6-14` | base 地址三级解析（注释即设计说明） |
| `src/api_server.py:53` | `_PUBLIC_PATHS = {"/api/health"}` |
