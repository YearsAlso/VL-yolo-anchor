---
kind: dependency_management
name: 多语言依赖管理：uv + pyproject.toml、npm + package.json、Cargo + Cargo.toml 三栈锁定
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
    - uv.lock
    - gui/package.json
    - gui/package-lock.json
    - gui/src-tauri/Cargo.toml
    - gui/src-tauri/Cargo.lock
---

## 1. 使用的系统与工具

本项目是一个多语言仓库，分别使用三种独立的依赖管理系统：
- **Python**：使用 `pyproject.toml`（PEP 621）声明项目元数据与依赖，使用 `uv`（现代 Python 包管理器/解析器）生成并维护 `uv.lock` 锁定文件。构建后端为 `setuptools.build_meta`。
- **前端 GUI（React + Vite + Tauri）**：使用 `gui/package.json` 声明 npm 依赖，配合 `gui/package-lock.json` 锁定版本；通过 `vite` 构建，`@tauri-apps/cli` 作为开发依赖。
- **Tauri Rust 壳层**：使用 `gui/src-tauri/Cargo.toml` 声明 Rust crate 依赖，配合 `gui/src-tauri/Cargo.lock` 锁定版本。

三个子系统各自独立，不存在跨语言的统一依赖入口。

## 2. 关键文件

- `pyproject.toml`：Python 项目的唯一依赖声明源，包含 `dependencies`、`optional-dependencies.gpu`、`optional-dependencies.dev`、`build-system`、以及 ruff/mypy/pytest 工具配置。
- `uv.lock`：由 `uv` 生成的完整可复现锁定文件，记录每个包的精确版本、来源（`https://pypi.org/simple`）、sha256 哈希、wheel/sdist URL 及基于 Python 版本的 resolution markers。
- `gui/package.json`：前端依赖声明，区分 `dependencies` 与 `devDependencies`。
- `gui/package-lock.json`：npm 锁定文件。
- `gui/src-tauri/Cargo.toml`：Rust 依赖声明，仅引入 `tauri`、`serde`、`serde_json`、`tauri-build`。
- `gui/src-tauri/Cargo.lock`：Rust 锁定文件。
- `.venv/`：本地虚拟环境目录（未纳入版本控制）。

## 3. 架构与约定

### Python 依赖分层
- 核心运行依赖集中在 `pyproject.toml` 的 `dependencies` 中（opencv-python、pyyaml、numpy、pillow、matplotlib、fastapi、uvicorn、pydantic、jinja2），要求 `requires-python >= 3.11`。
- GPU 相关依赖（torch、torchvision、transformers、accelerate、bitsandbytes）被拆到 `optional-dependencies.gpu`，仅在显式安装时拉取，避免默认安装重型 ML 包。
- 开发依赖（ruff、mypy、pytest、httpx）放在 `optional-dependencies.dev`，通过 `uv pip install -e .[dev]` 或等价方式安装。
- 构建系统固定为 `setuptools>=70.0`，但实际依赖解析与锁定由 `uv` 完成，体现“声明在 pyproject，锁定在 uv.lock”的分工。

### 锁定策略
- Python 侧使用 `uv.lock` 而非 `requirements.txt` 或 `pip-tools` 的 `requirements.txt` 锁定，所有第三方包均带有 sha256 校验和，确保跨平台可复现安装。
- `uv.lock` 顶部声明 `requires-python = ">=3.11"` 与 `resolution-markers`，按 Python 版本分支选择不同依赖（如 numpy 在 <3.12 与 ≥3.12 下选择不同版本）。
- 前端使用 npm 标准 `package-lock.json`，Rust 使用 `Cargo.lock`，三者均为各自生态的标准锁定方案。

### 私有仓库与镜像
- 当前仓库未发现任何自定义 registry、`uv` 的 `--index-url`、`.netrc`、`PIP_INDEX_URL`、`cargo config` 或 npm `registry` 配置，所有包均来自公开 PyPI (`https://pypi.org/simple`)、npm registry 与 crates.io。

## 4. 约定与约束

- **Python 最低版本约束**：`pyproject.toml` 中 `requires-python = ">=3.11"`，mypy 配置 `python_version = "3.12"`，二者存在轻微不一致（mypy 指向更高版本），但运行时要求以 `requires-python` 为准。
- **可选依赖隔离**：GPU 依赖必须通过 `uv pip install -e .[gpu]` 显式启用，避免在无 GPU 环境中安装 torch/bitsandbytes 等重型包。
- **工具链配置内聚于 pyproject**：ruff（行宽 120、目标 py311、排除 gui/tasks/prompts）、mypy（strict=true、disallow_untyped_defs=true、忽略缺失导入）、pytest（testpaths=tests）全部集中声明在 `pyproject.toml` 的 `[tool.*]` 段，不额外维护 `.ruff.toml`、`mypy.ini`、`pytest.ini`。
- **GUI 与 Rust 子工程独立**：`gui/` 是独立 npm 工程，`gui/src-tauri/` 是独立 Cargo 工程，两者通过 Tauri 机制组合，但不共享依赖图。
- **无 vendoring**：未发现 `vendor/`、`third_party/` 或源码级 vendoring；所有第三方代码通过包管理器从远程 registry 拉取。
- **虚拟环境隔离**：根目录存在 `.venv/` 与 `.mypy_cache/`、`.pytest_cache/`、`.ruff_cache/` 等缓存目录，这些通常应被 gitignore（仓库根 `.gitignore` 未展示，但从缓存目录存在推断已被忽略）。