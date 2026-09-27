# Design: test-suite

> **状态**：已实施（as-built）
> **关联 spec**：[`spec.md`](spec.md)
> **实施栈**：后端
> **最后核对**：2026-09-27

## 一、执行摘要

套件的设计目标是**在 stub 模型下把全链路行为钉死**，使"换成真实模型"时能区分模型差异与代码回归。为此三条纪律最关键：文件系统一律 `tmp_path` 隔离、IO 失败一律 monkeypatch 注入（不依赖真实文件锁）、断言必须"实现改错就会变红"。第三条是本项目反复踩过的坑 —— 曾发现多例「看起来在测、实际永远通过」的断言。

## 二、关键设计决策

| 决策 | 选项 | 选择 | 理由 | 被否选项的代价 |
|------|------|------|------|---------------|
| 任务根目录 | 复用仓库 `tasks/` / `tmp_path` 隔离 | **`tmp_path`** | 测试不得污染演示产物；并行与重跑才安全 | 复用仓库目录 ⇒ 测试残留污染 `tasks/`，且 `task.yaml` 被上一例改写导致顺序依赖 |
| IO 失败复现 | 真实文件锁独占 / monkeypatch | **`monkeypatch.setattr(Path, "read_text", _boom)`** | Windows 独占能触发，**Linux/macOS 无强制锁 ⇒ CI 上永远通过**，等于没测 | 真实锁 ⇒ 假绿测试，掩盖 422/500 分类缺陷（见排查记录 #1） |
| 测试图像来源 | 仓库内图片 / numpy+cv2 现场生成 | **现场生成** | 不依赖二进制资产，`.claudeignore` 也能安心排除 `tasks/*/images/` | 依赖图片 ⇒ 克隆即缺图、且大图拖慢 checkout 与上下文 |
| HTTP 测试 | 起真实 uvicorn / `TestClient` | **`TestClient`** | 免端口占用、免进程管理，毫秒级 | 起服务 ⇒ 与 `start_gui` 抢 8765，CI 上端口冲突且慢 |
| 断言对象 | 私有函数内部状态 / 公开 API | **只依赖公开 API** | 重构内部实现不应改测试 | 断内部状态 ⇒ 机械式重构被判为回归失败，测试绑架实现 |
| 异常断言 | 只断类型 / 类型 + `match=` | **应当两者都断**（现状有 7 处只断类型，账本 T1） | 只断类型 ⇒ 异常消息改错不会变红 | 只断类型 ⇒ 面向用户的 `detail` 文案无人守护 |
| 模型侧 | mock 真实 client / 用 `provider: stub` | **用 stub 实现** | stub 是产品的一等公民（离线交付形态），测它等于同时测产品路径 | mock ⇒ 测的是"我认为 client 会怎么返回"，与真实契约脱节 |
| 配置测试 | 依赖 `get_settings()` / 直接 `load_settings()` | **绕开 `@lru_cache`**（`config.py:211`） | 缓存会让第二例读到第一例的环境 | 用缓存 ⇒ 用例间 env 串味，单跑通过全跑失败 |

## 三、断言有效性判据（本项目实测教训）

`.claude/rules/assertion-integrity.md` 的四类陷阱，本项目都真实出现过：

| 陷阱 | 表现 |
|------|------|
| 谓词断言恒真 | 对**就地改写**的对象做 `mock.assert_called_with` 谓词匹配 ⇒ 断言检查的是已被改写后的状态，永远成立 |
| 空集合恒真 | 对空 list 的 `all(...)` / `not any(...)` ⇒ 集合为空时断言自动通过 |
| 只断异常类型 | `pytest.raises(X)` 不写 `match=` ⇒ 抛对了类但消息全错也不红 |
| 空断言 | `assert x is not None` 类弱断言，几乎任何实现都能满足 |

**唯一判据**：**「若把实现改错，这条断言会变红吗？」** 答不出具体改法即断言无效。

## 四、测试侧的可测性约束

测试结构反过来约束了实现设计（`python-code-review` 维度⑨ 可测试性）：

- 依赖注入优先（`Pipeline.__init__` 接受 agents、`AnnotateAgent` 接受 `vl_client`）⇒ 可替换为 stub
- **禁止模块级副作用**（导入即建目录 / 即读配置）⇒ 否则单文件运行顺序不可控
- ⚠️ 现存张力：`api_server.py:41-46` 的模块级 `_pipeline` 单例让"每个测试要新配置"变得别扭，且是并发缺陷 A1 的现场。**测试通过并不代表并发安全**

## 五、风险与缺口

| 项 | 等级 | 说明 |
|----|------|------|
| T1：7 处 `pytest.raises` 缺 `match=` | 🟡 | 异常消息无守护 |
| A8：`tests/test_annotate_agent.py:19` 冗余赋值 | 🔵 | 无害但误导读者 |
| 前端零测试 | 🟡 | `gui/` 无测试框架，前端唯一门禁是 `tsc --noEmit` ⇒ 运行时行为（hooks 依赖、canvas 渲染）**完全无自动化覆盖** |
| 覆盖率无法复测 | 🔵 | `coverage` 不在 `[dev]` extra；需要时先加依赖 |
| 上游 DeprecationWarning ×2 | 🔵 | 来自 starlette/anyio 别名，非本项目代码，勿在门禁里掩盖 |

## 附录：关键位置速查

| 位置 | 用途 |
|------|------|
| `tests/conftest.py` | 共享 fixture 入口 |
| `tests/test_api_server.py:212/:223/:235/:246/:258/:270/:411/:423-433` | 标签容错与只读保证的锚定用例（逐条对应硬约束） |
| `tests/test_pipeline.py` | 全流程与导出产物断言 |
| `pyproject.toml` `[tool.pytest.ini_options]` | `testpaths=["tests"]`、`pythonpath=["."]` |
| `.claude/rules/assertion-integrity.md` | 断言纪律规范源 |
| `test-design` skill | 用例设计流程 |
