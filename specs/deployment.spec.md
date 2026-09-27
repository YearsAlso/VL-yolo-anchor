# Spec: deployment（Linux Docker 自部署）

## 功能描述

将平台以 Linux Docker 方式交付给客户自部署：`docker compose up -d` 拉起
FastAPI 后端（纯 CPU）与 nginx 托管的 Web GUI；VL/LLM 通过可配置的远程
OpenAI 兼容端点（自定义 `base_url`/`api_key`/`model`）调用，镜像不含模型权重、
无需 GPU。`provider: stub` 时完全离线，用于演示与 CI。

## 输入约束

- 运行时配置优先级：环境变量（`VL_ANCHOR_*` / `VL_MODEL_*`）> 挂载的
  `config/global.yaml` > 内置默认（`src/config.py`）。
- 关键变量：`VL_ANCHOR_HOST/PORT`、`VL_ANCHOR_DATA_DIR`、`VL_ANCHOR_CONFIG_DIR`、
  `VL_ANCHOR_CORS_ORIGINS`、`VL_ANCHOR_AUTH_TOKEN`。
- 模型分两角色：`llm`（驱动 PlanAgent，文本）与 `vl`（驱动 AnnotateAgent，视觉）；
  InspectAgent 不用模型。共享变量 `VL_MODEL_PROVIDER/BASE_URL/API_KEY/NAME`，
  分角色覆盖 `VL_LLM_*` / `VL_VL_*`（未设则继承共享）。
- 后端镜像多阶段构建，非 root 运行；GUI 镜像 `npm ci → vite build → nginx`。
- compose 的 `env_file: .env` 为可选（`required: false`），未复制 `.env` 也能启动。

## 输出约束

- `GET /api/health` 返回 `{"status":"ok"}`，供 Docker HEALTHCHECK 探活。
- 容器内后端绑定 `0.0.0.0:8765`；任务/日志写入卷 `/data`。
- GUI 构建注入 `VITE_API_BASE=""`，前端走同源 `/api`，由 nginx 反代到 backend，
  免 CORS。对外仅暴露 nginx（默认 `:8080`），后端端口仅回环映射。
- `remote` 模式解析 OpenAI ChatCompletion 响应的 `choices[0].message.content`；
  annotate 从其文本中抽取 JSON 数组解析为 OBB detections。

## 边界条件

- `provider: remote` 但 `base_url`/`model` 缺失 → 记录 warning 并回退 stub，不崩溃。
- 设置了 `VL_ANCHOR_AUTH_TOKEN` → 除 `/api/health` 外所有 `/api/*` 需
  `Authorization: Bearer <token>`，否则 401；未设置则关闭鉴权（本地/测试兼容）。
- 数据目录不可写 → 启动/写入失败属部署配置问题（文档要求卷可写）。
- api_key 仅从环境变量读取，不写日志、不入库、不进镜像。

## 验收标准

1. `docker compose config -q` 通过；`docker build .` 与 gui 镜像可构建。
2. `docker compose up -d` 后 `curl localhost:8765/api/health` 返回 200，
   `/api/tasks` 可用；浏览器 `:8080` 渲染真实 OBB 框。
3. CI（`ci.yml`）：`uv run ruff check src/ run.py tests/`、`uv run mypy src/`、
   `uv run pytest`、GUI `tsc --noEmit` + `npm run build` 全绿。
4. `docker.yml` 在 tag/主分支构建并推送 GHCR，PR 仅构建。

> 实现：`src/config.py`、`src/agents/model_client.py`、`src/api_server.py`、
> `src/core/pipeline.py`、`Dockerfile`、`gui/Dockerfile`、`gui/nginx.conf`、
> `docker-compose.yml`、`.env.example`、`.github/workflows/*`。
