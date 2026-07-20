# GitHub Actions 持续集成质量门禁

## 1. 当前职责

本文件只说明代码进入 `main` 前持续执行的远端检查。产品目标、阶段、当前能力和后续开发顺序统一以 `docs/CURRENT_BASELINE.md` 为准，不在这里保存旧 PR A～K 的历史计划。

持续集成可以证明仓库卫生、自动测试、数据库迁移和前端构建被独立执行，但不能代替真实小说质量评测、PC 页面检查或用户验收。

## 2. 触发条件

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

## 3. 当前检查

| Check | 目的 |
|---|---|
| `repository-hygiene` | UTF-8、乱码、生成目录、数据库、`.env`、制品等仓库卫生检查 |
| `backend-python-3.12` | Python 3.12 后端测试 |
| `backend-python-3.13` | Python 3.13 后端测试 |
| `migration-sqlite` | 空 SQLite 数据库执行 upgrade → downgrade → upgrade |
| `frontend-node-20` | `npm ci` 与生产构建 |
| `windows-powershell-smoke` | Windows 环境执行安装、测试、数据库迁移与前端构建 |

P0 任务领取、旧 Worker 隔离、重试、取消、模型服务失败分类、制品幂等和崩溃恢复已经进入正式代码与自动测试，不再作为“未来 PR”描述。

## 4. 合并规则

远端检查全部成功并完成代码审阅后才能合并。建议仓库规则至少启用：

- Require a pull request before merging；
- Require status checks to pass；
- Require branches to be up to date before merging；
- 将第 3 节检查设为 Required；
- 禁止绕过失败检查直接合并。

分支、PR、检查和合并属于开发流程，不要求最终用户参与验收。

## 5. 仓库卫生

`scripts/check_repo_hygiene.py` 检查 Git 已跟踪文件和本地未忽略文件，拒绝：

- `.env` 与 `secrets/`；
- `workspace` 用户数据；
- SQLite 数据库及 WAL 文件；
- `node_modules`、`dist`、`.venv`、缓存与 `egg-info`；
- `*.tsbuildinfo`；
- 非 UTF-8 文本；
- Unicode 替换字符、私用区字符和已知中文乱码片段；
- 无效的 `.env.example` 跨域配置。

## 6. 不能由本门禁证明的内容

- 在线模型服务的真实权限和兼容性；
- 人物归一、事件边界和高层分析的实际准确率；
- 5 万、50 万和 100 万字以上作品的质量、耗时和费用；
- PC 页面是否易懂、是否符合用户使用方式；
- 用户是否接受人物、剧情、事件和后续分析结果。

这些内容按当前基线的产品验收与长篇质量标准单独验证。
