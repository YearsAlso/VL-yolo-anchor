# Spec: gui-launch-scripts

## 功能描述

`start_gui.ps1`（Windows PowerShell）与 `start_gui.sh`（POSIX bash）一键启动 FastAPI 后端（127.0.0.1:8765）与 `gui/` 下的 Vite 开发服务器，退出时终止后端进程。

## 输入约束

- 项目根目录存在 `pyproject.toml` 与 `gui/`；后端入口 `src.api_server:app`。
- 无必选参数；`-SkipInstall` / `--skip-install` 可跳过依赖安装检查。

## 输出约束

- 后端监听 `127.0.0.1:8765`，前端为 Vite 默认端口 5173。
- 失败以非零退出码报告；正常中断（Ctrl+C）后无残留后端进程。

## 边界条件

- `.venv` 缺失 → 打印 `uv sync --extra dev` 提示并以非零码退出，不静默创建环境。
- `gui/node_modules` 缺失 → 自动执行 `npm install`。
- 8765 被占用 → 后端启动失败，非零退出。

## 验收标准

1. 两文件存在，sh 具备 POSIX shebang，`bash -n start_gui.sh` 通过。
2. 无硬编码绝对路径（一律基于脚本所在目录）。
3. 前端退出后后端进程被清理。
