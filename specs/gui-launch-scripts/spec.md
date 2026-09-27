# Spec: gui-launch-scripts

> **状态**：已实施（as-built，从 `specs/gui-launch-scripts.spec.md` 迁移并重组为六要素）
> **设计**：[`design.md`](design.md)
> **实施栈**：跨栈（脚本编排后端与前端进程）
> **最后核对**：2026-09-27

## 一、业务背景

`start_gui.ps1`（Windows PowerShell，84 行）与 `start_gui.sh`（POSIX bash，71 行）一键启动 FastAPI 后端（`127.0.0.1:8765`）与 `gui/` 下的 Vite 开发服务器，并在退出时终止后端进程 —— 避免留下孤儿进程占着 8765。

## 二、功能范围

**包含**

- 前置检查：`pyproject.toml` 与 `gui/` 存在、`.venv` 存在、8765 未被占用
- 后端启动与**真实 PID 跟踪**、健康检查轮询
- 前端启动与 `node_modules` 自动安装
- 退出时的进程树清理与**退出码传播**

**不包含**

- 生产部署（走 Docker，见 `specs/deployment/`）
- 依赖安装本身（缺失时**提示** `uv venv; uv sync --extra dev` 并非零退出，不静默创建环境）

## 三、接口契约

| 项 | 契约 |
|----|------|
| 用法 | `./start_gui.sh [--skip-install]` / `./start_gui.ps1 [-SkipInstall]` |
| 后端 | `python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8765`（用 `.venv` 的解释器） |
| 前端 | Vite 默认端口 5173 |
| 探活 | `GET http://127.0.0.1:8765/api/tasks`，`--max-time 1` / `-TimeoutSec 1` |
| 端口探测 | sh 用 `lsof -nP -iTCP:8765 -sTCP:LISTEN`；ps1 用 `Get-NetTCPConnection -LocalPort 8765 -State Listen` |
| 清理 | sh `trap cleanup EXIT INT TERM`（`:38`）+ `kill` + `wait`；ps1 `taskkill /PID <id> /T /F`（`:35`） |
| shebang | `#!/usr/bin/env bash`（sh 第 1 行），**必须 LF 行尾**（`.gitattributes` 已钉 `eol=lf`，CRLF 会让 shebang 解析为 `bash\r` 而直接失效） |

## 四、数据模型（磁盘产物与状态）

无持久化产物。涉及的磁盘判定：

| 检查 | 行为 |
|------|------|
| `.venv` 缺失 | 打印 `uv venv; uv sync --extra dev` 提示，非零退出 |
| `gui/node_modules` 缺失 | 自动 `npm install`（`--skip-install` 可跳过）；ps1 中 `npm install` 失败即非零退出 |
| 一切路径 | 基于脚本所在目录推导（`$ROOT` / `$PSScriptRoot`），**无硬编码绝对路径** |

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 7 全相对路径 | 不得出现绝对路径字面量；根目录由脚本位置推导 |
| 6 资源目标 | 本地起的后端为纯 CPU + 远程模型端点，脚本不假设 GPU 环境 |
| 5 职责边界 | 脚本只做进程编排，不做任何 CV/IO 逻辑 |

## 六、边界条件

| 情形 | 行为 |
|------|------|
| 8765 被占用 | **启动前**拦截，非零退出（不等后端起来再失败） |
| `.venv` 缺失 | 提示 + 非零退出，不静默创建环境 |
| `node_modules` 缺失 | 自动安装（除非显式 skip） |
| 后端过早死亡 | 轮询期间检测进程已退出，立即终止并非零退出 |
| 后端未通过健康检查 | 杀掉已起进程，非零退出 |
| Ctrl+C 正常中断 | 无残留后端进程 |
| Vite 退出码非零 | 传播为脚本退出码（ps1 `:74`/`:84`，sh `:68`/`:71`） |

## 七、验收标准

1. 两文件存在；`bash -n start_gui.sh` 通过，PowerShell AST 解析通过
2. 无硬编码绝对路径
3. 跟踪的是 `.venv` 的 **python 进程**而非 `uv` 父进程；前端退出后 `Get-NetTCPConnection -LocalPort 8765` 为空 / `lsof` 无监听
4. 8765 被占用时以非零码退出
5. Vite 非零退出码被正确传播

## 已知缺陷

🔴 **L1（新发现，2026-09-27）**：探活打的是 `GET /api/tasks`，而鉴权中间件的公开路径集合只有 `{"/api/health"}`（`src/api_server.py:53`、判断在 `:70`）⇒ **一旦设置 `VL_ANCHOR_AUTH_TOKEN`，探活请求返回 401，`curl -fs` 失败，脚本会误判「后端未就绪」而终止**。默认（token 为空）不触发，故本地开发未暴露。修复方向：探活改打 `/api/health`（该端点正是为此而生，且免鉴权）。已登记 `docs/audit-reports/README.md`。

## 实现位置

`start_gui.sh`（71 行）、`start_gui.ps1`（84 行）
