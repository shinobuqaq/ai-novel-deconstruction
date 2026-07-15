# GitHub Actions CI Gate

## 1. 目的

本文件定义 M0 可靠性重基线实施阶段的远端质量门禁。

PR A 只建立独立验收能力，不修改 Task、Provider、Artifact 或数据库运行语义。后续 Task Reliability、Provider Contract 和 Artifact Recovery PR 必须先经过这里定义的远端检查。

## 2. Workflow 触发条件

`.github/workflows/ci.yml` 在以下情况运行：

- 向仓库提交 Pull Request；
- 向 `main` 推送；
- 手动执行 `workflow_dispatch`。

Workflow 使用只读仓库权限：

```yaml
permissions:
  contents: read
```

不读取仓库 Secret，不执行自动合并。

## 3. 检查名称

后续应将以下检查配置为 `main` 的 Required status checks：

| Check | 目的 |
|---|---|
| `repository-hygiene` | UTF-8、乱码、生成目录、数据库、`.env`、Artifact 等仓库卫生检查 |
| `backend-python-3.12` | Python 3.12 后端测试 |
| `backend-python-3.13` | Python 3.13 后端测试 |
| `migration-sqlite` | 空 SQLite 数据库执行 upgrade → downgrade → upgrade |
| `frontend-node-20` | `npm ci` 与生产构建 |
| `windows-powershell-smoke` | Windows 环境执行 PowerShell 安装、测试、Migration 与前端构建 |

## 4. 合并规则

PR A 本地执行包会：

1. 创建分支；
2. 本地运行卫生检查、后端测试、Migration 和前端构建；
3. 创建 **Draft PR**；
4. 等待 GitHub Actions；
5. 输出结果日志。

本地执行包不会：

- 自动将 PR 标记为 Ready；
- 自动合并；
- 自动修改 `main` 的 Ruleset 或 Branch protection。

只有远端检查全部成功并完成人工审阅后，才能将 PR 标记为 Ready 并合并。

## 5. PR A 合并后的仓库设置

PR A 合并到 `main` 后，在仓库 Ruleset 或 Branch protection 中至少启用：

- Require a pull request before merging；
- Require status checks to pass；
- Require branches to be up to date before merging；
- 将第 3 节列出的六项检查设为 Required；
- 禁止绕过失败检查直接合并。

仓库设置属于 GitHub 管理配置，不保存在本次代码 PR 中。应在 PR A 合并后单独配置并验证。

## 6. 仓库卫生检查

`scripts/check_repo_hygiene.py` 同时检查 Git 已跟踪文件和本地未忽略文件，拒绝：

- `.env` 与 `secrets/`；
- `workspace` 用户数据；
- SQLite 数据库及 WAL 文件；
- `node_modules`、`dist`、`.venv`、缓存与 `egg-info`；
- `*.tsbuildinfo`；
- 非 UTF-8 文本；
- Unicode 替换字符、私用区字符和已知中文乱码片段；
- 无效的 `.env.example` CORS JSON。

## 7. 本阶段不解决的问题

CI Gate 只能证明检查被独立执行，不能自动证明当前 Task 系统已经可靠。

以下问题仍由后续 PR 处理：

- 原子 Task Claim；
- Lease Token / Generation 与旧 Worker 隔离；
- Heartbeat；
- 自动重试与最大次数终态；
- 取消语义；
- Provider 注入和稳定错误码；
- Artifact 幂等提交与崩溃恢复。

## 8. 下一实施项

PR A 合并且 Required checks 配置完成后，进入：

> PR B：Task Reliability Test Baseline

PR B 先加入并发领取、旧 Worker 提交、租约过期、重试耗尽、取消竞争等红灯测试，不先修改实现。
