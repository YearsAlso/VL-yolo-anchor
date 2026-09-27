# Spec: deployment（Linux Docker 自部署）

> **状态**：已实施（as-built，从 `specs/deployment.spec.md` 迁移并重组为六要素）
> **设计**：[`design.md`](design.md)（吸收了原 `2026-09-27-docker-deployment` 提案的交付物与验收对照）
> **实施栈**：后端 + 运维
> **最后核对**：2026-09-27

## 一、业务背景

把平台以 Linux Docker 方式交付给客户自部署：`docker compose up -d` 拉起 FastAPI 后端（**纯 CPU**）与 nginx 托管的 Web GUI。VL/LLM 通过可配置的远程 OpenAI 兼容端点（`base_url` / `api_key` / `model`）调用，**镜像不含模型权重、无需 GPU**；`provider: stub` 时完全离线，用于演示与 CI。

## 二、功能范围

**包含**

- 后端两阶段镜像、GUI 构建 + nginx 反代镜像、compose 编排
- 三层配置（env > 挂载 `config/global.yaml` > 内置默认）
- `/api/health` 探活、可选 Bearer 鉴权、env 驱动 CORS
- CI/CD：`ci.yml`（lint/type/test/tsc + 资产门禁）、`docker.yml`（构建，`v*`/主分支推 GHCR）、`release.yml`

**不包含**

- 编排式部署（K8s、 Swarm）、TLS 终结（由客户侧反代负责）
- GPU 推理镜像（4-bit 本地推理走 `[gpu]` extra，属可选路径，非默认交付形态）

## 三、接口契约

| 项 | 契约 |
|----|------|
| 探活 | `GET /api/health` → `{"status":"ok"}`，**免鉴权**（`_PUBLIC_PATHS`，`api_server.py:53`） |
| 其余 `/api/*` | 设置 `VL_ANCHOR_AUTH_TOKEN` 后需 `Authorization: Bearer <token>`，否则 **401**；未设置则鉴权关闭 |
| 对外端口 | nginx `:8080`（唯一对外入口）；后端 `8765` **仅回环映射** |
| 同源策略 | GUI 构建注入 `VITE_API_BASE=""` ⇒ 前端走同源 `/api`，由 nginx 反代到 `backend:8765`，**免 CORS** |
| nginx | `client_max_body_size 100m`（大图）、`proxy_read_timeout 300s`（长推理）、SPA fallback、`/assets/` 缓存 30d |
| remote 响应解析 | OpenAI ChatCompletion 的 `choices[0].message.content`；annotate 从其文本抽取 JSON 数组解析为 OBB detections |

## 四、数据模型（配置与卷）

| 键 | env | 默认 | 说明 |
|----|-----|------|------|
| 绑定地址 | `VL_ANCHOR_HOST` | `127.0.0.1`（容器内注入 `0.0.0.0`） | 容器内靠端口映射收口 |
| 端口 | `VL_ANCHOR_PORT` | `8765` | |
| 可写根 | `VL_ANCHOR_DATA_DIR` | `.`（容器内 `/data`） | 命名卷 `vl-data` 挂载 |
| 配置目录 | `VL_ANCHOR_CONFIG_DIR` | `config`（容器内 `/app/config`） | |
| CORS | `VL_ANCHOR_CORS_ORIGINS` | 内置白名单 | 逗号切分 |
| 鉴权 | `VL_ANCHOR_AUTH_TOKEN` | `""`（关闭） | |
| 基座模型 | `VL_MODEL_PROVIDER` / `_BASE_URL` / `_API_KEY` / `_NAME` | `stub` / 空 | 单端点部署只填这组 |
| LLM 角色 | `VL_LLM_PROVIDER` / `_BASE_URL` / `_API_KEY` / `_NAME` | 继承基座 | 驱动 `PlanAgent`（文本） |
| VL 角色 | `VL_VL_PROVIDER` / `_BASE_URL` / `_API_KEY` / `_NAME` | 继承基座 | 驱动 `AnnotateAgent`（视觉） |

`InspectAgent` **不使用模型**（纯规则计算）。

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 6 资源目标 | 交付形态以远程 API 为主 ⇒ 纯 CPU 即可运行；镜像**不内置权重**；4bit + CPU 回退属可选 `[gpu]` 路径 |
| 7 密钥与路径 | `api_key` **仅从环境变量读取**，不写日志、不入镜像、不入库；容器内路径全部相对 `/data`、`/app/config` |
| 7 包管理 | builder 阶段用 `uv export --frozen` 从 `uv.lock` 导出临时 requirements 再 `pip install --prefix`；**运行层不保留 requirements 作为依赖源**（不违反 uv 硬约束，见技术架构第七节的说明） |
| 容器安全 | 运行层 `USER app`（`groupadd`/`useradd`，`VOLUME /data` 属主为 app）⇒ 非 root |

## 六、边界条件

- `provider: remote` 但 `base_url` / `model` 缺失 → 记 warning 并**回退 stub**，不崩溃
- 未复制 `.env` 也能启动：compose 的 `env_file: .env` 配 `required: false`
- 数据目录不可写 → 启动/写入失败，属部署配置问题（文档要求卷可写）
- ⚠️ **L1**：`start_gui.*` 的探活打 `/api/tasks`，启用鉴权后返回 401 ⇒ 本地一键脚本在配置了 token 时会误判启动失败（见 `specs/gui-launch-scripts/spec.md`）
- ⚠️ **S4**：compose 的 gui 端口映射 `"8080:80"` **无 `127.0.0.1:` 前缀** ⇒ 绑全网卡（backend 已正确收口）

## 七、验收标准

1. `docker compose config -q` 通过；后端与 GUI 镜像可构建 ✅（本机已验证）
2. `docker compose up -d` 后 `:8080` 浏览器渲染**真实 OBB 框**、`/api/tasks` 可用 ⚠️（**从未在目标机验证**，见 design 的「未闭环项」）
3. CI：ruff / mypy / pytest / `tsc --noEmit` + build 全绿 ✅
4. `docker.yml` 在 tag 与主分支构建并推送 GHCR，PR 仅构建 ✅（条件与矩阵已定义）

## 实现位置

`src/config.py`、`src/agents/model_client.py`、`src/api_server.py`、`src/core/pipeline.py`、`Dockerfile`、`gui/Dockerfile`、`gui/nginx.conf`、`docker-compose.yml`、`.env.example`、`.dockerignore`、`Makefile`、`.github/workflows/{ci,docker,release}.yml`
