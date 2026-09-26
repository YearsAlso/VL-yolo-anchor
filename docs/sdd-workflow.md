# SDD 工作流（Spec-Driven Development）

本项目遵循 **Spec-First → Design → Implement → Verify → Archive**：

1. **Propose**（`/opsx:propose`）：在 `changes/<id>/proposal.md` 说明 why / what / impact，与用户确认。
2. **Spec & Design**（`/opsx:ff`）：产出 `design.md`、`specs/*.spec.md`（delta，五要素格式）、`tasks.md`。
3. **Apply**（`/opsx:apply`）：严格按 spec 实现，不得在 spec 外定义接口签名或数据结构。
4. **Verify**（`/opsx:verify`）：对照 spec 验收标准检查代码与测试，不通过则回修。
5. **Archive**（`/opsx:archive`）：将 delta spec 合并入 `specs/`（唯一可信源），清理 `changes/<id>/`。

Bug / 优化类任务先 `/opsx:explore` 输出问题分析 + 修复 spec，再走 Apply → Verify → Archive。

硬约束：无 spec 不编码；所有变更必须有 `changes/` 目录与 delta spec。
