# 02 系统技术架构与设计基线

> **文档性质**：后端架构、数据流拓扑、插件化规范与前端交互体系的唯一技术基准。  
> **代码对齐**：与 `backend/` 及 `frontend/` 生产代码 100% 对应。

---

## 1. 系统总体拓扑与架构原则

系统采用**模块化单体（Modular Monolith）与双进程分离模式**，在保证极简部署的同时，实现高耗时 AI 任务与前台交互的解耦：

```text
+-----------------------------------------------------------------------------------+
| 前端用户界面 (Frontend)                                                            |
+-----------------------------------------------------------------------------------+
| 前端用户界面 (Frontend)                                                            |
| React 18 + TypeScript + Vite + 模块化 CSS                                         |
| 现代 Studio 布局 (Sidebar + Main Canvas + Evidence Drawer + Diagnostics Modal)   |
| 核心能力: 42问即时过滤搜索、长文本虚拟滚动(Virtual Window)、字符绝对坐标荧光高亮  |
+-----------------------------------------------------------------------------------+
                                         ↕ HTTP REST API / 按需分视图加载 (端口: 18000)
+-----------------------------------------------------------------------------------+
| 后端 API 服务 (API Process: FastAPI 模块化 APIRouter - backend/app/routers/)      |
| - /settings: 模型探测、服务配置与系统健康状态                                     |
| - /projects: 项目管理与源小说文档导入                                             |
| - /sources: 卷章正文切片、人工分割/合并与原文证据检索                             |
| - /analysis: 六阶段流水线运行控制、分视图工作台(/workbench, /pacing)与差异对比   |
| - /learning: 42问创作研报启动与多态就绪校验                                       |
| - /tasks: 异步任务租约控制与工件查询                                              |
| - api.py: 极简聚合门面 (45行)，兼顾全局路由注册与测试 Monkeypatch 兼容             |
+-----------------------------------------------------------------------------------+
                                         ↕ 共享 SQLite 数据库 / 任务状态
+-----------------------------------------------------------------------------------+
| 异步任务工作器 (Worker Process)                                                   |
| - 轮询领取可恢复任务 (Lease/Heartbeat 租约机制)                                   |
| - 驱动六阶段拆解流水线与统一叙事场景与节律提取器 (pacing_extractor.py)            |
| - 软性置信度衰减与批注标记 (Soft Annotation) 防任务硬崩溃                         |
| - 调用大模型服务、结构化验证、不可变 Artifact 落盘                                 |
+-----------------------------------------------------------------------------------+
```

### 关键设计决定：
1. **进程隔离**：Web API 进程与长耗时任务 Worker 独立运行，长篇分析耗时几小时也不会阻塞前台页面操作。
2. **共享持久化**：两者共享本地 SQLite 数据库与不可变制品存储（Artifact Store），无网络分布式开销。
3. **完全可恢复与幂等性**：任务异常崩溃后，下次启动可凭租约超时机制自动接续，已完成的批次制品绝不重复生成。

---

## 2. 核心领域对象模型

```text
[SourceVersion] (小说源版本)
   │
   ├─► [SourceUnit] (卷章切片与目录树，含字符起止范围)
   │
   └─► [AnalysisRun] (拆解运行记录)
         │
         ├─► [EntityCandidate] (实体候选：人物、势力、地点、核心道具)
         │
         ├─► [EventCandidate] (事件候选：起因、经过、行动、证据切片)
         │
         ├─► [DeepAnalysis] (深度分析多维资产)
         │     ├─ FactVersion / StateChange (事实版本与状态演变)
         │     ├─ ActorKnowledge / KnowledgeTransfer (人物认知与信息流动)
         │     ├─ WorldRule (世界观硬规则与力量代价)
         │     ├─ Foreshadowing (伏笔埋设、强化与回收网络)
         │     ├─ Conflict (核心冲突与对抗升级)
         │     └─ SceneAnalysis (场景切片与叙事节奏)
         │
         └─► [LearningReport] (42问创作方法论学习研报)
               └─ 42项 WorkbenchLearningQuestion (指标、结论、实操工具包、证据追溯)
```

---

## 3. 六阶段拆解流水线与数据流

全书拆解按 6 个阶段严格推进，上游产出作为下游输入：

