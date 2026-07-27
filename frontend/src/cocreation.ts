export type StageResultStatus = "RESULT" | "NO_RESULT" | "INCOMPLETE" | "UNKNOWN";

export type FieldContract = {
  file: OpeningPackageFile;
  name: string;
  aliases: string[];
  necessity: "必填" | "条件必填" | "选填";
  lockTiming: "开写前锁定" | "开写前暂定" | "可延后";
  guidance: string;
};

export type StarterExample = {
  label: string;
  text: string;
};

export type CocreationStage = {
  id: number;
  name: string;
  summary: string;
  goal: string;
  whyNow: string;
  boundary: string;
  completionCriteria: string[];
  fields: FieldContract[];
  contextFiles: OpeningPackageFile[];
  examples: StarterExample[];
  references: string;
};

export type WritebackItem = {
  file: string;
  field: string;
  content: string;
  necessity: string;
  lockTiming: string;
  confirmation: string;
};

export type ParsedStageResult = {
  status: StageResultStatus;
  stageNumber: string;
  stageName: string;
  errors: string[];
  warnings: string[];
  confirmed: string;
  writebacks: WritebackItem[];
  unresolved: string;
  conflictCheck: string;
  nextImpact: string;
  noResultReason: string;
  reopenWhen: string;
  missingDecisions: string;
  cannotEndReason: string;
  nextDiscussion: string;
  canConfirm: boolean;
  blocksNext: boolean;
};

export type StageRecord = {
  stageId: number;
  userInput: string;
  resultBlock: string;
  confirmedAt: string;
  parsed: ParsedStageResult;
  needsReview: boolean;
};

export type StageDraft = {
  userInput: string;
  resultBlock: string;
};

export type CocreationWorkspace = {
  version: 2;
  activeStageId: number;
  stages: Record<string, StageRecord>;
  drafts: Record<string, StageDraft>;
  updatedAt: string;
};

export type SavedStageZeroV1 = {
  version: 1;
  userIdea: string;
  resultBlock: string;
  confirmedAt: string;
  parsed: ParsedStageResult;
  documentMarkdown: string;
};

export type DocumentField = WritebackItem & {
  stageId: number;
  stageName: string;
  confirmedAt: string;
  needsReview: boolean;
};

export type OpeningPackageDocument = {
  filename: OpeningPackageFile;
  title: string;
  fieldCount: number;
  noResultCount: number;
  needsReview: boolean;
  markdown: string;
};

export const PACKAGE_FILES = [
  "00_开书总表.md",
  "01_人物与关系.md",
  "02_世界与规则.md",
  "03_剧情与开篇.md",
  "04_连载规则与账本.md",
] as const;

export type OpeningPackageFile = (typeof PACKAGE_FILES)[number];

export const COCREATION_STORAGE_KEY = "ai-novel-deconstruction:new-book-cocreation:v2";
export const LEGACY_COCREATION_STORAGE_KEY = "ai-novel-deconstruction:new-book-cocreation:v1";

const DOCUMENT_TITLES: Record<OpeningPackageFile, string> = {
  "00_开书总表.md": "新书开书总表",
  "01_人物与关系.md": "人物与关系",
  "02_世界与规则.md": "世界与规则",
  "03_剧情与开篇.md": "剧情与开篇",
  "04_连载规则与账本.md": "连载规则与账本",
};

const field = (
  file: OpeningPackageFile,
  name: string,
  necessity: FieldContract["necessity"],
  lockTiming: FieldContract["lockTiming"],
  guidance: string,
  aliases: string[] = [],
): FieldContract => ({ file, name, aliases, necessity, lockTiming, guidance });

const stageExamples = (
  clear: string,
  vague: string,
  askAi: string,
): StarterExample[] => [
  { label: "我有明确想法", text: clear },
  { label: "我还没想清楚", text: vague },
  { label: "请 AI 帮我设计", text: askAi },
];

