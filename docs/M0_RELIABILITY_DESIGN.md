# M0 Reliability Design Baseline

## 1. 文档状态

本文件是 M0 可靠性改造的仓库内权威设计入口。后续实现、测试、PR 和 Issue 结论以本文件为准，不再依赖仓库外文档或聊天记录拼接背景。

| 项目 | 当前值 |
|---|---|
| 设计版本 | V1.1 |
| 更新日期 | 2026-07-16 |
| 适用基线 | `main` at `b76cd3d`，PR #15 合并后 |
| 已完成 | PR A、PR B、PR B.1 |
| 当前实施项 | PR C：TaskAttempt Schema |
| 下一实施项 | PR D：Atomic Claim |

当前判断保持为：

> M0 功能闭环已通过；M0 可靠性 Gate 尚未通过。

PR B.1 只补充设计与红灯测试，不修改 Task、Provider、Artifact、Worker 或数据库运行语义。

## 2. 边界

### 2.1 本阶段必须解决

- 同一 Task 只能有一个当前有效 Attempt。
- Claim 必须是 SQLite 原子事件。
- 旧 Worker 不能续租、提交成功、提交失败或发布 Artifact。
- 长 Provider 调用通过 Heartbeat 续租。
- 可重试错误自动进入 RETRY_WAIT；永久错误直接失败。
- 达到 `max_attempts` 或租约耗尽后必须进入终态。
- 取消与成功竞争必须产生确定、可审计的结果。
- Task、Artifact 和文件系统的崩溃窗口可扫描、可恢复、可对账。
- Provider 可注入，并输出稳定错误码与重试分类。
- 所有 PR 通过受保护 `main` 的远端 CI Gate。

### 2.2 本阶段明确不做

- 不引入 PostgreSQL、Redis、RabbitMQ 或分布式任务队列。
- 不为多用户或多机器调度提前拆微服务。
- 不实现 VS01 小说导入、EvidenceSpan 或分析能力。
- 不以性能优化替代正确性验证。
- 不在一个 PR 中同时重写 Task、Provider、Artifact 和 UI。

## 3. 已验证的当前缺口

| ID | 当前实现 | 风险 |
|---|---|---|
| R-01 | Claim 先 SELECT，再修改 ORM 并 COMMIT | 两个 Worker 可领取同一任务 |
| R-02 | 完成和失败不校验 Attempt、Token、Generation | 旧 Worker 可覆盖新 Worker |
| R-03 | 任意执行异常立即 FAILED | `max_attempts` 没有自动重试语义 |
| R-04 | 过期 RUNNING 达到上限后无人处理 | 任务可永久卡住 |
| R-05 | Artifact READY 与 Task SUCCEEDED 分两次提交 | 崩溃后状态可能分裂 |
| R-06 | Task Service 直接创建 FakeProvider | Provider Protocol 没有运行时解耦 |
| R-07 | Artifact 身份与内容哈希绑定 | 不同 Task 的 lineage 可能错误共享 |
| R-08 | 当前没有 Heartbeat、Reaper 或 Reconciler | 长任务和崩溃恢复不可验证 |

这些缺口由 `backend/tests/reliability/` 中的 strict xfail 契约固定。strict xfail 表示“当前已知失败，但目标断言不能删除或放宽”。

## 4. 可靠性不变量

| ID | 不变量 |
|---|---|
| INV-01 | 任意时刻，一个 Task 最多只有一个当前有效 Attempt。 |
| INV-02 | Claim 成功是数据库原子事件；未成功写入的 Worker 不得执行 Provider。 |
| INV-03 | `attempts` 只在 Claim 成功时增加一次。 |
| INV-04 | 每次 Claim 产生新的 `attempt_id`、`lease_generation` 和不可复用 `lease_token`。 |
| INV-05 | Heartbeat、成功、失败、Artifact 预留和最终发布都验证完整租约身份。 |
| INV-06 | 旧 Attempt 的迟到响应不能改变 Task 或 Artifact 终态。 |
| INV-07 | 达到最大尝试次数的任务必须进入 FAILED 或 CANCELLED。 |
| INV-08 | SUCCEEDED、FAILED、CANCELLED 是普通执行路径不可离开的终态。 |
| INV-09 | 一个 Task 的一种最终结果只有一个 Artifact 身份；内容去重不得破坏 lineage。 |
| INV-10 | 数据库与文件系统无法共享事务，但所有中间状态必须可扫描、判定和恢复。 |
| INV-11 | Provider 调用期间不持有数据库写事务，不长期复用 Claim Session。 |
| INV-12 | Clock、Token、退避与故障点可注入；自动测试不依赖真实长时间 sleep。 |

