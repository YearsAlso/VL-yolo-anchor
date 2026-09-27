# Design: tauri-shell

> **状态**：已实施（as-built）
> **关联 spec**：[`spec.md`](spec.md)
> **实施栈**：前端
> **契约冻结点**：`tauri.conf.json` 的 v1 schema 与后端地址 `127.0.0.1:8765`（改端口须同步 `services/api.ts:13-14` 与 allowlist/CSP 两处）
> **最后核对**：2026-09-27

## 一、执行摘要

选 Tauri v1 而非 Electron：本项目只需要「原生窗口 + 加载本地 SPA」，Tauri 用系统 webview，产物体积与内存占用远小于捆绑 Chromium。代价是 Rust toolchain 与 crates.io 可达性成为构建前置，且 v1 的 allowlist/CSP 配置有「写了但无效」的坑（缺 `request: true` 时 scope 不生效）。

## 二、关键设计决策

| 决策 | 选项 | 选择 | 理由 | 被否选项的代价 |
|------|------|------|------|---------------|
| 桌面壳技术 | Electron / Tauri v1 / PyQt | **Tauri v1** | 复用已有 React SPA，不额外捆绑浏览器内核；硬约束 5 已锁定该栈 | Electron ⇒ 体积翻倍且违反栈锁定；PyQt ⇒ 等于重写前端 |
| 壳的业务厚度 | 厚壳（Rust 做 IO/图像） / 薄壳 | **薄壳**（`main.rs` 8 行） | 硬约束：全部 CV/推理/文件 IO 留在 Python | 厚壳 ⇒ 几何与解码逻辑在两处实现，标注结果与后端不一致 |
| HTTP 能力放行 | `all: true` / `request: true` + `scope` | **`request: true` + `scope` 仅放行 `127.0.0.1:8765/*`** | 最小权限；Tauri v1 中 `scope` 必须配合 `request: true` 才生效 | `all: true` ⇒ 桌面端可任意出网；只写 `scope` 不写 `request` ⇒ 静默无效，前端调不通还以为后端挂了 |
| CSP | `null`（不设） / 最小 CSP | **最小 CSP** | `null` 等于关闭防护；Ant Design 只需 `style-src 'unsafe-inline'`，画布只需 `img-src blob: data:` | 设 `null` ⇒ 注入面扩大；CSP 过严 ⇒ AntD 样式或图像静默失效 |
| dev 加载方式 | 加载打包产物 / 加载 Vite dev server | **`devPath = http://localhost:5173`** | 开发期保留 HMR 与 sourcemap | 加载 `distDir` ⇒ 每次改动都要重新构建 |
| 图标集 | 只给 `icon.ico` / 五份齐给 | **五份齐给并在 `bundle.icon` 声明** | Windows 上 `tauri-build` 依赖 `icons/icon.ico` 生成资源，**缺失即编译失败**；其他平台各取所需 | 缺 ico ⇒ Windows 构建直接失败，且报错信息与"图标"无直观关联，排查成本高 |
| Tauri 版本 | 追新（v2）/ 锁 v1 | **锁 v1**（`@tauri-apps/cli ^1.6`、`Cargo.toml` `tauri = "1"`、schema `config/1`） | v1→v2 是 allowlist/插件体系的破坏性变更 | 擅自升级 ⇒ `allowlist` 配置整体失效，需按 v2 权限模型重写 |

## 三、配置一致性三处联动

后端地址变更时必须同批改三处，否则桌面端静默连不通：

1. `gui/src-tauri/tauri.conf.json` 的 `tauri.allowlist.http.scope`
2. 同文件的 `tauri.security.csp`（`connect-src` 与 `img-src`）
3. `gui/src/services/api.ts:13-14` 的 `API_BASE` 生产分支

这三处不属 Pydantic ↔ TS 契约面，`api-contract-review` **不覆盖**，需由 `react-ts-review` 的 Tauri 章节人工核对。

## 四、边界与风险

| 风险 | 等级 | 说明 |
|------|------|------|
| 离线/网络受限环境无法 `cargo check` | 🟡 弱可控 | 属环境问题；验收标准第 5 条已标条件，不阻塞文件与 schema 验收 |
| `@tauri-apps/api` 已声明但 `gui/src/**` 零引用 | 🔵 | 账本 D1：SPA 目前不直接调 Tauri JS API。删除前须确认打包阶段无隐式依赖 |
| CSP 中 `'unsafe-inline'`（`style-src`） | 🔵 | AntD 运行期注入样式的现实需求，已知取舍；不得扩散到 `script-src` |

## 附录：关键位置速查

| 位置 | 用途 |
|------|------|
| `gui/src-tauri/tauri.conf.json:7-12` | `build`：devPath / distDir / before 命令 |
| `gui/src-tauri/tauri.conf.json:13-20` | `allowlist.http` |
| `gui/src-tauri/tauri.conf.json:21-28` | 窗口定义 |
| `gui/src-tauri/tauri.conf.json:29-39` | bundle 与图标 |
| `gui/src-tauri/tauri.conf.json:40-42` | CSP |
| `gui/src-tauri/src/main.rs` | 8 行薄壳 |
| `react-ts-review` skill 第 ⑩ 项 | Tauri v1 API 使用边界审查 |
