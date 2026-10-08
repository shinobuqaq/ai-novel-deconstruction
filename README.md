# AI 小说拆解工作台 (AI Novel Deconstruction Studio)

> **定位**：服务网文创作者的“方法论研习引擎”。从整本小说中抽取真实文本原料（实体、事件、事实、伏笔、冲突、节律），升华为结构化的 42 项创作方法论研报与开书工具包。

---

## 📚 权威文档导航

系统设计哲学、核心技术架构、创作问题规约与共创流程，统一以 `docs/` 下的四大核心白皮书为准：

- **[01 产品哲学与创作方法论白皮书](docs/01_PRODUCT_METHODOLOGY.md)**：永久记忆锚点与权威层级、系统定位、事实硬校验与文学软包容铁律、防走偏十条警钟。
- **[02 系统技术架构与设计基线](docs/02_SYSTEM_ARCHITECTURE.md)**：FastAPI + 独立 Worker + SQLite 模块化单体拓扑、六阶段流水线、42 问插件化体系（`QuestionPlugin`）、Pydantic V2 严格验证流、现代 Studio 前端交互规范。
- **[03 创作学习手册规约与标准范例](docs/03_LEARNING_HANDBOOK_SPEC.md)**：42 个北极星问题 P0/P1/P2 全景分级、层级 3 实操工具包规约、3.1 开场第一幕黄金标准示范案例。
- **[04 新书共创规范与标准开书包](docs/04_NEW_BOOK_COCREATION.md)**：从拆书输入到新书落地的 10 步共创流程、5 份标准交付文档规范、跨文档一致性校验与完整示范样本。
- **[技术调研与参考机制总览矩阵](docs/research/REFERENCE_MATRIX.md)**：P01~P19 深度技术调研报告总览、机制落地映射与第三方参考源码索引。

---

## 🌟 核心特色与架构设计

1. **现代 Studio 前端交互**：
   - **常驻工作室导航栏**（`Sidebar`）：支持多小说项目下拉切换与创建，4 大核心工作区即时跳转与状态徽章；
   - **全局原文证据滑动抽屉**（`EvidenceDrawer`）：点击任何指标或结论的证据标签，右侧平滑滑出毛玻璃抽屉，查阅原文段落、字符偏移量与前后上下文；
   - **四大沉浸视图**：创作学习手册（`LearningView` 双栏研报阅读与 42 问即时过滤搜索）、故事元素档案（`ArchiveView` 11 项细粒度解耦子组件库）、原文与章节（`SourceView` 书籍式纯净阅读器、长篇零依赖高性能虚拟滚动视窗与字符绝对坐标微光高亮）、分析控制中心（`AnalysisView` 向导式流水线控制）；
   - **独立技术排查弹窗**（`DiagnosticsModal`）：底层 LLM 调用日志、Token 统计与单模块重试功能完全独立，不干扰主屏创作。
2. **后端高内聚插件体系**：
   - 42 个创作问题彻底解耦为独立的 `QuestionPlugin` 插件，由全局注册中心（`registry.py`）按需动态发现与分发，彻底消灭历史硬编码双轨制；支持多态就绪评估（`assess_readiness`）与多态答案校验（`validate_answer`）。
3. **统一叙事场景与节律提取引擎（NarrativeSceneTree）**：
   - 将传统离散孤立的章末钩子（4.9）、开局结构（3.1）、前三章兑现（3.4）与人物出场信号（2.2），收敛于全局统一的场景树与时空节律提取器（`pacing_extractor.py`），支持按需分视图极速加载。
4. **双轨证据校验（客观事实硬约束 vs 主观文学软包容）**：
   - 深度贯彻《01 产品哲学与创作方法论白皮书》铁律，对阅读留存等文学推论采用软性批注（Soft Annotation）与置信度衰减，严禁因文学主观性触发硬性熔断；严格禁止越界计费造假。
5. **Pydantic V2 严格验证流**：
   - 全面淘汰脆弱的手工字典遍历与正则，模型输出直接经过模式校验，不合格当场重试或纠偏。