## 5. 状态模型

### 5.1 Task 状态

```text
PENDING
  +-- claim ----------------> RUNNING
  +-- cancel ---------------> CANCELLED

RETRY_WAIT
  +-- due + claim ----------> RUNNING
  +-- cancel ---------------> CANCELLED

RUNNING
  +-- fenced success -------> SUCCEEDED
  +-- retryable failure ----> RETRY_WAIT
  +-- permanent/exhausted --> FAILED
  +-- cancel request -------> CANCEL_REQUESTED
  +-- lease expiry ---------> Reaper decides RETRY_WAIT / FAILED

CANCEL_REQUESTED
  +-- worker acknowledge ---> CANCELLED
  +-- lease expiry ---------> CANCELLED

SUCCEEDED / FAILED / CANCELLED
  +-- terminal; ordinary worker commands cannot reopen
```

### 5.2 Attempt 状态

```text
RUNNING
  +-- success -------------> SUCCEEDED
  +-- retryable failure ---> RETRYABLE_FAILED
  +-- permanent failure ---> PERMANENT_FAILED
  +-- lease expiry --------> EXPIRED
  +-- cancellation --------> CANCELLED
  +-- fencing rejection ---> STALE
```

### 5.3 Attempt 计数

- `attempt_no` 从 1 开始，对同一 Task 单调递增。
- `max_attempts=3` 表示最多真实开始执行三次，包括第一次。
- 失败调度不会增加计数；下一次 Claim 成功时才增加。
- 每个 Attempt 独立记录 Worker、Provider、用量、错误、开始、心跳和结束时间。

## 6. 数据模型

### 6.1 Task 增补字段

| 字段 | 说明 |
|---|---|
| `status` | 增加 RETRY_WAIT、CANCEL_REQUESTED |
| `current_attempt_id` | 当前有效 Attempt 外键，可空 |
| `lease_generation` | 每次 Claim 原子加 1 |
| `next_attempt_at` | RETRY_WAIT 可再次领取的时间 |
| `cancel_requested_at` | 取消请求时间 |
| `last_error_code` | 最近稳定错误码 |
| `last_error_message` | 截断后的用户诊断信息 |
| `result_artifact_id` | 最终结果；成功后不可被普通路径覆盖 |

迁移期间可保留 Task 上现有 `lease_owner` 与 `lease_expires_at`，但 TaskAttempt 是租约权威源。完成切换并验证数据迁移后，再单独清理重复字段。

### 6.2 TaskAttempt 表

| 字段 | 约束或作用 |
|---|---|
| `id` | 主键 |
| `task_id` | Task 外键 |
| `attempt_no` | `UNIQUE(task_id, attempt_no)` |
| `lease_generation` | Claim 代次 |
| `lease_token` | 全局唯一、不可复用 |
| `worker_id` | 领取者 |
| `status` | Attempt 状态 |
| `started_at` | Claim 成功时间 |
| `heartbeat_at` | 最近成功心跳 |
| `lease_expires_at` | 当前租约截止时间 |
| `finished_at` | Attempt 终止时间，可空 |
| `provider_name` | 实际 Provider，可空 |
| `error_code/message` | 稳定错误与诊断，可空 |
| `usage_json` | Token 与成本占位 |

必须建立：

- `UNIQUE(task_id, attempt_no)`
- `UNIQUE(lease_token)`
- `INDEX(status, lease_expires_at)`
- `INDEX(task_id, started_at)`

### 6.3 Claim 返回值

`claim_next_task` 后续返回短生命周期的 Claim 结果，而不是让 Worker 长期持有可修改 ORM Task：

```text
ClaimedTask
  task_id
  project_id
  kind
  payload
  attempt_no
  current_attempt_id
  lease_token
  lease_generation
  lease_expires_at
```

Worker 执行期间只携带这份不可变租约身份；后续数据库操作重新打开短 Session。

## 7. 固定命令契约

PR B.1 的行为测试固定以下命令名与最小参数，避免实现阶段只增加空壳字段：

