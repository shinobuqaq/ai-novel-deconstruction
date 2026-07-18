# AI 自动小说拆书分析器

这是“AI 小说拆解工作台”的公开开发仓库。产品目标、页面、用户流程、参考机制、真实进度和开发顺序统一以 [当前基线 V1.0](docs/CURRENT_BASELINE.md) 为准。

当前仓库已经完成 P0 工程地基的关键可靠性能力，并跑通整本导入、章节确认、第一版人物事件分析和原文回查，但 P1、P2 尚未完成复杂样本、跨章归一和正式验收：

- TXT、Markdown、DOCX、EPUB 整本导入与章节确认
- 在线模型服务、模型目录和任务分析方案
- 第一版人物与事件候选及原文证据回查
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

当前先修正模型设置语义，再完成 P1、P2 的正式验收范围：

```text
模型自动参数、真实模型测试和能力过滤
→ 复杂导入样本与章节校正
→ 人物归一与剧情阶段
→ 跨章事件合并
→ 整本导入、人物事件和原文回查的第一次正式验收
```

## 6. 研究成果如何进入代码

本仓库不是从 P01—P19 中选择一个项目 Fork 而来。产品需求先决定需要什么零件，再从参考项目吸收适用的机制、思想或实现边界；不因为原项目存在某项功能就照搬同名产品功能。

- [当前基线 V1.0](docs/CURRENT_BASELINE.md)：产品设计、页面、功能、参考零件矩阵、进度和开发顺序
- [第三方代码登记](docs/THIRD_PARTY_CODE.md)：当前未复制 P01—P19 源码；未来引入必须固定来源、版本和许可证
- [工程专项文档](docs/engineering/)：当前持续集成等工程说明
- [历史工程记录](docs/archive/)：已完成阶段的设计、测试和验收记录，不再表示当前进度
- [架构决策](docs/adr/)：已经接受的架构选择及原因

## 7. 文档权威

1. [当前基线 V1.0](docs/CURRENT_BASELINE.md)：唯一当前产品与系统判断入口
2. `P01—P19` 单项目档案：需要具体零件时回查的研究证据
3. [工程专项文档](docs/engineering/) 与 [架构决策](docs/adr/)：当前专项实现和验证依据，不能修改产品目标
4. 本 README：启动和开发入口，不替代当前基线

## 8. License

项目仓库已经公开，但许可证尚未最终确定。公开可见不等于已经授权他人复制、修改或分发；正式发布前需补充 `LICENSE`，并完成第三方依赖与通知复核。
