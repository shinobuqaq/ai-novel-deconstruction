# Prompt Registry

M0 不放真实拆书 Prompt。后续每个 Prompt 至少包含：

- `prompt_id`
- `semver`
- 输入 JSON Schema
- 输出 JSON Schema
- 允许的 Candidate ID 列表
- 变更记录
- Golden fixtures

Prompt 只能生成 Candidate，不能直接写数据库权威对象。
