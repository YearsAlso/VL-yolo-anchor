# Spec: per-image-label-api

## 功能描述

后端向 GUI 提供逐图 YOLO-OBB 标签的只读 HTTP 接口：

- `GET /api/tasks/{name}/labels` — 列出有标签文件的图片名。
- `GET /api/tasks/{name}/labels/{image_name}` — 返回单张图的 OBB 框列表。

`AnnotationReviewPage` 选中图片时调用后者渲染真实框。

## 输入约束

- 标签行格式 `cls x1 y1 x2 y2 x3 y3 x4 y4`（8 个归一化浮点，范围 [0,1]）。
- 读取优先级：`candidate_labels/<stem>.txt` → `ai_labels/<stem>.txt`。
- `image_name` 必须存在于 `images/`，标签以文件 stem 匹配。

## 输出约束

- 单图响应 `{"image": str, "boxes": [{"cls": int, "points": [8 floats], "conf": float}]}`；文件中无置信度列时 `conf = 1.0`。
- 列表响应 `{"images": [str, ...]}`，按图片名排序。
- 畸形行（非数字、坐标数 ≠ 8）跳过并记 warning，不影响整体响应。

## 边界条件

- 任务不存在 → 404；图片不存在 → 404；图片存在但无标签文件 → 404（GUI 显示空画布，不弹错）。
- 空标签文件 → `boxes: []`，200。
- 端点为只读，绝不写入标签目录（硬约束：永不覆盖原始标签）。

## 验收标准

1. `uv run mypy src/` 与 `uv run ruff check src/` 零错误。
2. TestClient 测试覆盖：候选标签优先、回退 `ai_labels`、404（无任务/无图/无标签）、畸形行跳过、空文件。
3. 实测 uvicorn 下返回内容与 `ai_labels/*.txt` 一致；GUI 选中图片渲染真实框。

> 实现：`src/api_server.py`（`_parse_label_file` / `_find_label_file` 及两个端点）、
> `gui/src/services/api.ts`、`gui/src/pages/AnnotationReviewPage.tsx`。