export const COCREATION_STAGES: CocreationStage[] = [
  {
    id: 0,
    name: "灵感与创作约束",
    summary: "确认想给读者什么体验，以及目前有哪些方向或限制。",
    goal: "弄清已有想法、核心读者体验、题材倾向，以及平台和商业模式带来的真实限制。",
    whyNow: "这些决定会约束后面的品类承诺、核心发动机和开篇节奏；现在只建立起点，不扩写整本书。",
    boundary: "不要提前写完整人物、世界观、金手指或大纲；实时市场问题必须搜索近期来源，无法联网时必须暴露限制。",
    completionCriteria: [
      "能说明想让读者持续获得什么体验，或明确记录暂时不确定。",
      "题材方向与目标体验不明显冲突；未确定时已有可比较方向。",
      "平台、商业模式和目标读者即使暂定，也已明确记录当前状态。",
      "只保留真正阻碍下一阶段的未决问题。",
    ],
    fields: [
      field("00_开书总表.md", "项目标识与暂定书名", "必填", "开写前暂定", "建立能稳定指代当前新书的项目名称；书名可暂定，并检查它是否承诺了正文不存在的看点。", ["项目标识", "暂定书名"]),
      field("00_开书总表.md", "创作意图与目标体验", "必填", "开写前锁定", "用通俗语言说明读者持续获得的主要情绪和满足。", ["创作意图", "目标体验", "读者体验"]),
      field("00_开书总表.md", "当前题材方向", "必填", "开写前暂定", "记录当前题材倾向；未确定时写清候选和分歧。", ["题材", "赛道方向"]),
      field("00_开书总表.md", "目标平台、商业模式与读者", "必填", "开写前暂定", "只记录会影响篇幅、开篇、节奏和内容边界的限制。", ["目标平台", "商业模式与读者"]),
      field("00_开书总表.md", "当前重大未决问题", "必填", "开写前暂定", "只列真正阻碍下一阶段的决定；没有时明确写无。", ["重大未决", "待确认问题"]),
    ],
    contextFiles: [],
    examples: stageExamples(
      "我想写一本修仙成长小说，希望读者主要获得一步步升级、最终登顶的成长爽感。",
      "我大概想写修仙，但还不知道传统升级、宗门经营和规则解谜哪一种更适合。",
      "我现在没有灵感。请先帮助我比较至少三个读者体验明显不同的方向；涉及当前市场时请搜索近期来源。",
    ),
    references: "Fable 8.6 + 用户确认的无灵感起点",
  },
  {
    id: 1,
    name: "品类、承诺与差异化",
    summary: "明确品类必须兑现什么、规避什么、这本书哪里不同。",
    goal: "确定本书遵守的品类契约、明确规避的雷点、一句话卖点和不会破坏核心预期的差异化。",
    whyNow: "核心发动机必须服务已经选定的读者承诺；如果品类契约和差异化互相冲突，后续设计越细返工越大。",
    boundary: "不把“设定新颖”当差异化，不用违反品类核心预期证明创新，也不提前设计完整金手指。",
    completionCriteria: [
      "品类必给预期具体到本书准备怎样兑现。",
      "雷点和允许例外边界清楚。",
      "一句话卖点能同时表达题材组合、核心冲突或情绪承诺。",
      "差异化与品类契约不互相抵消。",
    ],
    fields: [
      field("00_开书总表.md", "品类及必给预期", "必填", "开写前锁定", "列出本书承诺兑现的核心预期及准备兑现的位置。", ["品类必给", "品类契约"]),
      field("00_开书总表.md", "雷点与创作禁区", "必填", "开写前锁定", "记录明确规避内容、允许例外及例外条件。", ["雷点", "创作禁区"]),
      field("00_开书总表.md", "一句话卖点", "必填", "开写前锁定", "一句话说清题材组合、核心冲突或持续情绪承诺。", ["核心卖点", "卖点"]),
      field("00_开书总表.md", "差异化", "必填", "开写前锁定", "说明哪里遵守品类、哪里创新，以及创新的风险。", ["差异化方向"]),
      field("00_开书总表.md", "当前重大未决问题", "必填", "开写前暂定", "更新仍会阻碍核心发动机设计的问题。", ["重大未决", "待确认问题"]),
    ],
    contextFiles: ["00_开书总表.md"],
    examples: stageExamples(
      "我确定写传统升级修仙，必须有清楚的成长阶梯和越级翻盘，但不想写无代价开挂。",
      "我知道读者要升级爽感，但还没想好差异化应该放在能力机制、宗门经营还是人物关系。",
      "请根据我第 0 步的结果，给三个不破坏修仙核心预期的差异化方案，并比较风险。",
    ),
    references: "Fable 1.1～1.4",
  },
  {
    id: 2,
    name: "核心发动机",
    summary: "设计能够持续制造冲突、奖励和升级的机制。",
    goal: "把金手指或其他剧情发动机写成可持续运行的机制，明确条件、限制、代价、成长和爽点生产方式。",
    whyNow: "发动机决定主角为什么不断遇到新目标、阻碍和奖励，也决定世界规则与长线节奏是否会过早失控。",
    boundary: "不只写能力介绍，不用临时新增功能解死局；限制和代价必须能在真实剧情中咬人。",
    completionCriteria: [
      "能解释机制怎样反复制造目标、冲突、奖励和升级。",
      "使用条件、限制与代价能够实际约束主角。",
      "成长轨道足以支持长篇，但只细化当前需要的阶段。",
      "至少有两种可轮换的爽点或回报方式。",
    ],
    fields: [
      field("00_开书总表.md", "持续剧情发动机", "必填", "开写前锁定", "说明故事怎样持续产生目标、阻碍、奖励和升级。", ["核心发动机", "剧情发动机"]),
      field("02_世界与规则.md", "核心机制规则、限制与代价", "必填", "开写前锁定", "写清能力、条件、限制、代价及真实触发方式。", ["机制规则", "限制与代价"]),
      field("02_世界与规则.md", "成长轨道与防失控护栏", "必填", "开写前锁定", "记录阶段性成长、上限、节流阀和避免过早无敌的方法。", ["成长轨道", "防失控护栏"]),
      field("00_开书总表.md", "当前重大未决问题", "必填", "开写前暂定", "更新仍会阻碍主角或世界规则设计的问题。", ["重大未决", "待确认问题"]),
    ],
    contextFiles: ["00_开书总表.md"],
    examples: stageExamples(
      "我想让主角通过识破规则漏洞完成越级翻盘，但每次利用漏洞都会留下可追踪的代价。",
      "我有一个能力点子，但不知道它怎样持续产剧情，也不知道限制应该卡在哪里。",
      "请基于既有卖点给三个机制核心明显不同的发动机方案，并检查它们能否支撑长篇。",
    ),
    references: "Fable 1.5～1.6、2.10",
  },
  {
    id: 3,
    name: "主角最小完整集",
    summary: "锁定主角的目标、动机、底线、反差和压力选择。",
    goal: "形成足以稳定判断主角在新场景中会怎样选择、为什么这样选择的最小人物卡。",
    whyNow: "核心人物和第一卷剧情都要围绕主角的行动逻辑配置；人物不是百科资料，而是选择规则。",
    boundary: "不以身高、体重、星座或 MBTI 代替动机和行为；不为了完整编造无剧情用途的细节。",
    completionCriteria: [
      "当前外在目标具体到会采取行动。",
      "深层欲望能解释主角为什么长期坚持。",
      "至少一组反差或内在矛盾会制造选择困难。",
      "底线和压力反应足以区分主角与其他人物。",
      "第一卷任务和预期变化与发动机相容。",
    ],
    fields: [
      field("01_人物与关系.md", "主角身份与叙事功能", "必填", "开写前锁定", "只记录影响社会位置、行动和读者识别的身份信息。", ["主角身份", "叙事功能"]),
      field("01_人物与关系.md", "主角当前目标与深层欲望", "必填", "开写前锁定", "外在目标要能行动，深层欲望要能解释长期选择。", ["外在目标", "深层欲望"]),
      field("01_人物与关系.md", "主角行为底线与压力选择", "必填", "开写前锁定", "说明通常绝不做什么，以及危险、诱惑或背叛下优先保什么。", ["行为底线", "压力选择"]),
      field("01_人物与关系.md", "主角反差、矛盾与识别锚点", "必填", "开写前暂定", "用能产生剧情的矛盾和可反复展示的记忆点塑造主角。", ["性格反差", "识别锚点"]),
      field("01_人物与关系.md", "主角第一卷任务与变化", "必填", "开写前暂定", "记录第一卷承担的任务、起点状态和预期变化。", ["第一卷任务与变化"]),
    ],
    contextFiles: ["00_开书总表.md", "02_世界与规则.md"],
    examples: stageExamples(
      "主角想摆脱被宗门控制的命运，但他最不能接受的是为了自由牺牲无辜者。",
      "我知道主角要变强，却说不清他为什么非要变强，也不知道压力下会怎样选择。",
      "请根据卖点和发动机给三个行动逻辑明显不同的主角方案，用同一个危机场景比较他们的选择。",
    ),
    references: "Fable 2.2",
  },
  {
    id: 4,
    name: "核心人物班子",
    summary: "按叙事功能配齐开篇人物与第一梯队反派。",
    goal: "按冲突、信息、情感、资源和成长参照等功能配置开篇人物，建立有自己事业线的第一梯队反派。",
    whyNow: "第一卷骨架需要知道谁持续提供压力、信息和关系变化；角色数量应由功能决定，而不是先凑人数。",
    boundary: "不为每个角色写百科小传；轻量角色只写首次登场功能，功能重复时优先合并或重设。",
    completionCriteria: [
      "开篇核心人物都有不可替代的叙事功能。",
      "配角功能没有明显重叠或空缺。",
      "第一梯队反派有独立目标，不只是跟主角过不去。",
      "主要关系写清双方诉求、当前张力和预计变化。",
    ],
    fields: [
      field("01_人物与关系.md", "开篇核心人物阵容", "必填", "开写前暂定", "按人物、首次登场功能和第一卷戏份级别列出阵容。", ["核心人物阵容"]),
      field("01_人物与关系.md", "核心配角功能分工", "必填", "开写前暂定", "说明每个配角给冲突、信息、情感、资源或参照中的什么。", ["配角功能"]),
      field("01_人物与关系.md", "第一梯队反派与事业线", "必填", "开写前锁定", "记录反派自己的目标、资源、压力方式及与主角的结构性冲突。", ["第一梯队反派", "反派事业线"]),
      field("01_人物与关系.md", "关系与冲突表", "必填", "开写前暂定", "记录双方各自诉求、当前张力和预计变化。", ["人物关系", "关系冲突"]),
      field("01_人物与关系.md", "人物功能重叠与空缺检查", "必填", "开写前暂定", "明确重复功能、缺失功能及处理决定。", ["功能重叠", "功能空缺"]),
    ],
    contextFiles: ["00_开书总表.md", "01_人物与关系.md"],
    examples: stageExamples(
      "开篇需要一个给主角资源但要求服从的师父、一个竞争型同门和一个执行宗门规则的反派。",
      "我只知道需要两个配角和一个反派，但不知道他们各自应该承担什么功能。",
      "请按冲突、信息、情感、资源和成长参照检查功能缺口，给三个规模不同的开篇班子。",
    ),
    references: "Fable 2.1、2.3～2.4",
  },
  {
    id: 5,
    name: "世界最内圈与硬规则",
    summary: "只准备第一卷会用到的规则和少数长线锚点。",
    goal: "建立开篇活动范围、马上会用的硬规则、力量与势力关系，以及不能在后文推翻的少数长线锚点。",
    whyNow: "人物和发动机已经确定，才能判断第一卷真正依赖哪些世界信息；世界观的价值是支持行动和冲突，不是成为百科全书。",
    boundary: "现实常识不重复记录；第一卷暂时不用的外层世界允许留白，长线谜底只锁锚点和揭示顺序。",
    completionCriteria: [
      "第一卷活动范围和社会环境足以支持开篇行动。",
      "自定义硬规则、例外和代价不会互相冲突。",
      "力量、势力和资源只写到第一卷实际使用深度。",
      "长线锚点与信息揭示顺序清楚，外层世界保留生长空间。",
    ],
    fields: [
      field("02_世界与规则.md", "开篇活动范围", "必填", "开写前锁定", "只写第一卷实际进入或影响剧情的地点和社会环境。", ["活动范围"]),
      field("02_世界与规则.md", "世界硬规则与例外", "条件必填", "开写前锁定", "记录与常识不同、违反会造成矛盾的规则及例外。", ["硬规则", "规则与例外"]),
      field("02_世界与规则.md", "力量或能力体系", "条件必填", "开写前锁定", "有等级、战力、技能或资源竞争时启用。", ["力量体系", "能力体系"]),
      field("02_世界与规则.md", "势力与利益关系", "条件必填", "开写前暂定", "记录第一卷会参与资源、权力或立场争夺的组织。", ["势力关系", "利益关系"]),
      field("02_世界与规则.md", "核心资源与代价", "条件必填", "开写前锁定", "说明成长、能力或身份依赖什么稀缺资源及代价。", ["核心资源", "资源与代价"]),
      field("02_世界与规则.md", "长线悬念锚点", "条件必填", "开写前锁定", "只定后文不能推翻的锚点，不提前写完整答案。", ["长线锚点", "悬念锚点"]),
      field("02_世界与规则.md", "信息揭示顺序", "必填", "开写前暂定", "说明哪些开篇必须知道、哪些必须延迟。", ["揭示顺序"]),
      field("02_世界与规则.md", "锁定、暂定与留白清单", "必填", "开写前暂定", "区分已锁定、当前暂定、刻意留白和不适用。", ["锁定暂定留白", "留白清单"]),
    ],
    contextFiles: ["00_开书总表.md", "01_人物与关系.md", "02_世界与规则.md"],
    examples: stageExamples(
      "第一卷只发生在边境宗门与附近矿城，力量体系先写三个境界和获取资源的代价。",
      "我有很大的世界观想法，但不知道开篇究竟需要写到哪一层。",
      "请从人物、发动机和第一卷用途反推最小世界范围，给三个详略程度不同的方案。",
    ),
    references: "Fable 2.6～2.11",
  },
  {
    id: 6,
    name: "第一卷骨架",
    summary: "确定第一卷任务、对手、状态变化和不可逆节点。",
    goal: "锁定第一卷从什么状态走到什么状态、主角完成什么任务、面对谁、经过哪些不可逆节点。",
    whyNow: "人物和最内圈世界足够明确后，才能设计由角色选择和规则自然推动的卷级任务；章级仍保留生长空间。",
    boundary: "不强迫逐章写死，不提前扩写后续所有卷；关键节点只保留会改变人物、资源、关系或局势的不可逆变化。",
    completionCriteria: [
      "一句话说清第一卷核心任务。",
      "主要对手和关键资源与主角、发动机及世界规则相容。",
      "关键节点具有不可逆后果，不是一般事件罗列。",
      "卷末状态变化明确，能够自然带出下一卷。",
    ],
    fields: [
      field("03_剧情与开篇.md", "全书方向与终局锚点", "条件必填", "开写前暂定", "只写会约束长线的方向，不要求详细结局。", ["终局锚点", "全书方向"]),
      field("03_剧情与开篇.md", "第一卷任务", "必填", "开写前锁定", "一句话写清主角本卷必须完成什么。", ["卷级任务"]),
      field("03_剧情与开篇.md", "第一卷主要对手与关键资源", "必填", "开写前锁定", "记录主要压力来源和推动任务的稀缺资源。", ["主要对手", "关键资源"]),
      field("03_剧情与开篇.md", "第一卷关键节点", "必填", "开写前锁定", "只保留改变人物、资源、关系或局势的不可逆转折。", ["卷级关键节点", "不可逆节点"]),
      field("03_剧情与开篇.md", "第一卷卷末状态变化", "必填", "开写前锁定", "说明人物、关系、资源和局势从何种状态变成何种状态。", ["卷末状态"]),
      field("03_剧情与开篇.md", "主线与首批支线", "条件必填", "开写前暂定", "需要提前启动时才建立，并说明每条支线服务什么。", ["首批支线", "主线支线"]),
    ],
    contextFiles: ["00_开书总表.md", "01_人物与关系.md", "02_世界与规则.md"],
    examples: stageExamples(
      "第一卷让主角从被宗门控制的杂役，变成掌握规则漏洞并能独立选择去留的人。",
      "我知道开篇和卷末大概是什么，但中间哪些节点必须锁定还没想好。",
      "请给三个卷级任务结构，分别强调成长、悬疑和势力冲突，并说明不可逆节点。",
    ),
    references: "Fable 2.12、6.4",
  },
  {
    id: 7,
    name: "开篇施工",
    summary: "安排第一幕、前三章任务、首次兑现和章末钩。",
    goal: "把卖点、主角、发动机和第一卷任务压缩成可以开始第一章的开篇施工方案。",
    whyNow: "前六步已经给出稳定原料，现在才适合决定第一眼让读者看什么、先承诺什么、何时给第一笔回报。",
    boundary: "不倾泻设定，不用无关日常拖延主角进入麻烦；平台节奏参数没有真实证据时只记录待验证，不伪造标准答案。",
    completionCriteria: [
      "第一幕同时展示主角、立即麻烦和故事承诺。",
      "前三章各有清楚任务，信息装载不过量。",
      "首次情绪兑现能回报前文承诺。",
      "前三章章末钩都有明确指向，不能只写抽象悬念。",
      "商业卡点按实际平台和模式启用或明确暂定。",
    ],
    fields: [
      field("03_剧情与开篇.md", "开场第一幕", "必填", "开写前锁定", "记录场景、主角正在做什么和立即面对的麻烦。", ["开场", "第一幕"]),
      field("03_剧情与开篇.md", "前三章任务", "必填", "开写前锁定", "逐章记录信息、冲突、兑现和章末任务。", ["黄金三章", "前三章"]),
      field("03_剧情与开篇.md", "首次情绪兑现", "必填", "开写前锁定", "说明前面承诺什么、在哪里给出第一笔回报。", ["首个兑现", "首次兑现"]),
      field("03_剧情与开篇.md", "前三章章末钩", "必填", "开写前锁定", "记录每章提出的问题、钩子类型和预计回应距离。", ["章末钩"]),
      field("03_剧情与开篇.md", "信息交代与悬置", "必填", "开写前暂定", "区分必须让读者知道和应延迟的信息。", ["交代与悬置", "信息悬置"]),
      field("03_剧情与开篇.md", "商业卡点计划", "条件必填", "开写前暂定", "根据真实平台和付费/免费模式启用；未知时明确待验证。", ["商业卡点", "平台卡点"]),
    ],
    contextFiles: ["00_开书总表.md", "01_人物与关系.md", "02_世界与规则.md", "03_剧情与开篇.md"],
    examples: stageExamples(
      "开场让主角正在偿还一次能力代价时撞上新的规则异象，首章末必须迫使他再次使用能力。",
      "我有第一卷骨架，但不知道前三章应该先交代人物、能力还是世界规则。",
      "请给三个开局类型明显不同的前三章施工方案，并比较承诺兑现速度和信息负担。",
    ),
    references: "Fable 3.1～3.7",
  },
  {
    id: 8,
    name: "一致性与开写确认",
    summary: "检查锁定、暂定和留白是否完整且互不冲突。",
    goal: "逐项检查前四份开书材料中的必填、锁定、暂定、留白和跨文档冲突，判断是否达到开写门槛。",
    whyNow: "开篇施工完成后必须先消除会让正文立即崩坏的矛盾，再进入连载运行设计。",
    boundary: "本阶段不悄悄改写前序决定；发现冲突时明确指出应返回哪一步修订。未通过门槛时必须输出“阶段尚未结束”。",
    completionCriteria: [
      "所有必填且开写前锁定的字段已有确认结果。",
      "必填且开写前暂定的字段至少有可用版本。",
      "刻意留白都有原因和最迟补充时机。",
      "人物、世界、第一卷和前三章不存在未处理的硬冲突。",
      "明确给出已达到或尚未达到开写门槛。",
    ],
    fields: [
      field("00_开书总表.md", "开写状态总览", "必填", "开写前锁定", "明确已达到或尚未达到开写门槛，并说明依据。", ["开写状态", "开写门槛"]),
      field("00_开书总表.md", "锁定、暂定与留白总清单", "必填", "开写前锁定", "汇总仍需遵守、验证或延后补充的决定。", ["锁定暂定留白总清单"]),
      field("01_人物与关系.md", "人物一致性检查", "必填", "开写前锁定", "检查动机、行为、关系和第一卷任务是否一致。", ["人物一致性"]),
      field("02_世界与规则.md", "规则一致性检查", "必填", "开写前锁定", "检查发动机、力量、代价、势力和揭示顺序是否冲突。", ["规则一致性"]),
      field("03_剧情与开篇.md", "剧情与开篇一致性检查", "必填", "开写前锁定", "检查第一卷任务、关键节点、前三章和首次兑现是否一致。", ["剧情一致性", "开篇一致性"]),
    ],
    contextFiles: ["00_开书总表.md", "01_人物与关系.md", "02_世界与规则.md", "03_剧情与开篇.md"],
    examples: stageExamples(
      "请严格检查现有四份材料；如果达到开写门槛，列出仍需运行期观察的暂定项。",
      "我怀疑主角的底线和前三章第一次使用能力的行为有冲突，但还不知道该改哪里。",
      "请从卖点、人物、规则、第一卷和前三章五条链分别做一致性检查；发现硬冲突时告诉我返回哪一步。",
    ),
    references: "Fable 2.6、2.11、6.4",
  },
  {
    id: 9,
    name: "连载运行合同",
    summary: "建立事件循环、节奏护栏和持续更新的账本。",
    goal: "建立能够支持第一批连载单元的事件循环、爽点轮换、主支线与悬念账本，以及防止战力、规则和人物状态失控的更新纪律。",
    whyNow: "开写材料已经通过一致性检查，最后需要把日更时的生长空间装进可维护的运行容器。",
    boundary: "不把全书逐章写死；账本记录当前状态、下一次回勾和关闭条件，不保存无关推理过程。",
    completionCriteria: [
      "第一种事件单元能完整经历进入、升级、爆发、结算和带出下一单元。",
      "主线、支线、爽点和悬念都有可持续更新规则。",
      "战力、资源和机制代价有阶段上限或执行护栏。",
      "每个单元结束后知道必须更新哪些人物、设定、认知和关系状态。",
      "视角与信息纪律足以防止正文越权泄露。",
    ],
    fields: [
      field("04_连载规则与账本.md", "第一种事件单元循环", "必填", "开写前锁定", "说明进入、升级、爆发、结算和下一单元引子。", ["事件单元循环"]),
      field("04_连载规则与账本.md", "主线与支线账本", "必填", "开写前暂定", "记录当前状态、服务目标、下一次回勾和关闭条件。", ["主支线账本"]),
      field("04_连载规则与账本.md", "爽点与情绪轮换", "必填", "开写前暂定", "记录不同回报方式及轮换原则，避免长期单一重复。", ["爽点轮换", "情绪轮换"]),
      field("04_连载规则与账本.md", "章末钩与悬念账本", "必填", "开写前暂定", "登记问题、预计兑现范围、保温方式和是否逾期。", ["悬念账本", "章末钩账本"]),
      field("04_连载规则与账本.md", "战力或资源曲线护栏", "条件必填", "开写前锁定", "有成长、权力或资源曲线时记录阶段上限和节流阀。", ["战力护栏", "资源曲线护栏"]),
      field("04_连载规则与账本.md", "规则代价执行表", "条件必填", "开写前锁定", "登记机制代价何时真实发生，避免只停在设定里。", ["代价执行"]),
      field("04_连载规则与账本.md", "设定变更记录", "必填", "开写前暂定", "新设定怎样兼容旧内容，冲突时不得静默覆盖。", ["设定变更"]),
      field("04_连载规则与账本.md", "角色与状态更新", "必填", "开写前暂定", "每个单元后更新人物目标、关系、资源和认知变化。", ["角色状态更新", "人物状态更新"]),
      field("04_连载规则与账本.md", "视角与信息纪律", "必填", "开写前锁定", "记录叙述视角、谁知道什么和哪些信息不得越权泄露。", ["信息纪律", "视角纪律"]),
      field("04_连载规则与账本.md", "简版文风约束", "条件必填", "可延后", "只保存会影响正文一致性的叙述距离、句式倾向和禁用表达。", ["文风约束"]),
    ],
    contextFiles: ["00_开书总表.md", "01_人物与关系.md", "02_世界与规则.md", "03_剧情与开篇.md", "04_连载规则与账本.md"],
    examples: stageExamples(
      "我希望每个事件单元都从规则异常开始，以主角识破规则并付出代价结算，同时推进一条长线谜团。",
      "我有第一卷和前三章，但不知道日更时怎样控制支线、爽点和悬念不失控。",
      "请设计三个长度和节奏明显不同的事件循环，并给出对应账本更新规则。",
    ),
    references: "Fable 4.1～4.17、5.1～5.6",
  },
];

