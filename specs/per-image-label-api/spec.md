# Spec: per-image-label-api

> **状态**：已实施（as-built，从 `specs/per-image-label-api.spec.md` 迁移并重组为六要素）
> **设计**：[`design.md`](design.md)
> **实施栈**：跨栈
> **最后核对**：2026-09-27

## 一、业务背景

标注审核页需要在用户选中某张图时渲染真实的 OBB 框，并允许人工修正。后端因此提供逐图标签的**只读** HTTP 接口，把 `tasks/<task>/` 下的标签文件解析为结构化数据交给前端画布。

## 二、功能范围

**包含**

- `GET /api/tasks/{name}/labels` —— 列出**有标签文件**的图片名
- `GET /api/tasks/{name}/labels/{image_name}` —— 返回单张图的 OBB 框列表与标签来源
- 标签文件的容错解析（BOM、非 UTF-8 字节、畸形行、坐标越界、类别不在计划内）
- 前端消费：`AnnotationReviewPage` 选中图片时调用并渲染，同时显示标签来源

**不包含**

- **任何标签写入**（写接口不属本能力；人工修正只写 `candidate_labels/`，由前端提交到别的路径）
- 图像内容的 CV 计算（硬约束：留在 Python 侧的解析已经完成，前端只画）

## 三、接口契约

| 方法 | 路径 | 响应模型 | 错误码 | 前端封装 | TS 类型 |
|------|------|---------|--------|---------|---------|
| `GET` | `/api/tasks/{name}/labels` | `LabeledImagesResponse`（`response_model` 已声明） | 404 | `listLabeledImages()` | `string[]` |
| `GET` | `/api/tasks/{name}/labels/{image_name}` | `LabelBoxesResponse` | 400 / 404 / 422 | `getLabelBoxes()` | `LabelResult` |

单图响应结构：

```json
{
  "image": "img_01.png",
  "boxes": [{ "cls": 0, "points": ["8 个归一化浮点"], "conf": 1.0 }],
  "source": "candidate_labels"
}
```

- `source` ∈ `candidate_labels` | `ai_labels`，**必须**呈现给使用者（同一张图两个来源可能不同）
- 标签文件无置信度列时 `conf` 固定为 `1.0`
- `image_name` 与 `{name}` 都必须是裸名/已注册任务名：含 `/`、`\`、`.`、`..` 或以绝对路径逃逸的一律 **400**；未知任务**只判定不建目录**

## 四、数据模型（磁盘产物）

| 输入 | 规则 |
|------|------|
| `tasks/<name>/images/<image_name>` | 必须存在，否则 404 |
| `tasks/<name>/candidate_labels/<stem>.txt` | **优先**读取 |
| `tasks/<name>/ai_labels/<stem>.txt` | 兜底读取（`_find_label_file` 顺序 `("candidate_labels", "ai_labels")`） |

**行格式** `cls x1 y1 x2 y2 x3 y3 x4 y4`；畸形行（非数字、坐标数 ≠ 8、坐标越出 `[0,1]`、`cls` 不在本任务 `plan.yaml` 声明的 classes 内）**跳过并记 warning，不影响整体响应**。编码策略：`utf-8-sig` + `errors="replace"`。

## 五、硬约束影响

| 小节 | 影响 |
|------|------|
| 1 OBB 输出格式 | `points` 必须是 8 个 `[0,1]` 归一化值；越界即丢弃该行（不夹紧、不静默保留） |
| 2 标签不可覆盖 | 两个端点**均为只读**，绝不写入任何标签目录 |
| 3 任务规则隔离 | 允许的类别集合来自**当前任务**的 `plan.yaml`；`plan.yaml` 缺失或无 `classes` 时跳过类别过滤（而非拒绝全部） |
| 5 职责边界 | 解析与容错全在 Python；前端只做 canvas 渲染 |
| 7 工程约束 | 响应必须声明 `response_model`（本能力的两个端点均已满足） |

## 六、边界条件

| 情形 | 行为 |
|------|------|
| 任务不存在 | 404 |
| 图片不存在 | 404 |
| 图片存在但无标签文件 | 404（前端显示空画布，**不弹错**） |
| 非法文件名（路径逃逸） | 400 |
| 标签文件整体不可读（被其他进程独占等） | **422** + `detail` 含文件名，**绝不 500** |
| 空标签文件 | `boxes: []`，200 |
| `plan.yaml` 损坏 | 记 warning 后跳过类别过滤，标签读取不受影响 |
| CORS / base 地址 | dev 走 Vite proxy 相对路径；Tauri 生产直连 `127.0.0.1:8765`；Docker 同源 `/api` 由 nginx 反代 |

## 七、验收标准

1. `make lint` 与 `make type` 零错误
2. `tests/test_api_server.py` 覆盖：候选标签优先、回退 `ai_labels`、`source` 字段、404（无任务/无图/无标签）、路径逃逸 400（含 `%5c` 编码变体）、畸形/越界/非法 cls 跳过、BOM 不丢首行、非 UTF-8 不 500、IO 失败 → 422、只读不写标签目录、GET 不建目录
3. 实测返回内容与 `ai_labels/*.txt` 一致；GUI 选中图片渲染真实框并显示标签来源

## 已知缺口

- **F2**：`gui/src/services/api.ts` 无响应拦截器、全文不含 `detail`，6 处 `catch` 只读 `err.message` ⇒ 本节 400/404/422 的 `detail` 文案**到不了用户**，抵消了异常分类的价值
- **A7**：`getPlan()` 封装与 `GET /api/tasks/{name}/plan` 端点均无前端消费方

## 实现位置

`src/api_server.py`（`_safe_child:170`、`_allowed_classes`、`_parse_label_file:221`、`_find_label_file:268`、`list_labeled_images:447`、`get_label_boxes:469`）、`gui/src/services/api.ts`、`gui/src/pages/AnnotationReviewPage.tsx`、`gui/src/components/OBBCanvas.tsx`