```python
reap_expired_tasks(session, *, now) -> int

fail_task_attempt(
    session,
    *,
    task_id,
    attempt_id,
    lease_token,
    lease_generation,
    error_code,
    error_message,
    retryable,
    retry_after_seconds,
    now,
)

request_task_cancellation(session, *, task_id, now)

complete_task_attempt(
    session,
    *,
    task_id,
    attempt_id,
    lease_token,
    lease_generation,
    result_artifact_id,
    now,
)
```

这些命令必须使用条件更新并检查影响行数。返回对象可以在实现 PR 中定义，但数据库最终状态和稳定错误码必须满足测试。

## 8. SQLite 原子 Claim

M0 使用短事务 `BEGIN IMMEDIATE`，在读取候选任务前取得写锁。第二个领取者只能等待或得到 `SQLITE_BUSY`，不能在旧快照中领取同一任务。

### 8.1 正确事务顺序

Task 的 `current_attempt_id` 是外键，不能先指向尚不存在的 Attempt。固定顺序为：

```sql
BEGIN IMMEDIATE;

SELECT id, attempts, max_attempts, lease_generation
FROM tasks
WHERE
  (status = 'PENDING'
   OR (status = 'RETRY_WAIT' AND next_attempt_at <= :now))
  AND attempts < max_attempts
ORDER BY COALESCE(next_attempt_at, created_at), created_at
LIMIT 1;

INSERT INTO task_attempts (
  id, task_id, attempt_no, lease_generation,
  lease_token, worker_id, status,
  started_at, heartbeat_at, lease_expires_at
)
VALUES (...);

UPDATE tasks
SET status = 'RUNNING',
    attempts = attempts + 1,
    lease_generation = lease_generation + 1,
    current_attempt_id = :attempt_id,
    started_at = COALESCE(started_at, :now),
    last_error_code = NULL,
    last_error_message = NULL
WHERE id = :task_id
  AND attempts = :previous_attempts
  AND lease_generation = :previous_generation
  AND attempts < max_attempts
  AND (status = 'PENDING'
       OR (status = 'RETRY_WAIT' AND next_attempt_at <= :now));

-- rowcount must be 1; otherwise ROLLBACK removes the inserted Attempt
COMMIT;
```

也可以使用 `DEFERRABLE INITIALLY DEFERRED` 外键，但 M0 默认采用“先插 Attempt、后更新 Task、失败整体回滚”，避免依赖延迟约束。

### 8.2 SQLite 运行要求

- Claim 使用全新短生命周期 Connection/Session。
- Claim 前不得先在同一 Session 查询 Task。
- 设置 `PRAGMA busy_timeout`。
- `SQLITE_BUSY` 只做有上限的短重试，并记录诊断。
- Claim 事务内禁止 Provider、文件 I/O 和网络日志发送。
- Windows 多进程竞争必须有实机测试。

## 9. Heartbeat 与 Fencing

Worker 执行顺序：

```text
短事务 Claim
-> 关闭 Claim Session
-> 启动独立 Heartbeat
-> Provider 调用
-> Attempt staging
-> fenced Artifact reserve/finalize
-> fenced Task completion
-> 停止 Heartbeat
```

Heartbeat、完成、失败和 Artifact 操作必须同时匹配：

```text
task_id + attempt_id + lease_token + lease_generation + worker_id
```

还必须确认：

- Attempt 状态仍为 RUNNING；
- Task 状态仍为 RUNNING；
- Task 的 `current_attempt_id` 仍指向该 Attempt；
- 完成时租约尚未过期。

影响行数为 0 时返回稳定结果：

- `LEASE_LOST`
- `ATTEMPT_STALE`
- `TASK_CANCEL_REQUESTED`
- `TASK_ALREADY_TERMINAL`

无法立即中断的远端 Provider 调用可以继续返回，但其最终提交必须被 fencing 拒绝。

## 10. Reaper、重试与取消

### 10.1 Reaper

Reaper 在 Worker 启动时运行，并按固定短间隔扫描过期 Attempt：

- CANCEL_REQUESTED -> Attempt CANCELLED；Task CANCELLED。
- attempts >= max_attempts -> Attempt EXPIRED；Task FAILED；错误 `LEASE_EXPIRED_MAX_ATTEMPTS`。
- 尚可重试 -> Attempt EXPIRED；Task RETRY_WAIT；设置 `next_attempt_at`。

Claim 只领取 PENDING 和已到期 RETRY_WAIT，不直接偷取 RUNNING。

