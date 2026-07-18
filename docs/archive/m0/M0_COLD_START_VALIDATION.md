# M0 Windows 冷启动与最小闭环验证记录

## 1. 验证结论

M0 已在真实 Windows 环境完成冷启动和最小闭环验证：

```text
Project → Task → Worker → Artifact
```

验证日期：2026-07-13（用户实机记录）

## 2. 验证环境

- Windows 桌面环境
- Git 2.50.1.windows.1
- Python 3.13
- Node.js / npm 可用（本次记录未保存具体版本）
- SQLite + Alembic
- FastAPI + 单机轮询 Worker
- React + Vite
- GitHub `main` 基线：`8c71437`（PR #5 合并后）

## 3. 已通过项目

- [x] 从 GitHub 克隆最新 `main`
- [x] 创建 Python `.venv`
- [x] 安装后端依赖
- [x] 安装前端依赖，npm audit 为 0 vulnerabilities
- [x] Alembic 从零执行 `0001_m0_core`
- [x] 原始 M0 后端测试通过：2 passed；稳定化修复新增 2 个配置测试，预期共 4 passed
- [x] `/health` 返回 `status=ok`
- [x] FastAPI `/docs` 正常打开
- [x] 前端 Run Center 正常连接 API
- [x] 创建 Project
- [x] 创建 `fake.echo` Task
- [x] Worker 领取并完成 Task，`attempts=1`
- [x] 生成 READY Artifact
- [x] Artifact 内容可以通过 API 读取

## 4. 实机闭环证据

- Project ID：`prj_85632d4121324920af94007b905e80ea`
- Task ID：`tsk_19a9fdf73ede46ac83e5006d713df68b`
- Artifact ID：`art_aa1a6c6a9cbb438aa3ba54a6c8d1ccc7`
- Task 状态：`SUCCEEDED`
- Artifact 状态：`READY`
- Provider：`fake`
- 输入：`M0 fake provider end-to-end test`

Artifact 返回内容包含：

```json
{
  "response": {
    "echo": {
      "message": "M0 fake provider end-to-end test"
    },
    "provider": "fake",
    "task_kind": "fake.echo"
  },
  "usage": {
    "completion_tokens": 10,
    "prompt_tokens": 12
  }
}
```

## 5. 冷启动中发现并修复的问题

### 5.1 CORS 环境变量解析

原 `.env.example` 使用逗号分隔值，但 Pydantic Settings 会先对 `list[str]` 尝试 JSON 解码，导致首次启动失败。

修复后：

- `.env.example` 使用 JSON 字符串数组；
- 配置代码同时兼容 JSON 数组和逗号分隔格式；
- 新增两种格式的自动测试。

### 5.2 PowerShell 脚本假成功

原脚本没有检查外部程序的 `$LASTEXITCODE`，Alembic 或 pytest 失败后仍可能打印成功信息。

修复后：

- `setup.ps1` 检查 venv、pip、配置、npm 和 Alembic 的退出码；
- `init-db.ps1` 检查 Alembic 退出码；
- `test.ps1` 检查 pytest 退出码。

### 5.3 Windows PowerShell 5.1 中文显示

API 和数据库中的中文内容是正常 UTF-8；Windows PowerShell 5.1 的表格输出可能出现乱码。可在当前窗口执行：

```powershell
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
```

该问题仅影响终端显示，不影响数据库、API 或前端中的实际文本。

## 6. 非阻塞项

测试中出现 Starlette 关于 `httpx` / `httpx2` 的弃用警告。当前不影响 M0 正确性，留待依赖升级时处理。

## 7. 后续 Gate

M0 冷启动和基础闭环已通过。M0-08 仍需完成 Worker 在 Provider 返回后、Artifact commit 前崩溃的恢复测试，才能关闭完整恢复 Gate。
