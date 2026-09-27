---
kind: frontend_style
name: 基于 Ant Design + Vite 的 Tauri 桌面 GUI 样式体系
category: frontend_style
scope:
    - '**'
source_files:
    - gui/package.json
    - gui/vite.config.ts
    - gui/src/main.tsx
    - gui/src/App.tsx
    - gui/src/pages/TaskPlanPage.tsx
    - gui/src/pages/AnnotationReviewPage.tsx
    - gui/src/pages/InspectionReportPage.tsx
    - gui/src/pages/TrainingExportPage.tsx
    - gui/src/components/OBBCanvas.tsx
---

## 1. 采用的系统与工具

- **框架与构建**：React 18 + TypeScript，通过 Vite 5 进行开发与构建（`vite.config.ts`），开发服务器端口 5173，并通过 `proxy` 将 `/api` 请求转发到本地 FastAPI 后端（`http://127.0.0.1:8765`）。
- **UI 组件库**：Ant Design 5（`antd@^5.20.0`）作为唯一 UI 组件来源，配套使用 `@ant-design/icons` 图标库与 `@ant-design/charts` 图表库。
- **打包与桌面集成**：前端资源由 Vite 构建后，由 Tauri v1（`@tauri-apps/api ^1.6.0`、`@tauri-apps/cli ^1.6.0`）嵌入为桌面应用；入口位于 `gui/src/main.tsx`，根组件 `App.tsx` 以 `<Tabs>` 组织四个功能页签。
- **样式方案**：**无独立 CSS/SCSS/Tailwind 文件**。所有视觉样式通过以下三种方式实现：
  - Ant Design 组件内置主题（如 `Card`、`Space`、`Descriptions`、`Upload.Dragger`、`Alert`、`Button`、`Select`、`Spin`、`Statistic`、`Table`、`Tag`、`Progress`、`Tabs`、`Typography` 等）。
  - React inline `style={{ ... }}` 对象（例如 `padding: 24, maxWidth: 1280, margin: "0 auto"`、`minWidth: 220`、`width: 260, maxHeight: 480, overflow: "auto"`、`cursor: "pointer", background: "#e6f4ff"` 等）。
  - Canvas 2D API 直接绘制 OBB 标注框（颜色来自硬编码数组 `CLASS_COLORS = ["#f5222d","#fa8c16","#52c41a","#1890ff","#722ed1","#eb2f96"]`）。

## 2. 关键文件

- `gui/package.json`：声明 antd、@ant-design/*、react、axios、@tauri-apps/* 等依赖及 `dev/build/preview/tauri` 脚本。
- `gui/vite.config.ts`：Vite 配置，含 React 插件、开发代理、`@` → `/src` 路径别名。
- `gui/src/main.tsx`：React 根渲染入口，包裹 `<React.StrictMode>` 挂载 `App`。
- `gui/src/App.tsx`：顶层布局，使用 Ant Design `<Tabs>` 定义“任务与计划 / 标注审核 / 质检报告 / 训练导出”四个标签页。
- `gui/src/pages/*.tsx`：四个页面组件，全部以 Ant Design 组件组合而成，内联 style 控制布局。
- `gui/src/components/OBBCanvas.tsx`：自定义 Canvas 组件，用原生 2D 上下文绘制图像与 OBB 多边形覆盖层。
- `gui/src/types/index.ts`：TS 类型定义（被 pages 与 components 引用）。
- `gui/src/services/api.ts`：封装对 FastAPI 后端的 axios 调用。

## 3. 架构与约定

- **页面级组织**：每个业务域一个 page 组件（`TaskPlanPage`、`AnnotationReviewPage`、`InspectionReportPage`、`TrainingExportPage`），通过 `App.tsx` 的 Tabs 路由切换，无 react-router。
- **布局模式**：统一使用 `<Space direction="vertical" size="large">` 作为页面容器，内部嵌套 `<Card>` 分组区块，表单控件使用 `<Input>`、`<Select>`、`<Upload.Dragger>` 等，结果展示使用 `<Descriptions>`、`<Table>`、`<Statistic>`、`<Alert>`、`<Spin>` 等。
- **交互反馈**：所有异步操作通过 `message.success/warning/error` 提示用户，按钮使用 `loading={busy}` 状态控制。
- **Canvas 可视化**：OBB 标注框的颜色从固定调色板按 `box.cls % CLASS_COLORS.length` 取色，边框宽度随缩放比例调整（`2 / scale`），背景色使用半透明十六进制（`#color22`）。
- **开发代理**：Vite 将 `/api` 前缀的请求代理至 `http://127.0.0.1:8765`，使前端在 dev 模式下可直接调用后端 API。

## 4. 约定与约束

- **不使用 CSS Modules / SCSS / Tailwind**：仓库中不存在任何 `.css`、`.scss`、`.less`、`tailwind.config.*` 或设计 token 文件；样式完全依赖 Ant Design 默认主题与 inline style。
- **组件样式来源单一**：所有可复用 UI 元素优先选用 Ant Design 组件而非自绘 DOM；仅 Canvas 绘图部分绕过组件库，直接使用 Canvas API。
- **响应式策略**：未引入媒体查询或响应式框架；布局通过 `maxWidth: 1280` 与 `margin: "0 auto"` 居中，配合 Ant Design 栅格（`Row`/`Col`）与 `Space` 自适应。
- **主题定制**：未发现 `ConfigProvider` 主题覆盖或 antd theme 定制代码，界面遵循 Ant Design 5 默认外观。
- **Tauri 集成约束**：前端资源需经 `tsc && vite build` 编译后由 Tauri 打包，因此样式必须兼容生产构建产物（无开发时仅用的样式源）。

综上，该前端采用“Ant Design 组件 + 内联样式 + Canvas 自定义绘制”的轻量风格体系，没有独立的样式工程或设计系统，适合当前 Tauri 桌面端标注平台的快速迭代需求。