const RESULT_HEADER = "【阶段结束：已形成结果】";
const NO_RESULT_HEADER = "【阶段结束：无正式结果】";
const INCOMPLETE_HEADER = "【阶段尚未结束】";
const NECESSITY_VALUES = new Set(["必填", "条件必填", "选填"]);
const LOCK_TIMING_VALUES = new Set(["开写前锁定", "开写前暂定", "可延后"]);
const CONFIRMATION_VALUES = new Set(["已确认", "暂定", "刻意留白", "不适用"]);

function normalizeText(value: string) {
  return value.replace(/\r\n?/g, "\n").trim();
}

function cleanLineValue(value: string) {
  return value.trim().replace(/^`|`$/g, "").trim();
}

function lineValue(text: string, label: string) {
  const lines = normalizeText(text).split("\n");
  const prefix = new RegExp(`^${label}[：:]\\s*(.*)$`);
  for (const line of lines) {
    const match = line.trim().match(prefix);
    if (match) return cleanLineValue(match[1]);
  }
  return "";
}

function sectionValue(text: string, label: string, followingLabels: string[]) {
  const lines = normalizeText(text).split("\n");
  const startPattern = new RegExp(`^${label}[：:]\\s*(.*)$`);
  const followingPatterns = followingLabels.map((item) => new RegExp(`^${item}[：:]`));
  let collecting = false;
  const collected: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!collecting) {
      const match = trimmed.match(startPattern);
      if (!match) continue;
      collecting = true;
      if (match[1].trim()) collected.push(match[1].trim());
      continue;
    }
    if (followingPatterns.some((pattern) => pattern.test(trimmed))) break;
    collected.push(line);
  }
  return collected.join("\n").trim();
}

