# Research Traceability

## Purpose

把 P01—P18 与 M/G 研究结论追踪到自有工程模块、M0—M8 工作项和验证方式。研究参考不等于源码依赖；本仓库当前为 clean-room 独立实现。

## Traceability matrix

| Area | Research basis | Mechanism / gap | Engineering module | Work item | Adopted | Rejected | Validation |
|---|---|---|---|---|---|---|---|
| Source / Evidence | P05, P07, P15 | M-015 | `source`, `evidence`, artifact metadata | M1 Source / Evidence | 稳定坐标、来源回填、逐层回源 | 摘要替代原文、自由文本位置 | EvidenceSpan 精确回贴与版本测试 |
| Artifact Runtime | P08, P09, P12, P17 | M-022 | Task / Artifact / Worker / recovery | M0-04, M0-05, M0-08 | 不可变写入、租约、失败续跑、局部重算入口 | 把临时 Tree/Blob 当完成进度 | 崩溃恢复、哈希去重、原子写入测试 |
| Model Settings / Provider Gateway | P01, P02, P08, P09 | 模型配置、任务参数、失败诊断和隐私 | `provider_config`, provider adapters, `/settings` | P2 及后续所有模型任务 | 独立设置中心、多服务、按任务选择模型、参数记录、脱敏诊断 | 把 API Key 表单塞进拆书步骤、全任务共用写死模型 | 旧配置迁移、密钥不回传、模型发现、参数透传和连接错误测试 |
| Entity Resolution | P06, P13 | M-023, G-01 | Candidate pairs / resolver / merge lineage | M2 Entity Identity | Candidate-first、程序校验、可逆合并 | 无保护 Union-Find、名称直接作稳定 ID | hard-negative、merge/split 回放测试 |
| Event Discovery | P14, P18 | M-021, G-04 | route adapter / union / boundary resolver | M3 Event | 多路线候选并集、边界裁决、Mention 与 Canonical Event 分层 | 最近 Token 对齐、强制单 Token、候选直写正式事件 | span gold set、UNCERTAIN 队列、coreference 测试 |
| Temporal Facts | P13 | G-02, G-08 | FactVersion / conflict guard | M4 Fact / State | 有效区间、冲突、失效保留历史 | 当前值覆盖历史、LLM 直接写权威事实 | recurrence、conflict、as-of query 测试 |
| Actor Knowledge | P12 | M-010 | ActorKnowledge / visibility gate | M4 Fact / State | 客观事实与角色认知分离 | 用全局事实直接回答角色视角 | 防剧透与误认知可见性测试 |
| State Projection | P17 | M-013 | StatePatch / deterministic reduce | M4 Fact / State | State-at-T、Patch replay、派生视图 | 把 State 快照当不可变事实源 | 任意时点重放与追溯修改测试 |
| Retrieval | P07, P11, P12, P16 | M-018, G-03, G-05 | route planner / RRF / rerank / backfill | M5 Retrieval | 路由激活、异构查询、RRF、配额、图与全局导航 | 多项目检索链串联、Community Report 当原文证据 | route ablation、source landing、recall 测试 |
| Claim Verification | P15 | M-016, G-07 | AnalysisClaim / adjudication / repair | M6 Claim / Specialist | structured-first Claim、支持/反证、三态裁决 | Any Support Wins、先写长文再猜主张作为主链 | per-claim retrieval、counter-evidence、repair benchmark |
| Artifact Compiler | P10, P17 | M-014 | report / brief / consumer visibility | M7 Report / UI | 消费者特定视图、反馈隔离、Verified Claim 后合成 | 派生报告反哺为事实、Prompt MUST 当程序 Guard | visibility、lineage、publish gate 测试 |

## Authority order

1. 原始文本与 Evidence
2. Canonical Entity / Event 与 Temporal Facts
3. Actor Knowledge
4. State Projector 派生状态
5. Community / Navigation 报告
6. Verified Claim Ledger
7. 下游分析制品
8. Raw LLM Output 只作为候选或审计材料，不能直接成为最终权威层

## Update rule

每个正式 Issue 必须写明 Goal、Research basis、Adopted、Rejected、Deliverables、Acceptance criteria 和 Validation。引入任何外部源码前，必须同步更新 `THIRD_PARTY_CODE.md`。
