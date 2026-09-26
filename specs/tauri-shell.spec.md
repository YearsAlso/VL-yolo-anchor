# Spec: tauri-shell

## 功能描述

`gui/src-tauri/` 提供 Tauri v1 桌面壳，使 `npm run tauri dev` 可用：以原生窗口加载 Vite 开发服务器。

## 输入约束

- Tauri CLI v1.x（与 `gui/package.json` 的 `@tauri-apps/cli ^1.6` 匹配），`tauri.conf.json` 使用 v1 schema。
- 前端 dev 地址 `http://localhost:5173`，产物目录 `../dist`。

## 输出约束

- 文件：`tauri.conf.json`、`Cargo.toml`、`build.rs`、`src/main.rs`、`.gitignore`。
- 图标：`icons/{32x32.png,128x128.png,128x128@2x.png,icon.ico,icon.png}`，并在 `tauri.bundle.icon` 中声明（Windows 上 `tauri-build` 依赖 `icons/icon.ico` 生成资源文件，缺失即编译失败）。
- 窗口标题 `VL-YOLO-Anchor`，默认 1280×800。
- `allowlist.http` 仅放行 `http://127.0.0.1:8765/*`。

## 边界条件

- 需要 Rust toolchain 且能访问 crates.io 拉取 tauri 1.x 依赖；离线或网络受限环境下 `cargo check` / `tauri dev` 会失败，属环境问题，不阻塞本 spec 的文件与 schema 验收。
- Vite 未启动时窗口加载失败属预期行为。

## 验收标准

1. 五个文件齐备，`tauri.conf.json` 为合法 JSON 且字段符合 v1 schema（`devPath` / `distDir`）。
2. `Cargo.toml` 依赖 `tauri = "1"`。
3. 无硬编码绝对路径。
4. （依赖可下载时）`cd gui/src-tauri && cargo check` 通过。