function withoutBullet(value: string) {
  return value.replace(/^\s*[-*]\s*/, "").trim();
}

function hasMeaningfulText(value: string) {
  const normalized = withoutBullet(value);
  return Boolean(normalized && !/^[-—]$/.test(normalized));
}

function parseWritebacks(section: string) {
  const items: WritebackItem[] = [];
  let current: WritebackItem | null = null;
  let activeKey: keyof WritebackItem | null = null;
  const labelKeys: Record<string, keyof WritebackItem> = {
    文件: "file",
    字段: "field",
    内容: "content",
    必要程度: "necessity",
    锁定时机: "lockTiming",
    确认状态: "confirmation",
  };

  for (const line of normalizeText(section).split("\n")) {
    const match = line.match(/^\s*-\s*(文件|字段|内容|必要程度|锁定时机|确认状态)[：:]\s*(.*)$/);
    if (match) {
      const key = labelKeys[match[1]];
      if (key === "file") {
        if (current) items.push(current);
        current = { file: "", field: "", content: "", necessity: "", lockTiming: "", confirmation: "" };
      }
      if (!current) continue;
      current[key] = cleanLineValue(match[2]);
      activeKey = key;
      continue;
    }
    if (current && activeKey && line.trim()) {
      current[activeKey] = [current[activeKey], withoutBullet(line)].filter(Boolean).join("\n");
    }
  }
  if (current) items.push(current);
  return items;
}