| 阶段 | 名称 | 核心工作 | 关键输出 |
| :---: | :--- | :--- | :--- |
| **Stage 1** | **导入与章节确认** | 支持 TXT/MD/DOCX/EPUB 文件解析；正则与启发式识别卷/章/序/作品信息；人工微调合并与切分 | `SourceVersion`, `SourceUnit[]` |
| **Stage 2** | **实体与事件抽取** | 分批滑动窗口并发抽取核心实体与关键事件候选；去重与别名归一；人工确认与纠偏 | `EntityCandidate[]`, `EventCandidate[]` |
| **Stage 3** | **叙事骨架与主线** | 串联事件因果链条；识别剧情阶段（起承转合）；主角开篇亮相与主线破局 | 剧情阶段图谱、事件因果图 |
| **Stage 4** | **深度事实与规则** | 事实时态演变分析（何时生效、何时过期）；人物信息差感知；世界观与力量法则提取 | `FactVersion[]`, `WorldRule[]`, `StateChange[]` |
| **Stage 5** | **伏笔与冲突网络** | 全书伏笔生命周期追踪（铺垫、强化、回收）；核心人际/内心/阵营冲突递进链；章末钩分布 | `Foreshadowing[]`, `Conflict[]`, 章末钩矩阵 |
| **Stage 6** | **42问创作学习研报** | 依据前 5 阶段沉淀的专项原料，由问题插件化体系编译并生成 42 项创作研报 | `LearningReport`（含指标、结论与工具包） |

---

## 4. 问题插件化架构 (`backend/app/services/learning_questions/`)

为了彻底消除历史遗留的“投影问题”与“程序编译问题”双轨硬编码混乱，系统将 42 个北极星问题全面插件化解耦：

```text
backend/app/services/learning_questions/
├── base.py              # QuestionPlugin 协议与 BaseQuestionPlugin 抽象基类
├── registry.py          # 全局注册中心，支持动态注册与按需分发
└── plugins/
    ├── q1_4.py          # 1.4 核心卖点与首个兑现点专项插件
    ├── q2_1.py          # 2.1 主角开篇亮相机制与人物节奏插件
    ├── q2_2.py          # 2.2 主角人设立体度与双层欲望插件
    ├── q3_1.py          # 3.1 黄金三章第一幕类型与开局插件
    ├── q3_2.py          # 3.2 前三章逐章任务与破局排布插件
    ├── q3_4.py          # 3.4 爽点频率与前三章兑现距离插件
    ├── q4_9.py          # 4.9 全书章末钩类型与节律分布插件
    └── standard.py      # 其余标准问题的通用兜底插件
```

### 插件核心接口契约：
- `requires_projection`: 声明该问题是否需要全书宏观投影数据；
- `is_program_compiled`: 声明该问题是由算法程序直接确定性编译，还是通过大语言模型分析；
- `compile_contract()`: 校验该问题的输入原料完整性；
- `apply_program_answer()`: 执行确定性算法生成答案；
- `assess_readiness(projection)`: **多态就绪评估契约**，由每个插件自治判定所需原料是否齐备，杜绝在总线上硬编码堆砌判断；
- `validate_answer(answer, projection, ...)`: **多态答案校验契约**，各问题自包含业务边界与引证校验。

---

## 5. Pydantic V2 严格验证流

系统全面废除靠 `dict.get()` 和手写正则切片解析大模型返回内容的土办法，全部升级为 **Pydantic V2 严格模型校验**：

```python
class SceneAnalysisProposal(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    chapter_ordinal: int
    function: str
    summary: str
    
    @field_validator("function", mode="before")
    @classmethod
    def _normalize_function_alias(cls, value: object) -> object:
        # 模型输出别名在入模前自动规范化，彻底消灭外围手工字典手术
        if isinstance(value, str):
            val_str = value.strip().upper()
            return _DEEP_SCENE_FUNCTION_COMPATIBILITY.get(val_str, val_str)
        return value
```

---

## 6. 现代 Studio 前端交互规范 (`frontend/src/`)

前端采用专业 **创作工作室（Studio）布局**，实现信息减负与沉浸阅读：

```text
+-----------------------------------------------------------------------------------------------+
| [Sidebar]          | [Main Studio Canvas Area]                      | [Evidence Drawer]       |
| - 小说项目切换      |                                                | (全局右侧平滑滑出)      |
| - 📖 创作学习手册   |   - LearningView (双栏大纲+即时搜索+深度研报)  | - 原文定位高亮          |
| - 🗂️ 故事结构档案   |   - ArchiveView (11项细粒度解耦子组件库)       | - 章节/字符偏移量       |
| - 📜 原文与章节     |   - SourceView (书籍式阅读+长文本虚拟滚动)     | - 前后上下文滑动阅读    |
| - ⚙️ 分析控制中心   |   - AnalysisView (向导式流水线控制面板)        | - "跳转到原文章节"      |
| ------------------ |                                                |                         |
| ● 系统正常 ⚙ 设置  |                                                |                         |
+-----------------------------------------------------------------------------------------------+
```

