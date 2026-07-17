# M0 Reliability Test Baseline

## 1. 状态

本文件对应：

> PR B：Task Reliability Test Baseline

PR B 建立第一版可靠性测试基线。PR B.1 补充此前未落地的过期任务收敛、失败分类和取消竞争行为测试，并加入自动 gap 清单检查。

完整设计、不变量、固定命令契约与后续唯一 PR 顺序见：

- `docs/M0_RELIABILITY_DESIGN.md`

PR B 与 PR B.1 都不修改 Task、Lease、Provider、Artifact 或数据库运行语义。

当前项目状态仍定义为：

> M0 功能闭环已通过；M0 可靠性 Gate 尚未通过。

## 2. 为什么使用 strict xfail

当前实现已经确认存在可靠性缺口。若直接将目标断言作为普通测试提交，GitHub Actions 会持续失败，后续改造无法通过受保护的 `main`。

因此，PR B 将每个已确认缺口登记为：

```python
@pytest.mark.xfail(reason="M0-GAP-...", strict=True)
```

含义如下：

- 当前缺口仍存在：测试显示 `XFAIL`，CI 可以通过；
- 后续实现意外满足契约但尚未更新测试：显示 `XPASS(strict)`，CI 失败；
- 实现 PR 必须删除对应 `xfail`，让测试转为普通 `PASS`；
- 不允许通过删除断言、放宽条件或改成非严格 xfail 消除红灯。

`xfail` 在这里不是忽略测试，而是可追踪的技术债契约。

## 3. 基线清单

| 编号 | 目标契约 | 当前预期 |
|---|---|---|
| HARNESS-01 | 独立 Session 可以观察到持久化的 Claim 状态 | PASS |
| CLAIM-01 | 两个同步 Worker 对同一 Task 只能有一个成功 Claim | PASS |
| CLAIM-02 | Claim 创建 Attempt、Lease Token 和 Generation | PASS |
| LEASE-01 | 旧 Worker 在租约被重新领取后不能提交成功或失败 | PASS |
| LEASE-02 | Heartbeat 必须校验 Attempt、Token、Generation 和 Worker | PASS |
| LEASE-03 | 正确 Heartbeat 延长 Task 与 Attempt 的租约 | PASS |
| LEASE-04 | 错误 Token 或过期租约不能续租 | PASS |
| LEASE-05 | Worker 在执行期间通过独立 Session 后台续租 | PASS |
| RETRY-01 | 达到最大尝试次数后不能重新进入 PENDING | PASS |
| REAPER-01 | 过期 Attempt 有重试预算时进入 RETRY_WAIT | PASS |
| REAPER-02 | 过期 Attempt 达到上限时进入 FAILED | PASS |
| RETRY-02 | 可重试失败进入 RETRY_WAIT 并设置 next_attempt_at | PASS |
| RETRY-03 | 永久失败第一次直接进入 FAILED | PASS |
| CANCEL-01 | CANCELLED 是不可被原地重开的终态 | PASS |
| CANCEL-02 | 取消先提交时拒绝迟到成功 | PASS |
| CANCEL-03 | 成功先提交时迟到取消不得改写终态 | PASS |
| STATE-01 | 状态机包含 RETRY_WAIT 与 CANCEL_REQUESTED | PASS |
| PROVIDER-01 | Task 执行通过 Provider Registry 注入 | PASS |
| PROVIDER-02 | 超时、限流、暂时不可用和非法输出可重试 | PASS |
| PROVIDER-03 | 认证、错误请求和永久错误不可重试 | PASS |
| PROVIDER-04 | Attempt 记录 Provider、错误码和用量 | PASS |
| ARTIFACT-01 | 不同 Task 的相同内容拥有独立 Artifact 身份 | PASS |
| ARTIFACT-02 | 同一 Task 的相同结果幂等返回同一 Artifact | PASS |
| ARTIFACT-03 | 并发提交同一结果只创建一个 Artifact | PASS |
| ARTIFACT-04 | 相同内容通过 Blob 共享文件 | PASS |
| ARTIFACT-RECOVERY-01 | 崩溃遗留临时文件可清理 | PASS |
| ARTIFACT-RECOVERY-02 | 原子替换后的孤儿 Blob 可收养 | PASS |
| ARTIFACT-RECOVERY-03 | Artifact 提交后的当前 Task 可恢复完成 | PASS |
| ARTIFACT-RECOVERY-04 | 旧 Attempt 的 Artifact 不得恢复覆盖新 Worker | PASS |
| ARTIFACT-RECOVERY-05 | 缺失文件标记 DIRTY 且重复恢复幂等 | PASS |

当前实现计数（PR I）：

```text
49 tests
49 passed
0 xfailed
0 failed
0 errors
```

本地执行包和 PR 审核必须验证该计数。计数发生变化时，需要明确修改本文件，不能静默漂移。

## 4. 测试组织

```text
backend/tests/reliability/
├── conftest.py
├── test_gap_manifest.py
├── test_task_claim_contract.py
├── test_lease_fencing_contract.py
├── test_retry_cancel_contract.py
├── test_recovery_semantics_contract.py
└── test_provider_artifact_contract.py
```

测试使用：

- 每个测试独立的临时 SQLite 数据库；
- 独立 SQLAlchemy Session；
- 真实 WAL 配置；
- 线程 Barrier 构造确定性的双 Worker Claim 交错；
- 真实 Artifact 文件写入临时 Workspace；
- 自动核对每个 strict xfail 的唯一 gap ID；
- 不调用外部模型或网络服务。

## 5. PR B 与 PR B.1 不做什么

本 PR 不会：

- 新增 `task_attempts` 表；
- 修改 Alembic；
- 修改 `claim_next_task`；
- 增加 Lease Token、Generation 或 Heartbeat；
- 实现自动重试、退避或取消；
- 注入 Provider Registry；
- 拆分 Artifact 与 Blob；
- 实现 Artifact Reconciler；
- 修改前端 Run Center。

这些实现分别进入后续 PR C—PR K。

## 6. 后续转绿顺序

冻结后的唯一顺序如下：

1. PR B.1：补全可靠性设计与高风险行为测试；
2. PR C：TaskAttempt Schema；
3. PR D：Atomic Claim；
4. PR E：Lease Fencing 与 Heartbeat；
5. PR F：Retry 与 Cancellation；
6. PR G：Provider Contract；
7. PR H：Artifact Identity / Blob；
8. PR I：Artifact Recovery；
9. PR J：Run Center Reliability UI；
10. PR K：M0 Reliability Gate。

每个实现 PR 必须：

1. 删除自己解决的 `xfail`；
2. 保留原目标断言；
3. 增加边界与回归测试；
4. 通过六项 GitHub Actions Required checks；
5. 不顺手修改无关模块。

## 7. PR B.1 完成标准

PR B.1 只有同时满足以下条件才可合并：

- 完整设计基线进入 `docs/M0_RELIABILITY_DESIGN.md`；
- 可靠性目录共收集 21 个测试；
- 结果恰好为 8 PASS、13 XFAIL；
- 没有普通 FAIL 或 ERROR；
- 完整后端测试通过；
- 六项远端 Required checks 全部通过；
- PR 文件范围只包含可靠性设计、测试基线和可靠性测试目录；
- PR 保持 Draft，等待人工审核后再合并。