6. **长篇保真分窗与预算管理**：
   - 引入 15 万 Token 单次请求软上限，采用连续窗口切片聚合算法，杜绝大模型长上下文遗忘。

---

## 💻 环境要求

- **操作系统**：Windows 10 / 11
- **Python**：3.12 或 3.13（推荐使用项目内置 `.venv`）
- **Node.js**：20 或更高版本（含 npm）

---

## 🚀 快速启动

### 方式一：日常双击启动（推荐）
双击项目根目录下的 **`启动AI小说拆解工作台.bat`**。  
启动后将自动拉起前后端服务并打开工作台页面：
- **工作室前台**：`http://127.0.0.1:15173`
- **后端 API 文档**：`http://127.0.0.1:18000/docs`
- **健康检查接口**：`http://127.0.0.1:18000/health`

### 方式二：开发者命令行启动
```powershell
# 1. 激活后端虚拟环境并启动 API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 18000 --reload

# 2. 启动异步任务 Worker
.\.venv\Scripts\python.exe -m app.worker

# 3. 启动前端 Vite 开发服务器
cd frontend
npm run dev
```

---

## 🛠️ 常用开发与测试命令

```powershell
# 运行后端全量测试套件 (425 个自动化测试)
.\.venv\Scripts\python.exe -m pytest -q

# 编译与构建前端生产包 (TypeScript 类型检查 + Vite 打包)
cd frontend
npm run build

# 初始化或重置数据库
.\scripts\init-db.ps1
```

---

## 📁 项目目录结构

```text
ai-novel-deconstruction/
├── backend/                  # 后端源码 (FastAPI + Worker + 领域服务 + 插件体系)
│   ├── app/
│   │   ├── routers/          # 细粒度 RESTful 接口路由 (settings, projects, sources, analysis, learning, tasks, common)
│   │   ├── api.py            # 极简向前兼容 Facade 门面
│   │   ├── services/
│   │   │   ├── learning_questions/  # 42问插件化体系 (QuestionPlugin 与各专属插件)
│   │   │   ├── pacing_extractor.py  # 统一叙事场景与节律提取器 (NarrativeSceneTree 结构树与锚定投影)
│   │   │   ├── learning_report.py   # 学习手册调度引擎
│   │   │   ├── analysis.py          # 六阶段拆解逻辑与 Pydantic V2 校验
│   │   │   └── ...
│   │   └── worker.py         # 异步任务工作器
│   └── tests/                # 425 个自动化单元与集成测试
├── frontend/                 # 前端源码 (React 18 + TypeScript + Vite)
│   └── src/
│       ├── components/
│       │   ├── layout/       # AppLayout, Sidebar (现代化工作室布局)
│       │   ├── views/        # LearningView, ArchiveView, SourceView, AnalysisView
│       │   ├── archive/      # 11 个细粒度独立档案资产子组件 (CharacterList, PlotView 等)
│       │   ├── common/       # EvidenceDrawer (全局右侧原文证据抽屉)
│       │   └── DiagnosticsModal.tsx # 技术诊断排查弹窗
│       ├── ProductWorkbench.tsx # 主控工作台集成 (防抖持久化、分视图按需加载)
│       ├── NewBookCocreationPage.tsx # 独立下游新书共创工作台 (游离于拆解内核之外)
│       └── styles/           # 模块化现代领域样式层 (base, studio, archive, learning, source, pipeline)
├── docs/                     # 统一权威文档中心 (01~04 核心白皮书)
│   ├── 01_PRODUCT_METHODOLOGY.md
│   ├── 02_SYSTEM_ARCHITECTURE.md
│   ├── 03_LEARNING_HANDBOOK_SPEC.md
│   ├── 04_NEW_BOOK_COCREATION.md
│   └── research/             # 技术调研档案 (P01~P19 评估报告与参考源码)
│       ├── REFERENCE_MATRIX.md
│       ├── reference_projects/ # 第三方参考源码 (automated-novel-panel, xu-xie-ji)
│       └── fable_route_evidence/
├── samples/                  # 测试样书语料库
└── scripts/                  # 启动、安装与运维脚本
```