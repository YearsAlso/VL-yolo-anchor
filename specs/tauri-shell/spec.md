# Spec: tauri-shell

> **状态**：已实施（as-built，从 `specs/tauri-shell.spec.md` 迁移并重组为六要素）
> **设计**：[`design.md`](design.md)
> **实施栈**：前端
> **最后核对**：2026-09-27

## 一、业务背景

`gui/src-tauri/` 提供 Tauri v1 桌面壳，使 `npm run tauri dev` 可用：以原生窗口加载 Vite 开发服务器，交付一个可分发的桌面应用。

## 二、功能范围

**包含**

- Tauri v1 配置文件与最小 Rust 壳（`src/main.rs` 仅 8 行）
- 窗口、打包、图标、CSP、http allowlist 声明
- 开发/构建前置命令（`beforeDevCommand` / `beforeBuildCommand`）

**不包含**

- 任何业务逻辑（全部留在 React SPA 与 Python 后端）
- Rust 侧的自定义 command / IPC 插件 —— 壳只负责开窗与加载
- 自动更新（**技术栈锁定 Tauri v1，禁止擅自升级到 v2**）

## 三、接口契约

| 项 | 契约 |
|----|------|
| CLI | `@tauri-apps/cli ^1.6`，与 `gui/package.json` 版本匹配 |
| 配置 schema | `tauri.conf.json` 的 `$schema` 指向 `https://schema.tauri.app/config/1`，字段用 v1 语义（`build.devPath` / `build.distDir`） |
| 窗口 | 标题 `VL-YOLO-Anchor`，1280×800，`resizable: true` |
| 应用标识 | `bundle.identifier = com.vlyoloanchor.app`，`package.version = 0.1.0` |
| 后端地址 | `http://127.0.0.1:8765/*`（与 `services/api.ts` 的生产 base 一致） |

## 四、数据模型（文件与配置 schema）

| 文件 | 内容 |
|------|------|
| `gui/src-tauri/tauri.conf.json` | 44 行：`package` / `build` / `tauri.allowlist` / `tauri.windows` / `tauri.bundle` / `tauri.security` |
| `gui/src-tauri/Cargo.toml` | 17 行，依赖 `tauri = "1"` |
| `gui/src-tauri/build.rs` | 3 行，`tauri-build` |
| `gui/src-tauri/src/main.rs` | 8 行 |
| `gui/src-tauri/icons/` | `32x32.png`、`128x128.png`、`128x128@2x.png`、`icon.ico`、`icon.png`，并在 `bundle.icon` 声明 |
| `gui/src-tauri/Cargo.lock` | Rust 依赖锁定（三栈锁文件之一） |
| `gui/src-tauri/.gitignore` | 忽略 `target/` |

**allowlist 声明**：`http.all = false`、`http.request = true`、`http.scope = ["http://127.0.0.1:8765/*"]`。
**CSP**：`default-src 'self'`；`connect-src`/`img-src` 放行 `http://127.0.0.1:8765`（`img-src` 另放行 `blob:` 与 `data:`，供画布与 base64 图像）；`style-src` 允许 `'unsafe-inline'`（Ant Design 需要）；**不得为 `null`**。

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 5 职责边界（GUI 栈锁定） | **只能 Tauri + React + TS + AntD**；禁止改投 Electron / Vue / PyQt；禁止擅自升级 Tauri 大版本（v1 → v2 是破坏性变更，须走 SDD） |
| 5 职责边界（Python 承担 CV/推理/IO） | 壳与前端**不做**文件读写与图像解码；图像经 `imageUrl()` 由后端 FileResponse 提供 |
| 7 全相对路径 | `devPath` 用 `http://localhost:5173`、`distDir` 用 `../dist`，**禁止绝对路径** |

## 六、边界条件

- 需要 Rust toolchain 且能访问 crates.io 拉取 tauri 1.x 依赖；**离线或网络受限环境下 `cargo check` / `tauri dev` 会失败，属环境问题**，不阻塞本 spec 的文件与 schema 验收
- **Windows 上 `tauri-build` 依赖 `icons/icon.ico` 生成资源文件，缺失即编译失败**
- Vite 未启动时窗口加载失败属**预期行为**（`devPath` 指向 5173）
- `allowlist.http` 若缺 `request: true`，则 `scope` **不启用任何 HTTP 能力**（配置看似正确实则无效）

## 七、验收标准

1. 五个文件齐备，`tauri.conf.json` 为合法 JSON 且符合 v1 schema（`devPath` / `distDir`）
2. `Cargo.toml` 依赖 `tauri = "1"`
3. 无硬编码绝对路径
4. `security.csp` 非 `null` 且只放行必要来源
5. （依赖可下载时）`cd gui/src-tauri && cargo check` 通过