function parseStageNumber(value: string) {
  const normalized = value.replace(/[０-９]/g, (digit) =>
    String.fromCharCode(digit.charCodeAt(0) - 0xfee0),
  );
  const match = normalized.match(/\d+/);
  return match ? Number(match[0]) : null;
}

function baseFilename(value: string) {
  return cleanLineValue(value).replace(/^.*[\\/]/, "");
}

function matchingField(stage: CocreationStage, value: string) {
  const normalized = cleanLineValue(value);
  // 标准字段名优先，否则“前三章章末钩”会被较短别名“前三章”抢先识别成另一字段。
  const canonical = stage.fields.find((item) => item.name === normalized);
  if (canonical) return canonical;
  const exactAlias = stage.fields.find((item) => item.aliases.includes(normalized));
  if (exactAlias) return exactAlias;

  const fuzzyMatches = stage.fields
    .flatMap((item) => item.aliases
      .filter((alias) => normalized.includes(alias))
      .map((alias) => ({ item, aliasLength: alias.length })))
    .sort((left, right) => right.aliasLength - left.aliasLength);
  if (!fuzzyMatches.length) return undefined;
  const bestLength = fuzzyMatches[0].aliasLength;
  const bestItems = [...new Set(
    fuzzyMatches
      .filter((match) => match.aliasLength === bestLength)
      .map((match) => match.item),
  )];
  return bestItems.length === 1 ? bestItems[0] : undefined;
}

