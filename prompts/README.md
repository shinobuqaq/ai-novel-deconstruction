# Prompt Registry

这里保存已经进入真实分析链的任务级提示词。当前任务与提示词对应关系：

- `entities_events_v1.md`
- `hierarchical_digest_v1.md`
- `narrative_component_v1.md`
- `narrative_synthesis_v1.md`（只为兼容已经创建的旧任务，新任务按四个独立组件执行）
- `deep_insights_v1.md`
- `character_design_evidence_v1.md`
- `chapter_end_hooks_v1.md`
- `learning_report_v1.md`

每个正式提示词至少包含：

- `prompt_id`
- `semver`
- 输入 JSON Schema
- 输出 JSON Schema
- 允许的 Candidate ID 列表
- 变更记录
- Golden fixtures

Prompt 只能生成 Candidate，不能直接写数据库权威对象。

当前约定：

- 文件名中的版本随行为变化递增，不静默覆盖旧语义。
- 任务制品记录提示词编号、版本、来源范围、输入哈希和输出结构。
- 原文仍以固定来源版本为权威；提示词和模型输出不能成为事实来源。
