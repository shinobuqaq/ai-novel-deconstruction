# AI 自动小说拆书分析器

这是项目的 **M0 工程骨架 + Vertical Slice 01 起点**。当前仓库只实现最小可运行闭环：

- FastAPI API
- SQLite + Alembic
- Project / Task / Artifact 三类基础对象
- 单机轮询 Worker 与任务租约
- Fake Provider
- React + TypeScript + Vite 最小控制台
- Windows 一键安装、启动与测试脚本
- M0—M8 开发 Backlog

> 当前版本不是完整拆书分析器。它用于验证仓库结构、任务恢复、Artifact 写入、前后端连接和开发流程。

## 1. Windows 快速启动

在项目根目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\scripts\dev.ps1
```

启动后：

- 前端：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

`dev.ps1` 会分别启动 API、Worker 和 Frontend。关闭对应 PowerShell 窗口即可停止。

## 2. 验证最小闭环

1. 在页面创建项目。
2. 创建一个 `fake.echo` 任务。
3. Worker 领取任务并生成不可变 JSON Artifact。
4. 页面刷新后可看到任务状态由 `PENDING` 变为 `SUCCEEDED`。
5. Artifact 文件写入 `workspace/artifacts/<project_id>/`。

## 3. 常用命令

```powershell
# 只初始化数据库
.\scripts\init-db.ps1

# 运行测试
.\scripts\test.ps1

# 只运行一次 Worker（方便调试）
.\.venv\Scripts\python.exe -m app.worker --once
```

## 4. 当前目录

```text
ai-novel-deconstruction/
├─ backend/               FastAPI、Worker、领域与持久化
├─ frontend/              React + TypeScript + Vite
├─ docs/                  Roadmap、Issue Backlog、ADR
├─ prompts/               后续 Prompt Registry
├─ schemas/               Structured Output JSON Schema
├─ fixtures/              小型可重复测试语料
├─ scripts/               Windows 开发脚本
├─ workspace/             本地数据库、Artifact 与用户数据（不进 Git）
└─ .env.example
```

## 5. 当前编码顺序

先完成 `docs/ISSUE_BACKLOG.md` 中的 **M0 Ready Queue**，随后进入 Vertical Slice 01：

```text
导入 2—3 章
→ EvidenceSpan
→ 一个实体候选路线
→ 一个事件 LLM 路线
→ Source Alignment
→ Candidate / Issue
→ 简单 Claim
→ Evidence Inspector
```

## 6. 研究成果如何进入代码

本仓库不是从 P01—P18 中选择一个项目 Fork 而来，而是依据跨项目审计结论重新实现的组合式架构。

- [研究追踪矩阵](docs/RESEARCH_TRACEABILITY.md)：Pxx / M / G → 模块 → Issue → 验证
- [第三方代码登记](docs/THIRD_PARTY_CODE.md)：当前未复制 P01—P18 源码；未来引入必须固定来源与许可证
- [开发路线图](docs/ROADMAP.md)
- [Issue Backlog](docs/ISSUE_BACKLOG.md)

## 7. 文档权威

- 产品范围与验收：`《产品定义与 Quality Mode 原型方案 V0.1》`
- 架构与技术边界：`《候选系统架构与技术设计 V0.1》`
- 本 README 只提供开发入口，不替代上述两份正式文档。

## 8. License

本仓库自有代码采用 [Apache License 2.0](LICENSE)。第三方依赖与未来引入制品仍受其各自许可证约束。