### 核心组件职责：
1. **`AppLayout.tsx`**：工作室根容器，统一管理全局导航、主画布、证据抽屉与诊断弹窗状态。
2. **`Sidebar.tsx`**：左侧深色常驻导航，集成项目快速切换浮层、4 大视图入口与徽章状态。
3. **`EvidenceDrawer.tsx`**：全局原文证据滑动抽屉，点击任何指标卡片中的证据标签均平滑滑出，阅读原汁原味上下文，杜绝内联页面撑爆。
4. **`ArchiveView.tsx` 与细粒度子组件库 (`frontend/src/components/archive/`)**：
   - 彻底废黜 102KB 历史单体巨石 `FormalWorkbench.tsx`；
   - 拆解为 11 个职责明确的独立子组件：`CharacterList`, `PlotView`, `EventsView`, `TimelineView`, `FactsView`, `WorldView`, `ForeshadowingView`, `ConflictsView`, `PacingView`, `OverviewView`, `IssuesView`，组件间通过标准 Props 单向数据流通讯。
5. **`SourceView.tsx` 精准定位与虚拟切片（Virtual Window）**：
   - 支持从证据抽屉一键平滑跳转，居中对齐目标段落并应用金色微光荧光标记（`<mark className="evidence-highlight-mark">`）；
   - 内建零外部依赖的虚拟窗口渲染，超过 80 段长章节自动激活切片，并自动锁定目标段落（Target Pinning），保障百万字小说 60fps 流畅浏览。
6. **现代化模块化样式库 (`frontend/src/styles/`)**：
   - 彻底解耦 145KB 全局单体 `styles.css`，划分为 `base`, `studio`, `archive`, `learning`, `source`, `pipeline` 模块化样式，零运行时开销。
7. **`DiagnosticsModal.tsx`**：技术调试排查弹窗，收拢所有底层的 LLM Prompt、Raw JSON、Token 计费与单模块重试，让纯粹创作主屏不受干扰。

---

## 7. 长篇保真切片与 Token 上限策略

针对长篇网文（几十万至上百万字）拆解，系统制定了严格的安全预算：
- **单次请求软上限**：单次学习答案请求统一设置 **15 万 Token 默认软上限**；
- **连续分窗处理**：超长章节或全书统计任务（如 4.9 章末钩）采用连续分窗算法逐段处理并聚合，防止模型发生“中间遗忘（Lost in the Middle）”；
- **流式增量与阶段落盘**：耗时较长的推理任务启用流式增量接收或分阶段检查点存储，杜绝“任务超时全盘皆空”。

---

## 8. 软性置信度衰减与批注标记机制 (Soft Annotation)

遵循《01 产品哲学与创作方法论白皮书》“客观事实硬校验 vs 主观文学判断软包容”之铁律：
- **客观底线坚决强拦截**：价格、货币单位、单价等违规商业字段坚决抛出 `LEARNING_REPORT_PRICING_OUT_OF_SCOPE` 致命异常拦截；
- **文学推断软性衰减包容**：对于模型总结中包含的读者阅读行为、留存倾向等无平台数据支持的主观外推，在生成模式（`soft_annotation=True`）下不中断流水线，而是转为结构化批注标记（`EXTERNAL_CAUSALITY_UNSUPPORTED`）并施加软性置信度衰减扣分（`confidence_penalty = 0.15`），交由人类创作者做最终审美判断。

---

## 9. 统一叙事场景与节律提取引擎 (`backend/app/services/pacing_extractor.py`)

系统引入高达零件理论中的“场景与节律核心总装矿山”：
- **统一叙事场景树 (`NarrativeSceneTree`)**：以章节为根、段落切片为叶，挂载场景功能、信息模块、章末钩子、兑现距离与角色行为信号；
- **5合1聚合提取与投影**：将分散的章末钩（4.9）、开篇结构（3.1）、前三章兑现（3.4）、卖点候选（1.4）与角色设计（2.2）归并到统一场景树上，大幅降低正文重复扫描与 Token 冗余；
- **分视图按需接口优化**：在 `/api/analysis-runs/{run_id}/workbench` 基础上，支持按需获取 `workbench/pacing`、`workbench/learning` 与 `workbench/archive`，降低前端轮询与首屏序列化压力。