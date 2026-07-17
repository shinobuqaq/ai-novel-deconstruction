# AI 自动小说拆书分析器

这是项目的 **M0 工程骨架 + Vertical Slice 01 起点**。当前仓库实现的是最小可运行闭环，而不是完整的小说拆书产品：

- FastAPI API
- SQLite + Alembic
- Project / Task / Artifact 三类基础对象
- 单机轮询 Worker 与任务租约
- Fake Provider
- React + TypeScript + Vite 最小控制台
- Windows 一键安装、启动与测试脚本
- M0—M8 开发 Backlog

> M0 的目标是验证仓库结构、任务恢复、Artifact 写入、前后端连接和开发流程。后续能力按 `docs/ISSUE_BACKLOG.md` 逐步实现。


## 0. 环境要求

- Windows 10/11
- Git
- Python 3.12 或 3.13
- Node.js 20 或更新版本（包含 npm）

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

# M0 Windows 强杀恢复与最终可靠性验收
.\scripts\verify-m0-reliability.ps1

# 只运行一次 Worker（方便调试）
.\.venv\Scripts\python.exe -m app.worker --once
```

## 4. 当前目录

```text
ai-novel-deconstruction/
├─ backend/               FastAPI、Worker、领域与持久化
├─ frontend/              React + TypeScript + Vite
├─ docs/                  Roadmap、Issue Backlog、ADR、研究追踪
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

本仓库不是从 P01—P18 中选择一个项目 Fork 而来，而是依据跨项目审计结论进行 clean-room 独立实现。

- [研究追踪矩阵](docs/RESEARCH_TRACEABILITY.md)：Pxx / M / G → 模块 → Issue → 验证
- [第三方代码登记](docs/THIRD_PARTY_CODE.md)：当前未复制 P01—P18 源码；未来引入必须固定来源、版本和许可证
- [开发路线图](docs/ROADMAP.md)
- [Issue Backlog](docs/ISSUE_BACKLOG.md)
- [M0 Windows 冷启动与最小闭环验证记录](docs/M0_COLD_START_VALIDATION.md)
- [M0 可靠性最终验收](docs/M0_RELIABILITY_GATE.md)

## 7. 文档权威

1. `《产品定义与 Quality Mode 原型方案 V0.1》`：产品范围与验收
2. `《候选系统架构与技术设计 V0.1》`：架构与技术边界
3. `《机制演进台账 V0.16》`：研究机制状态
4. `《P01—P17 阶段总审计报告 V1.0》` 与 P01—P18 单项目档案：研究证据
5. 本 README：开发入口，不替代上述正式文档

## 8. License

项目当前仍为私有仓库，许可证尚未最终确定。在正式公开或分发前补充 `LICENSE`，并完成第三方依赖与通知复核。
