# specs/

正式 spec 目录 —— **唯一可信源**。

- 每个能力一个 `*.spec.md`，包含五要素：功能描述、输入约束、输出约束、边界条件、验收标准。
- 变更流程：`changes/<id>/` 中产出 proposal → design → delta spec → tasks，实现并通过验收后由 `/opsx:archive` 合并入本目录。
- 禁止在 `src/`、根目录存放正式 spec。