### 10.2 错误分类

| 错误码 | 可重试 | Task 结果 |
|---|---:|---|
| `PROVIDER_TIMEOUT` | 是 | RETRY_WAIT 或次数耗尽 FAILED |
| `PROVIDER_RATE_LIMITED` | 是 | 使用 retry_after 或退避 |
| `PROVIDER_UNAVAILABLE` | 是 | RETRY_WAIT |
| `PROVIDER_INVALID_OUTPUT` | 默认一次 | 配置化重试 |
| `PROVIDER_AUTH_FAILED` | 否 | FAILED |
| `PROVIDER_BAD_REQUEST` | 否 | FAILED |
| `UNSUPPORTED_TASK_KIND` | 否 | FAILED |
| `LEASE_LOST` | 不作为 Provider 失败 | Attempt STALE |

退避默认：

```text
next_attempt_at = now + min(base * 2^(attempt_no - 1), max_backoff)
```

测试关闭随机抖动并注入 Clock。真实运行可注入 jitter；Provider 的 `retry_after` 优先。

### 10.3 取消竞争

- PENDING/RETRY_WAIT：直接 CANCELLED。
- RUNNING：改为 CANCEL_REQUESTED，不删除当前 Attempt。
- 取消先提交：后续成功提交必须被拒绝。
- fenced 成功先提交：后续取消返回当前 SUCCEEDED，不改写终态。
- 重复取消幂等。
- 手动重试终态任务必须创建显式新策略或新 Task，不在原地绕过 `max_attempts`。

## 11. Provider Contract

TaskExecutor 不再创建 `FakeProvider()`，而是接收 Provider Registry/Resolver：

```python
class Provider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        ...


class ProviderError(Exception):
    code: str
    retryable: bool
    retry_after_seconds: float | None
```

Fake Provider 必须提供确定性模式：

- success
- timeout
- rate_limit
- temporary_unavailable
- invalid_output
- auth_failed
- permanent_error
- block_until_cancelled

Fake 场景不访问网络，不依赖真实 sleep。

## 12. Artifact 身份与恢复

### 12.1 两类对象

- Artifact：Task 级结果身份与 lineage。
- Blob：按内容哈希寻址的不可变文件，可被多个 Artifact 引用。

相同内容的不同 Task 可以共享一个 Blob，但必须有各自的 Artifact 记录。

### 12.2 约束

- `UNIQUE(artifacts.result_key)`：同一 Task、同一结果类型只有一个 Artifact 身份。
- `artifact_blobs.content_hash`：内容去重。
- Artifact 保留最终成功 Attempt 的 ID 与 Generation。
- 旧 Attempt 的历史保存在 TaskAttempt 与恢复审计记录中。

如果旧 Attempt 已预留 result_key，新 Attempt 只能在 Reconciler 已将旧预留标为 STALE 后，通过条件更新接管同一 Artifact 身份。不能插入第二个同 result_key 记录，也不能无条件覆盖旧代次。

### 12.3 提交流程

```text
A. Provider 返回并通过 Schema 校验
B. 写 Attempt 专属 staging，计算 SHA-256
C. fenced 预留 Artifact WRITING
D. staging 原子提升为 content-addressed Blob
E. 验证 Token、Generation、Blob 哈希与 Artifact 状态
F. 同一数据库事务：Artifact READY + Task SUCCEEDED + Attempt SUCCEEDED
```

### 12.4 崩溃恢复

| 崩溃点 | 遗留 | 恢复动作 |
|---|---|---|
| staging 后、预留前 | 临时文件 | TTL 清理 |
| WRITING 后、Blob 前 | WRITING + staging | 继续提升或 FAILED |
| Blob 后、最终事务前 | WRITING + Blob | 当前代次完成；旧代次 STALE |
| 最终事务后 | 完整终态 | 幂等返回 |
| 旧 Attempt 晚到 | staging/WRITING | fencing 拒绝，不覆盖 |

Reconciler 必须重复运行安全，并对 READY 文件缺失、哈希不匹配、孤儿 Blob 和孤儿 staging 输出可诊断报告。

## 13. 红灯测试基线

当前 strict xfail 契约覆盖：