export function parseStageResult(rawValue: string, stage: CocreationStage): ParsedStageResult {
  const text = normalizeText(rawValue);
  const errors: string[] = [];
  const warnings: string[] = [];
  const headers = [RESULT_HEADER, NO_RESULT_HEADER, INCOMPLETE_HEADER].filter((header) => text.includes(header));
  const status: StageResultStatus = headers.length !== 1
    ? "UNKNOWN"
    : headers[0] === RESULT_HEADER
      ? "RESULT"
      : headers[0] === NO_RESULT_HEADER
        ? "NO_RESULT"
        : "INCOMPLETE";
  const stageNumber = lineValue(text, "阶段编号");
  const stageName = lineValue(text, "阶段名称");
  const confirmed = sectionValue(text, "用户已确认", ["写回开书包", "仍然暂定或刻意留白", "与已有内容的冲突检查", "对下一阶段的影响"]);
  const writebackSection = sectionValue(text, "写回开书包", ["仍然暂定或刻意留白", "与已有内容的冲突检查", "对下一阶段的影响"]);
  const writebacks = parseWritebacks(writebackSection);
  const unresolved = sectionValue(text, "仍然暂定或刻意留白", ["与已有内容的冲突检查", "对下一阶段的影响"]);
  const conflictCheck = sectionValue(text, "与已有内容的冲突检查", ["对下一阶段的影响"]);
  const nextImpact = sectionValue(text, "对下一阶段的影响", []);
  const noResultReason = lineValue(text, "结束原因");
  const reopenWhen = sectionValue(text, "最迟应在什么时候重新处理", ["对下一阶段的影响"]);
  const missingDecisions = sectionValue(text, "仍缺少的关键决定", ["为什么现在不能结束", "下一轮只讨论"]);
  const cannotEndReason = sectionValue(text, "为什么现在不能结束", ["下一轮只讨论"]);
  const nextDiscussion = sectionValue(text, "下一轮只讨论", []);

  if (!text) {
    return {
      status, stageNumber, stageName, errors, warnings, confirmed, writebacks, unresolved,
      conflictCheck, nextImpact, noResultReason, reopenWhen, missingDecisions,
      cannotEndReason, nextDiscussion, canConfirm: false, blocksNext: false,
    };
  }

  if (headers.length !== 1) {
    errors.push(headers.length ? "结束块同时出现了多个阶段标题，只能保留一种结果状态。" : "没有找到规定的阶段标题，请让网页 AI 按固定结束格式重新输出。");
  } else if (status === "INCOMPLETE") {
    if (!hasMeaningfulText(missingDecisions)) warnings.push("网页 AI 没有列出仍缺少的关键决定。");
    if (!hasMeaningfulText(cannotEndReason)) warnings.push("网页 AI 没有说明为什么本阶段还不能结束。");
    if (!hasMeaningfulText(nextDiscussion)) warnings.push("网页 AI 没有给出下一轮只讨论什么。");
  } else {
    const parsedNumber = parseStageNumber(stageNumber);
    if (parsedNumber === null) errors.push("缺少“阶段编号”。");
    else if (parsedNumber !== stage.id) errors.push(`阶段编号是“${stageNumber}”，当前页面只接收第 ${stage.id} 步。`);
    if (!stageName) errors.push("缺少“阶段名称”。");
    else if (!stageName.includes(stage.name)) errors.push(`阶段名称是“${stageName}”，当前步骤应为“${stage.name}”。`);
  }

  if (status === "NO_RESULT") {
    if (!noResultReason) errors.push("无正式结果时必须填写“结束原因”。");
    if (!hasMeaningfulText(reopenWhen)) errors.push("无正式结果时必须说明最迟何时重新处理。");
    if (!hasMeaningfulText(nextImpact)) errors.push("无正式结果时必须说明对下一阶段的影响。");
  }

  if (status === "RESULT") {
    if (!hasMeaningfulText(confirmed)) errors.push("缺少用户已经确认的内容。");
    if (!writebacks.length) errors.push("没有识别到任何“写回开书包”的字段。");
    const foundContracts = new Map<string, number>();

    writebacks.forEach((item, index) => {
      const number = index + 1;
      const contract = matchingField(stage, item.field);
      const submittedField = item.field;
      const submittedFile = item.file;
      if (!item.file) errors.push(`第 ${number} 个写回项缺少文件名。`);
      if (!item.field) errors.push(`第 ${number} 个写回项缺少字段名。`);
      else if (!contract) errors.push(`“${item.field}”不是第 ${stage.id} 步允许写入的标准字段。`);
      if (contract && baseFilename(item.file) !== contract.file) {
        errors.push(`“${submittedField}”应写入 ${contract.file}，不能写入“${submittedFile || "空文件名"}”。`);
      }
      if (contract) {
        foundContracts.set(contract.name, (foundContracts.get(contract.name) ?? 0) + 1);
        // 网页 AI 可以使用允许的别名，但内部文档必须始终使用同一套标准字段名和文件名。
        item.field = contract.name;
        item.file = contract.file;
      }
      if (!item.content) errors.push(`第 ${number} 个写回项缺少具体内容。`);
      if (!NECESSITY_VALUES.has(item.necessity)) errors.push(`第 ${number} 个写回项的必要程度不在规定选项中。`);
      if (!LOCK_TIMING_VALUES.has(item.lockTiming)) errors.push(`第 ${number} 个写回项的锁定时机不在规定选项中。`);
      if (!CONFIRMATION_VALUES.has(item.confirmation)) errors.push(`第 ${number} 个写回项的确认状态不在规定选项中。`);
      if (contract?.necessity === "必填" && item.confirmation === "不适用") {
        errors.push(`必填字段“${contract.name}”不能标记为不适用；无法形成结果时应使用“无正式结果”结束。`);
      }
    });

    for (const contract of stage.fields) {
      const count = foundContracts.get(contract.name) ?? 0;
      if (!count) errors.push(`第 ${stage.id} 步还缺少标准字段“${contract.name}”；可以暂定、刻意留白或不适用，但必须明确记录。`);
      if (count > 1) errors.push(`标准字段“${contract.name}”重复出现了 ${count} 次，请合并为一个当前结果。`);
    }
    if (!hasMeaningfulText(conflictCheck)) errors.push("缺少与已有内容的冲突检查。");
    if (!hasMeaningfulText(nextImpact)) errors.push("缺少对下一阶段的影响说明。");
  }

  const blocksNext = status === "NO_RESULT"
    && !/不影响|无需重开/.test(`${nextImpact}\n${reopenWhen}`);
  return {
    status, stageNumber, stageName, errors, warnings, confirmed, writebacks, unresolved,
    conflictCheck, nextImpact, noResultReason, reopenWhen, missingDecisions,
    cannotEndReason, nextDiscussion,
    canConfirm: (status === "RESULT" || status === "NO_RESULT") && errors.length === 0,
    blocksNext,
  };
}

