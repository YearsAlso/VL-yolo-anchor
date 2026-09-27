# Design: per-image-label-api

> **状态**：已实施（as-built）
> **关联 spec**：[`spec.md`](spec.md)
> **实施栈**：跨栈
> **契约冻结点**：`LabelBoxesResponse` / `LabeledImagesResponse` 已冻结并被 `AnnotationReviewPage` + `OBBCanvas` 消费
> **最后核对**：2026-09-27

## 一、执行摘要

设计核心是**「单行坏数据不该毁掉整张图的审核工作」**：逐行跳过 + warning，而非抛异常；同时把「文件整体读不出来」与「服务器坏了」区分开（422 vs 500）。代价是解析器对调用方的错误契约敏感 —— 只有正确分类 `OSError` 的调用方才享受 422 语义，这条契约已写进函数 docstring 并由回归测试锚定。

## 二、关键设计决策

| 决策 | 选项 | 选择 | 理由 | 被否选项的代价 |
|------|------|------|------|---------------|
| 畸形行处理 | 抛异常中断 / 夹紧到 `[0,1]` / 跳过 + warning | **跳过 + warning** | 审核场景下用户要看的是**其余正确的框**；夹紧会伪造出并不存在的几何 | 抛异常 ⇒ 一行坏数据毁掉整张图；夹紧 ⇒ 静默引入错误标注，比丢框更危险 |
| 坐标越界 | 夹紧 / 丢弃 | **丢弃该行** | `[0,1]` 是硬约束 1 的值域契约，越界说明来源不可信 | 夹紧 ⇒ 掩盖上游归一化 bug |
| 类别不在 plan 内 | 报错 / 返回但标记 / 过滤掉 | **过滤掉** | 计划的类别集合就是本任务的合法标签空间；混入的类别会污染训练集 | 返回 ⇒ 导出后训练报类别不匹配，错误现场离成因更远 |
| 编码策略 | 严格 `utf-8` / `utf-8-sig` + `errors="replace"` | **后者** | 标签文件是**外部来源**（别的工具产出、编辑器保存），BOM 会静默吞掉第一行，非法字节不该让整请求崩 | 严格 utf-8 ⇒ 非 UTF-8 字节抛 `UnicodeDecodeError`（属 `ValueError` 家族，`except OSError` **拦不住**） |
| 内部自产文件 | 同上宽松 | **保持严格** | 内部文件编码出错应尽早暴露，不宜用 `replace` 掩盖 | 全局宽松 ⇒ 写入侧的编码 bug 永远看不见 |
| IO 失败分类 | 冒泡成 500 / 归为 4xx | **422 + `detail` 带文件名** | 文件被其他进程独占是**用户可恢复**的数据问题，500 会让用户以为服务坏了 | 500 ⇒ 用户不知道是哪一个标签文件读不了、该去关谁 |
| 类别集合来源 | 全局配置 / 当前任务 `plan.yaml` | **当前任务 `plan.yaml`** | 硬约束 3 任务规则隔离 | 全局 ⇒ 跨任务混用类别 ID |
| `plan.yaml` 缺失或损坏 | 拒绝请求 / 跳过类别过滤 | **跳过过滤 + warning**（`_allowed_classes` 的 `except Exception` 兜底） | 「计划还没生成但标签已存在」是正常中间态，不应阻断查看 | 拒绝 ⇒ 用户看不到标签，必须先跑 plan 才能审核 |
| 列表端点是否解析内容 | 解析后返回 / 只做存在性检查 | **只 `_find_label_file` 存在性检查** | 列图不需要全量解析，避免 N 次文件读 | 全量解析 ⇒ 大图目录下列表接口变成 O(N) IO，且一个坏文件毁掉整个列表 |

## 三、流程与异常边界

```
GET /api/tasks/{name}/labels/{image_name}
  → _require_task(name)                       api_server.py:148   不存在 → 404
  → _safe_child(task_dir / "images", image_name)  :170            路径逃逸 → 400
  → image_path.is_file()                                        不存在 → 404
  → _find_label_file(task_dir, image_name)     :268              无标签 → 404
       顺序 candidate_labels → ai_labels
  → _parse_label_file(label_path, _allowed_classes(task_dir))  :221
       read_text(utf-8-sig, errors="replace")   :242             OSError 逃逸
  → except OSError → HTTPException(422, "Unreadable label file: {name}")  :492-495
```

**只读保证**：整条链路无任何写操作 —— 由 `tests/test_api_server.py:258`（读端点不得修改标签文件）与 `:270`（只报告真有标签的图）锚定。

## 四、前后端契约

| 面 | 内容 |
|----|------|
| 面 A | 两个路径均已在 `api.ts` 有封装；`image_name` 需 `encodeURIComponent`（测试覆盖 `%5c` 编码变体） |
| 面 B | `LabelBoxesResponse` ↔ `LabelResult`（`gui/src/types/index.ts`）；`boxes[].points` 为 8 元数组，形状必须一致 |
| 面 C | 无请求体 |
| 面 D | 不涉及 |

## 五、测试设计

| 用例 | 锚定 |
|------|------|
| BOM 不丢首行 | `tests/test_api_server.py:212` |
| 非 UTF-8 不 500 | `:223` |
| 坐标越界丢弃（硬约束 1） | `:235` |
| 类别不在 plan 内过滤（硬约束 3） | `:246` |
| 读端点不修改标签文件 | `:258` |
| 只报告有标签的图 | `:270` |
| `%5c` 路径逃逸 400 | `:411` |
| IO 失败 → 422 | `:423-433`（`monkeypatch.setattr(Path, "read_text", _boom)`） |

**跨平台教训**：IO 失败**禁止**用真实文件锁复现（Linux/macOS 无强制锁，CI 上永远通过），一律 monkeypatch 注入 —— 详见 `docs/memory/bug-investigation-memory.md` 排查记录 #1 与 `.claude/rules/assertion-integrity.md`。

## 六、风险与已知缺口

| 风险 | 等级 | 说明 |
|------|------|------|
| **F2**：`detail` 到不了用户 | 🟡 | `api.ts` 无拦截器，6 处 `catch` 只读 `err.message` ⇒ 本设计精心区分的 400/404/422 文案在前端塌缩成「Request failed with status code 422」。**修复归属前端**：加 response interceptor 提取 `detail` |
| 同类 IO 保护缺失 | 🟡 | `inspect_agent.py:188` 与 `model_client.py:158` 未套用本设计的异常分类（账本 IO-1/IO-2） |
| `as` 断言绕过类型 | 🔵 | 前端对无 `response_model` 的端点用 `res.data as T`（见 `api-response-contract.md` 豁免表） |

## 附录：关键位置速查

| 位置 | 用途 |
|------|------|
| `src/api_server.py:148` | `_require_task` 任务存在性（404） |
| `src/api_server.py:170` | `_safe_child` 路径穿越防护（400） |
| `src/api_server.py:221-262` | `_parse_label_file` 容错解析主循环 |
| `src/api_server.py:268` | `_find_label_file` 来源优先级 |
| `src/api_server.py:492-495` | `OSError` → 422 分类点 |
| `gui/src/pages/AnnotationReviewPage.tsx` | 消费方与标签来源展示 |
| `gui/src/components/OBBCanvas.tsx` | 归一化四点 → canvas 坐标（**只做视口变换，不做归一化计算**） |
