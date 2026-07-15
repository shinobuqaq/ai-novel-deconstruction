# GitHub Issue Backlog

本文件是工程 Backlog 与 GitHub Issue 的范围基线。M0-01—M0-08 应分别建立或修正为独立 Issue；每个 Issue 必须包含 Goal、Research basis、Adopted、Rejected、Deliverables、Acceptance criteria 和 Validation。

## Ready Queue：先做这些

### M0-01 仓库与开发脚本
- [x] Monorepo 目录
- [x] `.gitignore` / `.env.example`
- [x] `setup.ps1` / `dev.ps1` / `test.ps1`
- [x] 在用户 Windows 机器上完成一次冷启动验证（见 `docs/M0_COLD_START_VALIDATION.md`）

**Done:** 新机器按 README 可在 15 分钟内启动三进程。

### M0-02 配置与 Workspace
- [x] Pydantic Settings
- [x] Workspace 与 Artifact 目录
- [ ] Secret file 约定
- [ ] 配置错误的可诊断提示

### M0-03 SQLite 与 Migration
- [x] SQLite WAL / foreign keys
- [x] Alembic 初始 migration
- [x] Migration smoke test（清理导入前已在隔离环境验证）
- [ ] 数据库备份/恢复脚本

### M0-04 Task Lease
- [x] PENDING / RUNNING / SUCCEEDED / FAILED
- [x] lease owner / expiry / attempts
- [ ] 进程崩溃后的过期租约恢复测试
- [ ] CANCEL command

### M0-05 Artifact Registry
- [x] 内容哈希
- [x] 临时文件 + 原子替换
- [x] 内容去重
- [ ] Artifact lineage / dependency edge
- [ ] DIRTY propagation baseline

### M0-06 Fake Provider
- [x] 确定性响应
- [x] Token 用量占位记录
- [ ] Provider contract test
- [ ] invalid JSON / timeout / rate-limit fake scenarios

### M0-07 API 与最小 Run Center
- [x] Project / Task / Artifact API
- [x] React 最小页面
- [ ] SSE 或轮询进度规范
- [ ] 错误码目录接入 UI

### M0-08 M0 恢复与测试 Gate
- [x] API health test
- [x] Project → Task → Artifact integration test
- [ ] Worker 在 Provider 后、Artifact commit 前崩溃的恢复测试
- [x] Windows 冷启动测试记录（见 `docs/M0_COLD_START_VALIDATION.md`）

---

## Vertical Slice 01

### VS01-01 导入 2—3 章
- TXT/Markdown parser
- SourceDocument / SourceVersion / SourceUnit
- 章节顺序、空章、重复章 Issue

### VS01-02 EvidenceSpan
- `start_char/end_char/text_snapshot/context_hash`
- 精确回贴测试
- 多匹配 Locator 返回 UNCERTAIN

### VS01-03 单一路实体候选
- 先实现 LLM 或规则中的一条
- Candidate 只绑定 Evidence，不直接 Canonical Write

### VS01-04 单一路事件候选
- Structured Output Schema
- Trigger phrase + source text
- Character Locator

### VS01-05 Source Alignment Gate
- 0 匹配：REJECTED
- 1 匹配：VALID candidate
- 多匹配：UNCERTAIN Issue

### VS01-06 Candidate / Issue Queue
- Candidate 状态机
- Issue 严重度与对象引用
- 用户接受/拒绝基础命令

### VS01-07 Simple Claim
- Claim text / type / Evidence IDs
- Fake adjudicator
- VERIFIED / UNCERTAIN / REJECTED

### VS01-08 Evidence Inspector
- 原文高亮
- Candidate / Claim 跳转
- 展示坐标、来源、状态和 Issue

---

## M1 Source / Evidence
- M1-01 Source schema and deterministic IDs
- M1-02 TXT/Markdown importer
- M1-03 DOCX/EPUB adapter（可延后）
- M1-04 chapter parser and manual correction
- M1-05 character authority and token projection
- M1-06 chunk overlap and dedup
- M1-07 FTS5 index
- M1-08 Source Inspector
- M1-09 source version diff and dirty roots
- M1-10 source gold fixtures

## M2 Entity Identity
- M2-01 EntityMention schema
- M2-02 alias/name route
- M2-03 embedding candidate route
- M2-04 candidate pair feature vector
- M2-05 hard negative guard
- M2-06 pair adjudication
- M2-07 guarded clustering
- M2-08 merge/split/redirect lineage
- M2-09 Entity Explorer
- M2-10 entity benchmark

## M3 Event
- M3-01 EventCandidate adapter
- M3-02 LLM phrase route
- M3-03 rule/state-change route
- M3-04 exact character locator
- M3-05 candidate union
- M3-06 boundary resolver
- M3-07 event type conflict handling
- M3-08 EventMention ID and status
- M3-09 event coreference and CanonicalEvent
- M3-10 Event Timeline and benchmark

## M4 Fact / State / Epistemic
- M4-01 TemporalFact and FactVersion
- M4-02 valid interval and recurrence
- M4-03 contradiction / coexisting / disputed
- M4-04 Fact Writer guard
- M4-05 StatePatch and deterministic reduce
- M4-06 State-at-T query
- M4-07 ActorKnowledge baseline
- M4-08 Fact/State Inspector
- M4-09 temporal benchmark

## M5 Retrieval
- M5-01 TaskProfile
- M5-02 lexical route
- M5-03 vector route
- M5-04 temporal/entity route
- M5-05 graph diffusion route
- M5-06 candidate normalization
- M5-07 RRF and rerank
- M5-08 route quota and diversity
- M5-09 source backfill and landing audit
- M5-10 retrieval ablation

## M6 Claim / Specialist
- M6-01 AnalysisClaim schema
- M6-02 support/counter retrieval
- M6-03 evidence adjudication
- M6-04 conflict-aware aggregation
- M6-05 split/narrow/repair/drop
- M6-06 six specialist contracts
- M6-07 specialist batch execution
- M6-08 Claim Inspector
- M6-09 claim benchmark

## M7 Report / UI
- M7-01 Artifact Compiler
- M7-02 report templates
- M7-03 Project dashboard
- M7-04 Run Center DAG
- M7-05 Issue Queue
- M7-06 Entity/Event/Fact/Claim navigation
- M7-07 diff view
- M7-08 DOCX exporter
- M7-09 publish gate

## M8 Benchmark
- M8-01 30—50 chapter gold corpus
- M8-02 metric harness
- M8-03 ablation runner
- M8-04 cost and latency dashboard
- M8-05 crash recovery suite
- M8-06 dirty recompute measurement
- M8-07 performance profiling
- M8-08 Quality Mode decision report
- M8-09 Balanced/Economy proposal