export function createEmptyWorkspace(): CocreationWorkspace {
  return {
    version: 2,
    activeStageId: 0,
    stages: {},
    drafts: {},
    updatedAt: new Date().toISOString(),
  };
}

export function migrateStageZeroV1(value: SavedStageZeroV1): CocreationWorkspace {
  const workspace = createEmptyWorkspace();
  const stage = COCREATION_STAGES[0];
  const parsed = parseStageResult(value.resultBlock, stage);
  workspace.drafts["0"] = { userInput: value.userIdea ?? "", resultBlock: value.resultBlock ?? "" };
  if (parsed.canConfirm) {
    workspace.stages["0"] = {
      stageId: 0,
      userInput: value.userIdea ?? "",
      resultBlock: value.resultBlock ?? "",
      confirmedAt: value.confirmedAt ?? new Date().toISOString(),
      parsed,
      needsReview: false,
    };
    workspace.activeStageId = parsed.blocksNext ? 0 : 1;
  }
  workspace.updatedAt = new Date().toISOString();
  return workspace;
}

export function stageAllowsProgress(record: StageRecord | undefined) {
  if (!record || record.needsReview) return false;
  return record.parsed.status === "RESULT"
    || (record.parsed.status === "NO_RESULT" && !record.parsed.blocksNext);
}

export function canOpenStage(workspace: CocreationWorkspace, stageId: number) {
  if (stageId === 0 || workspace.stages[String(stageId)]) return true;
  return COCREATION_STAGES.slice(0, stageId).every((stage) =>
    stageAllowsProgress(workspace.stages[String(stage.id)]),
  );
}

export function collectLatestFields(
  workspace: CocreationWorkspace,
  beforeStageId = Number.POSITIVE_INFINITY,
  includeNeedsReview = true,
) {
  const fields = new Map<string, DocumentField>();
  Object.values(workspace.stages)
    .filter((record) => record.stageId < beforeStageId && record.parsed.status === "RESULT")
    .filter((record) => includeNeedsReview || !record.needsReview)
    .sort((left, right) => left.stageId - right.stageId)
    .forEach((record) => {
      const stage = COCREATION_STAGES[record.stageId];
      record.parsed.writebacks.forEach((item) => {
        fields.set(`${baseFilename(item.file)}::${item.field}`, {
          ...item,
          file: baseFilename(item.file),
          stageId: record.stageId,
          stageName: stage.name,
          confirmedAt: record.confirmedAt,
          needsReview: record.needsReview,
        });
      });
    });
  return [...fields.values()];
}

export function buildRelevantContext(workspace: CocreationWorkspace, stage: CocreationStage) {
  const fields = collectLatestFields(workspace, stage.id, false)
    .filter((item) => stage.contextFiles.includes(item.file as OpeningPackageFile));
  const noResults = Object.values(workspace.stages)
    .filter((record) => record.stageId < stage.id && !record.needsReview && record.parsed.status === "NO_RESULT")
    .sort((left, right) => left.stageId - right.stageId);
  if (!fields.length && !noResults.length) return "目前没有与本阶段相关的已确认内容。";
  const fieldText = fields.map((item) =>
    `- ${item.file}｜${item.field}｜${item.confirmation}：${item.content}`,
  );
  const noResultText = noResults.map((record) =>
    `- 第 ${record.stageId} 步无正式结果：${record.parsed.noResultReason}；影响：${withoutBullet(record.parsed.nextImpact)}`,
  );
  return [...fieldText, ...noResultText].join("\n");
}

