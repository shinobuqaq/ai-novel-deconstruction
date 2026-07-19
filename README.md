# AI 小说拆解工作台

这是“AI 小说拆解工作台”的公开开发仓库。产品目标、页面、用户流程、参考机制、真实进度和开发顺序统一以 [当前基线 V1.0](docs/CURRENT_BASELINE.md) 为准。

当前仓库已经完成 P0 工程地基，并形成第一次产品验收候选：整本导入、章节确认、真实人物档案、剧情阶段、事件时间线和原文回查已经连成一条可浏览流程。P1/P2 的复杂样本、争议归一和长篇质量验收仍未完成，不应把当前候选当作最终版本：

- TXT、Markdown、DOCX、EPUB 整本导入与章节确认
- 在线模型服务、模型目录和任务分析方案
- 第一版人物与事件候选及原文证据回查
- 人物档案、剧情阶段和事件时间线工作台
- 可恢复任务、重试、取消和幂等制品
- Windows 一键安装、启动和完整自动测试
- 普通工作台与 `/debug` 内部调试入口分离

> 当前各模块成熟度和下一步统一查看 `docs/CURRENT_BASELINE.md`，不再用一个总百分比掩盖差异。


## 0. 环境要求

- Windows 10/11
- Git
- Python 3.12 或 3.13
- Node.js 20 或更新版本（包含 npm）

## 1. Windows 快速启动

日常使用请双击项目根目录的 `启动AI小说拆解工作台.bat`。

启动后会保留一个可见的工作台窗口。这个窗口负责页面、后台服务和任务执行器的完整生命周期；关闭窗口会一并关闭它们，不会留下本项目的后台进程。

关闭浏览器标签页不会停止正在进行的长篇分析；需要彻底关闭系统时，关闭这个启动窗口即可。

首次安装或开发调试时，在项目根目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
```

安装完成后关闭 PowerShell，再双击 `启动AI小说拆解工作台.bat`。

正式启动后：

- 工作台：`http://127.0.0.1:15173`
- API 文档：`http://127.0.0.1:18000/docs`
- 健康检查：`http://127.0.0.1:18000/health`

`dev.ps1` 仅用于开发调试，会分别启动 API、Worker 和 Frontend；日常使用不要通过它启动。

## 2. 开发人员验证后台闭环

下面的操作只用于开发人员验证后台，不是小说拆解流程，最终用户无需验收：

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

# Windows 强制中断恢复与可靠性验收
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

模型设置已经具备自动参数、所选模型测试和能力过滤，人物事件分析已经记录任务提示词版本和最终请求摘要；当前可以进行第一次产品验收，验收后继续补齐：

```text
不同服务的真实兼容性
→ 争议人物的多信号归一与可逆拆分
→ 多路线事件候选、边界裁决和事件关系
→ 最小上下文预算、用量/成本诊断和长篇验收
```

## 6. 研究成果如何进入代码

本仓库不是从 P01—P19 中选择一个项目 Fork 而来。产品需求先决定需要什么零件，再从参考项目吸收适用的机制、思想或实现边界；不因为原项目存在某项功能就照搬同名产品功能。

- [当前基线 V1.0](docs/CURRENT_BASELINE.md)：完整产品、系统、数据、质量、参考机制、进度和开发顺序；不需要拼接旧总览
- [第三方代码登记](docs/THIRD_PARTY_CODE.md)：当前未复制 P01—P19 源码；未来引入必须固定来源、版本和许可证
- [工程专项文档](docs/engineering/)：当前持续集成等工程说明
- [架构决策](docs/adr/)：已经接受的架构选择及原因

## 7. 文档权威

1. [当前基线 V1.0](docs/CURRENT_BASELINE.md)：唯一当前产品与系统判断入口
2. `P01—P19` 单项目档案：需要具体零件时回查的研究证据
3. [工程专项文档](docs/engineering/) 与 [架构决策](docs/adr/)：当前专项实现和验证依据，不能修改产品目标
4. 本 README：启动和开发入口，不替代当前基线

## 8. License

项目仓库已经公开，但许可证尚未最终确定。公开可见不等于已经授权他人复制、修改或分发；正式发布前需补充 `LICENSE`，并完成第三方依赖与通知复核。