- 双 Worker 同时 Claim；
- Attempt、Token、Generation 身份；
- 旧 Worker 完成隔离；
- Heartbeat 接口身份；
- 过期 Attempt 在有预算时进入 RETRY_WAIT；
- 过期 Attempt 达上限时进入 FAILED；
- retryable failure 进入 RETRY_WAIT；
- permanent failure 直接 FAILED；
- CANCELLED 终态不可原地重开；
- 取消先提交时拒绝成功；
- 成功先提交时取消不得改写终态；
- RETRY_WAIT 与 CANCEL_REQUESTED 状态；
- Provider 注入边界；
- Artifact 身份与 Blob 分离；
- Artifact Reconciler 存在。

`test_gap_manifest.py` 固定所有 gap ID 与 strict xfail 数量。删除测试、复制 ID、漏写 `strict=True` 或静默改变基线都会让 CI 失败。

实现 PR 仍需为以下边界补充普通回归测试：

- 20 Worker 竞争一个 Task；
- 两个 Worker 领取两个不同 Task；
- `SQLITE_BUSY` 有界重试；
- Heartbeat 正确续租与旧 Token 拒绝；
- 长 Provider 调用期间持续续租；
- 三次 retryable failure 后终止；
- Provider retry_after 优先；
- 取消 API 幂等；
- Artifact 各崩溃注入点与重复恢复器。

## 14. 固定 PR 顺序

这是后续唯一推荐顺序：

| PR | 中心目标 | 合并条件 |
|---|---|---|
| A | GitHub Actions CI Gate | 已完成 |
| B | Reliability Test Baseline | 已完成 |
| B.1 | 设计入库与高风险行为测试补全 | 只改测试与文档；strict xfail 基线稳定 |
| C | TaskAttempt Schema | 迁移升降级、旧数据升级和模型一致性通过 |
| D | Atomic Claim | Claim 红灯转绿；并发与 BUSY 测试通过 |
| E | Lease Fencing & Heartbeat | 旧 Worker、续租、长任务测试通过 |
| F | Retry & Cancellation | Reaper、失败分类、退避、取消测试通过 |
| G | Provider Contract | 注入、稳定错误码、Fake 故障模式通过 |
| H | Artifact Identity & Blob | result_key、Blob 分离和约束通过 |
| I | Artifact Recovery | 清理、对账、故障注入和幂等恢复通过 |
| J | Run Center Reliability UI | Attempt、错误码、重试时间和取消可见 |
| K | M0 Reliability Gate | 全矩阵、Windows 强杀与恢复记录通过 |

## 15. Migration Gate

PR C 开始，空库 upgrade/downgrade 不再足够。每个 Migration PR 必须同时验证：

1. 空数据库 upgrade -> downgrade -> upgrade；
2. 0001 数据库写入 Project、Task、Artifact 示例数据；
3. 从 0001 升级到最新 head；
4. 原数据仍可查询，默认值和外键正确；
5. Alembic 创建的 schema 与 ORM 模型一致；
6. 应用测试至少有一组运行在 Alembic 迁移后的数据库上，而不是只用 `Base.metadata.create_all()`。

## 16. 进入 VS01 的最终 Gate

- Claim、Lease、Retry、Cancel、Provider、Artifact 恢复测试全部普通通过。
- 仓库中不再有 M0 可靠性 strict xfail。
- Windows 实机完成双 Worker 竞争、强杀 Worker、租约过期恢复。
- 恢复后无重复付费路径、无永久 RUNNING、无旧 Worker 覆盖。
- READY Artifact、Task 和文件系统对账一致。
- `main` required checks 全部成功，最近合并有独立 Actions 记录。
- M0-04、M0-06、M0-08 更新验证证据并关闭。

## 17. 公开仓库边界

仓库公开后，提交前必须运行 `scripts/check_repo_hygiene.py`，并确认：

- 不跟踪 `.env`、Secret、数据库、Workspace 用户数据、Artifact 和日志；
- 测试语料可公开且不包含未授权小说正文；
- 文档不包含 API Key、账号、机器私有路径或个人数据；
- PR 只包含明确白名单文件；
- GitHub Secret Scanning 与 Push Protection 作为仓库设置单独启用和验证。

## 18. 参考

- SQLite transactions: <https://www.sqlite.org/lang_transaction.html>
- SQLite UPDATE: <https://www.sqlite.org/lang_update.html>
- SQLite RETURNING: <https://www.sqlite.org/lang_returning.html>
- SQLAlchemy DML: <https://docs.sqlalchemy.org/en/20/core/dml.html>
- GitHub protected branches: <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