export function buildStagePrompt(
  stage: CocreationStage,
  userInput: string,
  workspace: CocreationWorkspace,
) {
  const input = userInput.trim() || "我暂时没有写下具体想法，请主动帮助我找到可以比较的方案。";
  const fields = stage.fields.map((item) =>
    `- 文件：${item.file}\n  - 字段：${item.name}\n  - 内容：\n  - 必要程度：${item.necessity}\n  - 锁定时机：${item.lockTiming}\n  - 确认状态：`,
  ).join("\n");
  const fieldGuidance = stage.fields.map((item) =>
    `- ${item.file}｜${item.name}（${item.necessity}，默认${item.lockTiming}）：${item.guidance}`,
  ).join("\n");
  const criteria = stage.completionCriteria.map((item) => `- ${item}`).join("\n");
  const context = buildRelevantContext(workspace, stage);

  return `你是我的新书共创引导员。我们现在只完成“第 ${stage.id} 步：${stage.name}”。

本阶段目标：
${stage.goal}

为什么现在做：
${stage.whyNow}

本阶段边界：
${stage.boundary}

与本阶段有关的既有确认内容：
【既有内容开始】
${context}
【既有内容结束】

我的当前想法或要求：
【用户输入开始】
${input}
【用户输入结束】

你的工作规则：
1. 先复述你的理解，区分“已明确、只是倾向、还没想好”，让我纠正；
2. 每轮最多主动问三个最重要的问题，不要一次给我长问卷；
3. 优先讨论会改变后续路线的问题，不追问无剧情用途的百科细节；
4. 如果我已经想清楚，检查矛盾和缺口，不要强行改成你的故事；
5. 如果我没想好并需要你设计，至少给三个核心机制或实际影响明显不同的方案；逐个说明主要体验、优势、风险以及对后续人物、世界和剧情的影响；
6. 我可以选择一个、混合多个或全部否定，不要默认推荐方案已经被接受；
7. 涉及“目前流行什么”等实时问题时，有联网能力就搜索近期公开资料并标注时间和来源；没有联网能力就明确暴露限制；
8. 不得照搬参考作品的专有设定或表达，不得把暂定内容写成已确认；
9. 信息足够后先形成草案并让我确认；未经我明确确认，不得宣布阶段完成；
10. 只写本阶段规定字段。发现前序硬冲突时说明应返回哪一步，不要静默覆盖。

本阶段字段合同：
${fieldGuidance}

完成标准：
${criteria}
- 所有 AI 补充内容已经过我确认；
- 没有扩写与本阶段无关的大量细节。

结束时只能使用下面三种格式之一。除结束块外，不要在后面追加解释。

【阶段结束：已形成结果】

阶段编号：${stage.id}
阶段名称：${stage.name}

用户已确认：
- 

写回开书包：
${fields}

仍然暂定或刻意留白：
- 无 / 具体内容、原因与最迟补充时机

与已有内容的冲突检查：
- 无 / 具体冲突、涉及字段和应返回的步骤

对下一阶段的影响：
- 

【阶段结束：无正式结果】

阶段编号：${stage.id}
阶段名称：${stage.name}
结束原因：不适用 / 用户决定延后 / 缺少可靠数据 / 其他

本阶段已经确认的“无结果决定”：
- 

最迟应在什么时候重新处理：
- 无需重开 / 进入某阶段前 / 获得某项数据后

对下一阶段的影响：
- 如果可以继续，必须明确写“不影响”；否则写清阻断什么

【阶段尚未结束】

已经确认：
- 

仍缺少的关键决定：
- 

为什么现在不能结束：
- 

下一轮只讨论：
- `;
}

function escapeTableCell(value: string) {
  return value.replace(/\n+/g, "<br>").replace(/\|/g, "\\|").trim();
}

export function buildOpeningPackageDocuments(workspace: CocreationWorkspace): OpeningPackageDocument[] {
  const allFields = collectLatestFields(workspace);
  const allRecords = Object.values(workspace.stages).sort((left, right) => left.stageId - right.stageId);
  return PACKAGE_FILES.map((filename) => {
    const fields = allFields.filter((item) => item.file === filename);
    const rows = fields.length
      ? fields.map((item) =>
        `| ${escapeTableCell(item.field)} | ${escapeTableCell(item.content)} | ${escapeTableCell(item.necessity)} | ${escapeTableCell(item.lockTiming)} | ${escapeTableCell(item.confirmation)}${item.needsReview ? "（需复核）" : ""} | 第 ${item.stageId} 步 |`,
      ).join("\n")
      : "| 尚未写入 | — | — | — | — | — |";
    const relatedNoResults = allRecords.filter((record) =>
      record.parsed.status === "NO_RESULT"
      && COCREATION_STAGES[record.stageId].fields.some((item) => item.file === filename),
    );
    const needsReview = fields.some((item) => item.needsReview)
      || relatedNoResults.some((record) => record.needsReview);
    const noResultSection = relatedNoResults.length
      ? `\n## 无正式结果记录\n\n${relatedNoResults.map((record) =>
        `- 第 ${record.stageId} 步“${COCREATION_STAGES[record.stageId].name}”${record.needsReview ? "（需复核）" : ""}：${escapeTableCell(record.parsed.noResultReason)}；最迟处理：${escapeTableCell(record.parsed.reopenWhen)}；影响：${escapeTableCell(record.parsed.nextImpact)}`,
      ).join("\n")}\n`
      : "";
    const reviewNote = needsReview
      ? "> 警告：前序决定修改后，本文件仍包含需要重新确认的后续阶段结果；完成复核前不能作为当前权威版本。\n\n"
      : "";
    const markdown = `# ${DOCUMENT_TITLES[filename]}

${reviewNote}> 这是用户与 AI 共创并由用户确认的通用开书材料，不是参考书拆解结果，也不是任何写作 Agent 的专用输入。

| 字段 | 当前结果 | 必要程度 | 锁定时机 | 确认状态 | 最近来源 |
|---|---|---|---|---|---|
${rows}
${noResultSection}
`;
    return {
      filename,
      title: DOCUMENT_TITLES[filename],
      fieldCount: fields.length,
      noResultCount: relatedNoResults.length,
      needsReview,
      markdown,
    };
  });
}

export function nextStageNeedingWork(workspace: CocreationWorkspace, afterStageId: number) {
  for (const stage of COCREATION_STAGES.slice(afterStageId + 1)) {
    const record = workspace.stages[String(stage.id)];
    if (!stageAllowsProgress(record)) return stage.id;
  }
  return Math.min(afterStageId + 1, COCREATION_STAGES.length - 1);
}

export function isPackageInternallyComplete(workspace: CocreationWorkspace) {
  return COCREATION_STAGES.every((stage) =>
    stageAllowsProgress(workspace.stages[String(stage.id)]),
  );
}
