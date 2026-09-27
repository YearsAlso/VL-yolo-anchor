---
kind: build_system
name: 多语言构建系统：Python (uv/setuptools) + Vite/Tauri + Rust
category: build_system
scope:
    - '**'
source_files:
    - pyproject.toml
    - uv.lock
    - run.py
    - gui/package.json
    - gui/vite.config.ts
    - gui/src-tauri/Cargo.toml
    - gui/src-tauri/tauri.conf.json
    - start_gui.sh
    - start_gui.ps1
---

## 1. 使用的构建体系

仓库采用**多语言、分层式**的构建方案，没有统一的顶层 Makefile 或 CI 流水线，而是按子项目各自维护构建配置：

- **Python 后端**：`pyproject.toml` + `setuptools.build_meta`（`[build-system]`），依赖管理使用 `uv`（根目录存在 `uv.lock`）。
- **前端 GUI**：Vite 5 + React + TypeScript，通过 `gui/package.json` 的 scripts 驱动；Tauri v1 作为桌面壳，Rust 侧由 `gui/src-tauri/Cargo.toml` 管理。
- **CLI 入口**：`run.py` 提供 `plan/annotate/inspect/split/full/create` 六个命令，通过 `argparse` 解析参数。
- **开发启动脚本**：`start_gui.sh`（POSIX）与 `start_gui.ps1`（PowerShell）双实现，一键拉起 FastAPI 后端 + Vite dev server。

未发现 Dockerfile、Makefile、GitHub Actions / GitLab CI 等 CI/CD 配置文件。版本统一为 `0.1.0`，分布在 `pyproject.toml`、`gui/package.json`、`gui/src-tauri/Cargo.toml`、`gui/src-tauri/tauri.conf.json` 四份文件中。

## 2. 关键文件

| 文件 | 作用 |
|---|---|
| `pyproject.toml` | Python 包元数据、依赖、`ruff`/`mypy`/`pytest` 工具链配置 |
| `uv.lock` | uv 锁定的 Python 依赖快照 |
| `run.py` | CLI 入口，封装 Pipeline 的六种子命令 |
| `gui/package.json` | Vite/Tauri/React 依赖与 npm scripts |
| `gui/vite.config.ts` | Vite 开发服务器（端口 5173）、`/api` 代理到 `http://127.0.0.1:8765` |
| `gui/src-tauri/Cargo.toml` | Tauri/Rust 依赖，`rust-version = "1.70"` |
| `gui/src-tauri/tauri.conf.json` | Tauri 打包产物、窗口尺寸、允许访问的后端范围 `http://127.0.0.1:8765/*` |
| `start_gui.sh` | POSIX 一键启动脚本 |
| `start_gui.ps1` | Windows 一键启动脚本 |

## 3. 架构与约定

### Python 后端
- 包名 `vl-yolo-anchor`，要求 `requires-python = ">=3.11"`。
- 运行时依赖集中在 `dependencies`，GPU 可选依赖在 `[project.optional-dependencies] gpu`（torch/torchvision/transformers/accelerate/bitsandbytes），开发依赖在 `dev`（ruff/mypy/pytest/httpx）。
- 构建后端固定为 `setuptools.build_meta`，未启用 PEP 621 的 `tool.setuptools.*` 扩展，仅用 `[build-system]` 声明。
- 代码质量工具全部内嵌于 `pyproject.toml`：`ruff`（line-length=120，target=py311，排除 gui/tasks/prompts）、`mypy`（strict=true，python_version=3.12，ignore_missing_imports=true）、`pytest`（testpaths=tests，pythonpath=.）。

### 前端 GUI
- `gui/package.json` 定义四个 script：`dev` → `vite`、`build` → `tsc && vite build`、`preview` → `vite preview`、`tauri` → `tauri`。
- Vite 开发服务器默认监听 5173，并通过 proxy 将 `/api` 请求转发到 `http://127.0.0.1:8765`（FastAPI 后端端口）。
- Tauri 的 `beforeDevCommand` 调用 `npm run dev`，`beforeBuildCommand` 调用 `npm run build`，`distDir` 指向 `../dist`，即 Vite 输出目录。
- Tauri 应用标识符 `com.vlyoloanchor.app`，产物名称 `VL-YOLO-Anchor`，窗口 1280×800，仅允许 HTTP 访问 `http://127.0.0.1:8765/*`。

### Rust/Tauri 壳
- `gui/src-tauri/Cargo.toml` 仅引入 `tauri`、`serde`、`serde_json`，无业务逻辑；`features.custom-protocol` 被声明但未在 `Cargo.toml` 中启用（需通过 `--features custom-protocol` 传入）。
- `tauri.conf.json` 的 `security.csp` 设为 `null`，即禁用 CSP。

### 开发启动流程
- `start_gui.sh` / `start_gui.ps1` 均遵循相同顺序：
  1. 检查 `.venv` 是否存在，不存在则退出并提示先执行 `uv venv; uv sync --extra dev`。
  2. 后台启动 `uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8765`。
  3. 若 `node_modules` 不存在且未传跳过标志，则执行 `npm install`。
  4. 前台运行 `npm run dev`（Ctrl+C 时清理后端进程）。
- PowerShell 版额外在 `npm install` 失败时显式终止后端进程。

### CLI
- `run.py` 通过 `argparse` 暴露 `plan/annotate/inspect/split/full/create` 六个子命令，`full` 串联全部步骤，`create` 新建任务目录。
- 日志级别通过 `--log-level` 控制，默认 `INFO`。

## 4. 约定与约束

- **Python 环境**：项目强制使用 `uv` 管理虚拟环境与依赖（脚本中直接调用 `uv run`、`uv venv`、`uv sync`），而非 `pip`/`virtualenv`。
- **后端端口**：FastAPI 后端固定绑定 `127.0.0.1:8765`，该端口在 `start_gui.sh`、`start_gui.ps1`、`gui/vite.config.ts`、`gui/src-tauri/tauri.conf.json` 四处以硬编码形式保持一致。
- **GUI 开发模式**：Vite dev server 必须与后端同时运行，前端通过 `/api` 代理访问后端；生产模式下 Tauri 通过 `allowlist.http.scope` 白名单限制可访问地址。
- **版本同步**：Python 包、前端 NPM 包、Tauri Rust crate、Tauri bundle 四处的版本号均为 `0.1.0`，但当前没有自动化同步机制，属于手动约定。
- **构建后端**：Python 构建后端锁定为 `setuptools.build_meta`（`pyproject.toml` 第 37 行）。
- **类型检查**：`mypy` 以 `strict = true` 运行，`disallow_untyped_defs = true`，所有 `src` 下的 Python 代码均需带类型注解。
- **Lint 规则**：`ruff` 启用 E/W/F/I/N/UP/B/SIM 规则集，忽略 E501（行宽由 line-length=120 控制），`__init__.py` 豁免 F401。
- **测试路径**：`pytest` 仅扫描 `tests/` 目录，`pythonpath` 包含仓库根以便从 tests 中直接 import `src`。
- **CI/发布**：仓库中未发现任何 CI 配置文件（如 `.github/workflows`、`.gitlab-ci.yml`、`Dockerfile`、`Makefile`），因此不存在自动化的构建、测试、打包或发布流水线。
- **Tauri 安全策略**：CSP 被显式关闭（`csp: null`），HTTP 白名单仅允许 `http://127.0.0.1:8765/*`。