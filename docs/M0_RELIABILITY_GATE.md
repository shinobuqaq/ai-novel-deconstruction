# M0 可靠性最终验收

## 当前状态

PR K 提供唯一、可重复的 Windows 可靠性验收入口：

```powershell
.\scripts\verify-m0-reliability.ps1
```

命令使用 `workspace/diagnostics/m0-gate/<时间戳>/` 下的独立数据库和 Workspace，不读取小说正文、API Key 或正常应用数据库。

## 验收内容

1. 把全新 SQLite 数据库迁移到最新 Alembic 版本。
2. 创建一个 Project 和一个 Task。
3. 启动独立进程，原子领取 Task 并记录 Attempt 身份。
4. 在领取结果持久化后强制结束该进程。
5. 等待两秒 Lease 过期。
6. 运行一次正式 Worker。
7. 确认旧 Attempt 为 `EXPIRED`，恢复 Attempt 为 `SUCCEEDED`，Task 恰好执行两次后成功。
8. 确认 Artifact 和 Blob 均为 `READY`，Blob 文件哈希正确，且没有临时文件残留。
9. 运行完整后端可靠性测试目录。

## 输出文件

每次运行保留：

- `migration.log`：数据库迁移日志；
- `claimed.json`：强杀前的领取身份；
- `crash-probe.stdout.log`：崩溃探针标准输出；
- `crash-probe.stderr.log`：崩溃探针错误输出；
- `recovery-worker.log`：正式 Worker 恢复日志；
- `reliability-tests.log`：可靠性测试日志；
- `result.json`：最终机器可读验收结果。

成功的 `result.json` 必须满足：

```text
task_status = SUCCEEDED
attempts = 2
attempt_statuses = [EXPIRED, SUCCEEDED]
artifact_status = READY
blob_status = READY
blob_file_valid = true
temp_files = []
```

## 剩余产品验收

本 Gate 通过代表 M0 工程可靠性完成。进入 VS01 前仍需由最终用户试用 Run Center，确认任务状态、错误、重试时间、取消操作和结果是否清楚、顺手。
