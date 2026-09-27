# Proposal: Linux Docker 自部署 + 远程可配置模型

- 日期：2026-09-27
- 范围：把项目改造成客户可 `docker compose` 自部署的 Linux 方案；VL/LLM 从本地权重
  改为可配置的远程 OpenAI 兼容端点（base_url / api_key / model），镜像纯 CPU、不含权重。
- 对应 spec：`specs/deployment.spec.md`（新增能力）。

## 交付物

- 配置层：`src/config.py`（env > global.yaml > defaults）；`api_server.py`/`run.py`/
  `pipeline.py` 全面接入；`config/global.yaml` 扩展 `model.provider/base_url/...`、
  `paths.data_dir`。
- 模型：`src/agents/model_client.py`（`OpenAICompatClient` + `build_model_client`）；
  `annotate_agent`/`plan_agent` 支持 `provider=stub|remote`。
- 安全：`/api/health`；可选 Bearer 鉴权中间件；CORS 由 env 驱动。
- 容器：`Dockerfile`（后端多阶段非 root）、`gui/Dockerfile`（vite→nginx）、
  `gui/nginx.conf`、`docker-compose.yml`、`.dockerignore`、`gui/.dockerignore`、
  `.env.example`、`Makefile`；`api.ts` 支持 `VITE_API_BASE`。
- CI/CD：`.github/workflows/{ci,docker,release}.yml`。
- 文档：`LICENSE`、`CHANGELOG.md`、`SECURITY.md`、README Docker 章节、`.gitignore`。

## 基线复跑（M1 后端改动后）

- `uv run ruff check src/ run.py tests/`：All checks passed
- `uv run mypy src/`：Success, 17 files
- `uv run pytest -q`：94 passed
- `cd gui && npx tsc --noEmit`：ExitCode 0
- `cd gui && npm run build`：ExitCode 0（dist 产出）
- `docker compose config -q`：ExitCode 0

## 验收对照 deployment.spec.md

- [x] 验收 1：compose 配置合法；镜像可构建（Dockerfile/nginx 配置就绪）
- [ ] 验收 2：`docker compose up` 后 8080 出框 —— 需目标机执行（本机验证到 compose config + build 步骤）
- [x] 验收 3：CI 步骤对应命令全部本地通过
- [x] 验收 4：docker.yml 的 build/push 条件与矩阵已定义
