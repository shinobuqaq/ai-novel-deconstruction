export type CocreationStage = {
  id: number;
  name: string;
  summary: string;
};

export type StageResultStatus = "RESULT" | "NO_RESULT" | "INCOMPLETE" | "UNKNOWN";

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
};

export type SavedStageZero = {
  version: 1;
  userIdea: string;
  resultBlock: string;
  confirmedAt: string;
  parsed: ParsedStageResult;
  documentMarkdown: string;
};

export const COCREATION_STORAGE_KEY = "ai-novel-deconstruction:new-book-cocreation:v1";

export const COCREATION_STAGES: CocreationStage[] = [
  { id: 0, name: "灵感与创作约束", summary: "确认想给读者什么体验，以及目前有哪些方向或限制。" },
  { id: 1, name: "品类、承诺与差异化", summary: "明确品类必须兑现什么、规避什么、这本书哪里不同。" },
  { id: 2, name: "核心发动机", summary: "设计能够持续制造冲突、奖励和升级的机制。" },
  { id: 3, name: "主角最小完整集", summary: "锁定主角的目标、动机、底线、反差和压力选择。" },
  { id: 4, name: "核心人物班子", summary: "按叙事功能配齐开篇人物与第一梯队反派。" },
  { id: 5, name: "世界最内圈与硬规则", summary: "只准备第一卷会用到的规则和少数长线锚点。" },
  { id: 6, name: "第一卷骨架", summary: "确定第一卷任务、对手、状态变化和不可逆节点。" },
  { id: 7, name: "开篇施工", summary: "安排第一幕、前三章任务、首次兑现和章末钩。" },
  { id: 8, name: "一致性与开写确认", summary: "检查锁定、暂定和留白是否完整且互不冲突。" },
  { id: 9, name: "连载运行合同", summary: "建立事件循环、节奏护栏和持续更新的账本。" },
];

export const STAGE_ZERO_EXAMPLES = [
  {
    label: "我有明确想法",
    text: "我想写一本修仙小说，主角从底层一步步修炼成长到世界之巅。我希望重点是传统升级带来的成长爽感。",
  },
  {
    label: "我只有模糊方向",
    text: "我大概想写修仙，但还不知道应该做传统升级、宗门经营，还是加入规则解谜。",
  },
  {
    label: "我暂时没有灵感",
    text: "我现在没有灵感。请先帮我看看目前哪些题材或读者体验值得比较；如果你不能联网，请明确说明，不要把旧资料当成当前趋势。",
  },
];

const RESULT_HEADER = "【阶段结束：已形成结果】";
const NO_RESULT_HEADER = "【阶段结束：无正式结果】";
const INCOMPLETE_HEADER = "【阶段尚未结束】";
const STAGE_ZERO_FILE = "00_开书总表.md";

const NECESSITY_VALUES = new Set(["必填", "条件必填", "选填"]);
const LOCK_TIMING_VALUES = new Set(["开写前锁定", "开写前暂定", "可延后"]);
const CONFIRMATION_VALUES = new Set(["已确认", "暂定", "刻意留白", "不适用"]);

const REQUIRED_STAGE_ZERO_FIELDS = [
  { label: "创作意图与目标体验", aliases: ["创作意图", "目标体验", "读者体验"] },
  { label: "当前题材方向", aliases: ["题材", "赛道", "方向"] },
  { label: "目标平台、商业模式与读者", aliases: ["平台", "商业模式", "目标读者", "读者"] },
  { label: "当前重大未决问题", aliases: ["未决", "待确认", "待决定", "尚未决定"] },
];

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
  const followingPatterns = followingLabels.map(
    (item) => new RegExp(`^${item}[：:]`),
  );
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
        current = {
          file: "",
          field: "",
          content: "",
          necessity: "",
          lockTiming: "",
          confirmation: "",
        };
      }
      if (!current) continue;
      current[key] = cleanLineValue(match[2]);
      activeKey = key;
      continue;
    }

    if (current && activeKey && line.trim()) {
      const continuation = withoutBullet(line);
      current[activeKey] = [current[activeKey], continuation].filter(Boolean).join("\n");
    }
  }

  if (current) items.push(current);
  return items;
}

function isStageZero(value: string) {
  return /(^|\D)[0０](\D|$)/.test(value);
}

function matchesStageZeroFile(value: string) {
  return cleanLineValue(value).replace(/^.*[\\/]/, "") === STAGE_ZERO_FILE;
}

function hasMeaningfulText(value: string) {
  const normalized = withoutBullet(value);
  return Boolean(normalized && !/^[-—]$/.test(normalized));
}

function validateStageIdentity(
  stageNumber: string,
  stageName: string,
  errors: string[],
) {
  if (!stageNumber) errors.push("缺少“阶段编号”。");
  else if (!isStageZero(stageNumber)) errors.push(`阶段编号是“${stageNumber}”，当前页面只接收第 0 步。`);
  if (!stageName) errors.push("缺少“阶段名称”。");
}

export function parseStageZeroResult(rawValue: string): ParsedStageResult {
  const text = normalizeText(rawValue);
  const errors: string[] = [];
  const warnings: string[] = [];
  let status: StageResultStatus = "UNKNOWN";

  if (text.includes(RESULT_HEADER)) status = "RESULT";
  else if (text.includes(NO_RESULT_HEADER)) status = "NO_RESULT";
  else if (text.includes(INCOMPLETE_HEADER)) status = "INCOMPLETE";

  const stageNumber = lineValue(text, "阶段编号");
  const stageName = lineValue(text, "阶段名称");
  const confirmed = sectionValue(text, "用户已确认", [
    "写回开书包",
    "仍然暂定或刻意留白",
    "与已有内容的冲突检查",
    "对下一阶段的影响",
  ]);
  const writebackSection = sectionValue(text, "写回开书包", [
    "仍然暂定或刻意留白",
    "与已有内容的冲突检查",
    "对下一阶段的影响",
  ]);
  const writebacks = parseWritebacks(writebackSection);
  const unresolved = sectionValue(text, "仍然暂定或刻意留白", [
    "与已有内容的冲突检查",
    "对下一阶段的影响",
  ]);
  const conflictCheck = sectionValue(text, "与已有内容的冲突检查", [
    "对下一阶段的影响",
  ]);
  const nextImpact = sectionValue(text, "对下一阶段的影响", []);
  const noResultReason = lineValue(text, "结束原因");
  const reopenWhen = sectionValue(text, "最迟应在什么时候重新处理", [
    "对下一阶段的影响",
  ]);
  const missingDecisions = sectionValue(text, "仍缺少的关键决定", [
    "为什么现在不能结束",
    "下一轮只讨论",
  ]);
  const cannotEndReason = sectionValue(text, "为什么现在不能结束", [
    "下一轮只讨论",
  ]);
  const nextDiscussion = sectionValue(text, "下一轮只讨论", []);

  if (!text) {
    return {
      status,
      stageNumber,
      stageName,
      errors,
      warnings,
      confirmed,
      writebacks,
      unresolved,
      conflictCheck,
      nextImpact,
      noResultReason,
      reopenWhen,
      missingDecisions,
      cannotEndReason,
      nextDiscussion,
      canConfirm: false,
    };
  }

  if (status === "UNKNOWN") {
    errors.push("没有找到三种规定的阶段标题，请让网页 AI 按固定结束格式重新输出。");
  } else if (status === "INCOMPLETE") {
    if (!hasMeaningfulText(missingDecisions)) warnings.push("网页 AI 没有列出仍缺少的关键决定。");
    if (!hasMeaningfulText(cannotEndReason)) warnings.push("网页 AI 没有说明为什么本阶段还不能结束。");
    if (!hasMeaningfulText(nextDiscussion)) warnings.push("网页 AI 没有给出下一轮只讨论什么。");
  } else if (status === "NO_RESULT") {
    validateStageIdentity(stageNumber, stageName, errors);
    if (!noResultReason) errors.push("无正式结果时必须填写“结束原因”。");
    if (!hasMeaningfulText(reopenWhen)) errors.push("无正式结果时必须说明最迟何时重新处理。");
    if (!hasMeaningfulText(nextImpact)) errors.push("无正式结果时必须说明对下一阶段的影响。");
  } else if (status === "RESULT") {
    validateStageIdentity(stageNumber, stageName, errors);
    if (!hasMeaningfulText(confirmed)) errors.push("缺少用户已经确认的内容。");
    if (!writebacks.length) errors.push("没有识别到任何“写回开书包”的字段。");

    writebacks.forEach((item, index) => {
      const number = index + 1;
      if (!item.file) errors.push(`第 ${number} 个写回项缺少文件名。`);
      else if (!matchesStageZeroFile(item.file)) {
        errors.push(`第 ${number} 个写回项要写入“${item.file}”，超出了第 0 步允许更新的 ${STAGE_ZERO_FILE}。`);
      }
      if (!item.field) errors.push(`第 ${number} 个写回项缺少字段名。`);
      if (!item.content) errors.push(`第 ${number} 个写回项缺少具体内容。`);
      if (!NECESSITY_VALUES.has(item.necessity)) {
        errors.push(`第 ${number} 个写回项的必要程度应为“必填 / 条件必填 / 选填”之一。`);
      }
      if (!LOCK_TIMING_VALUES.has(item.lockTiming)) {
        errors.push(`第 ${number} 个写回项的锁定时机应为“开写前锁定 / 开写前暂定 / 可延后”之一。`);
      }
      if (!CONFIRMATION_VALUES.has(item.confirmation)) {
        errors.push(`第 ${number} 个写回项的确认状态应为“已确认 / 暂定 / 刻意留白 / 不适用”之一。`);
      }
    });

    for (const requiredField of REQUIRED_STAGE_ZERO_FIELDS) {
      const found = writebacks.some((item) =>
        requiredField.aliases.some((alias) => item.field.includes(alias)),
      );
      if (!found) errors.push(`第 0 步还缺少标准字段“${requiredField.label}”。内容可以暂定或留白，但必须明确记录当前状态。`);
    }

    if (!hasMeaningfulText(conflictCheck)) errors.push("缺少与已有内容的冲突检查。");
    if (!hasMeaningfulText(nextImpact)) errors.push("缺少对下一阶段的影响说明。");
  }

  return {
    status,
    stageNumber,
    stageName,
    errors,
    warnings,
    confirmed,
    writebacks,
    unresolved,
    conflictCheck,
    nextImpact,
    noResultReason,
    reopenWhen,
    missingDecisions,
    cannotEndReason,
    nextDiscussion,
    canConfirm: (status === "RESULT" || status === "NO_RESULT") && errors.length === 0,
  };
}

export function buildStageZeroPrompt(userIdea: string) {
  const idea = userIdea.trim() || "我暂时没有写下具体想法，请先帮助我找到可以比较的起点。";
  return `你是我的新书共创引导员。我们现在只完成“第 0 步：灵感与创作约束”，不要提前替我写完整人物、世界观、金手指或大纲。

本阶段目标：
1. 弄清我目前已经有什么想法；
2. 判断我想让读者持续获得什么主要体验；
3. 记录当前题材方向、目标平台/商业模式/读者，以及真正阻碍下一步的未决问题；
4. 如果我没有明确方向，帮助我比较方案；
5. 形成一个可以进入下一阶段的起点，或者明确记录本阶段暂时不产出正式结果。

我的当前想法：
【用户输入开始】
${idea}
【用户输入结束】

你的工作规则：
1. 先用一小段话复述你的理解，区分“已明确、只是倾向、还没想好”，让我纠正；
2. 每轮最多主动问三个最重要的问题，不要一次给我长问卷；
3. 优先讨论会改变后续路线的问题，不追问人物姓名、身高、地名等低影响细节；
4. 如果我已经想清楚，帮助我检查矛盾和缺口，不要强行改成你的故事；
5. 如果我没想好并需要你设计，请至少给三个核心机制或读者体验明显不同的方案。每个方案说明核心想法、主要读者体验、最大优势、主要风险，以及对后续人物、世界和剧情的影响；
6. 我可以选择一个、混合多个或全部否定；不要默认推荐方案已经被我接受；
7. 如果我问“目前什么题材火”之类依赖当前市场的问题：有联网能力就搜索近期公开资料，并标注资料时间和来源；没有联网能力就明确说无法可靠判断，不能用旧知识假装当前数据；
8. 不得照搬参考作品的专有设定或表达，不得为了显得完整而编造细节；
9. 形成草案后必须问我是否确认。未经我明确确认，不得宣布阶段完成；
10. 你根据下面的完成标准判断阶段是否结束，而不是按聊天轮数判断。

完成标准：
- 我能说清目前想写的核心体验，或者明确决定暂时不确定；
- 已选题材时，题材与核心体验不明显冲突；
- 未选题材时，已经得到可比较的候选方向，或明确记录缺少实时数据；
- 目标平台、商业模式和读者即使尚未决定，也已明确记录当前状态；
- 真正阻碍下一阶段的问题已经列出；没有阻碍时明确写“无”；
- 所有 AI 补充内容都经过我的确认；
- 没有提前写入后续阶段的大量细节。

形成正式结果时，只能写回 ${STAGE_ZERO_FILE} 的以下四类标准字段：
- 创作意图与目标体验
- 当前题材方向
- 目标平台、商业模式与读者
- 当前重大未决问题

每个字段都必须分别填写：
- 必要程度：必填 / 条件必填 / 选填
- 锁定时机：开写前锁定 / 开写前暂定 / 可延后
- 确认状态：已确认 / 暂定 / 刻意留白 / 不适用

结束时只能使用下面三种格式之一。除格式中的内容外，不要在结束块后追加解释。

【阶段结束：已形成结果】

阶段编号：0
阶段名称：灵感与创作约束

用户已确认：
- 

写回开书包：
- 文件：${STAGE_ZERO_FILE}
  - 字段：创作意图与目标体验
  - 内容：
  - 必要程度：必填
  - 锁定时机：
  - 确认状态：
- 文件：${STAGE_ZERO_FILE}
  - 字段：当前题材方向
  - 内容：
  - 必要程度：必填
  - 锁定时机：
  - 确认状态：
- 文件：${STAGE_ZERO_FILE}
  - 字段：目标平台、商业模式与读者
  - 内容：
  - 必要程度：必填
  - 锁定时机：
  - 确认状态：
- 文件：${STAGE_ZERO_FILE}
  - 字段：当前重大未决问题
  - 内容：
  - 必要程度：必填
  - 锁定时机：
  - 确认状态：

仍然暂定或刻意留白：
- 无 / 具体内容与原因

与已有内容的冲突检查：
- 第 0 步尚无既有正式内容 / 具体冲突及处理

对下一阶段的影响：
- 

【阶段结束：无正式结果】

阶段编号：0
阶段名称：灵感与创作约束
结束原因：不适用 / 用户决定延后 / 缺少可靠数据 / 其他

本阶段已经确认的“无结果决定”：
- 

最迟应在什么时候重新处理：
- 无需重开 / 进入某阶段前 / 获得某项数据后

对下一阶段的影响：
- 不影响 / 具体影响

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

export function buildStageZeroDocument(
  userIdea: string,
  parsed: ParsedStageResult,
  confirmedAt: string,
) {
  const time = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(confirmedAt));

  if (parsed.status === "NO_RESULT") {
    return `# 新书开书总表

> 当前仅完成“第 0 步：灵感与创作约束”的无结果记录。后续阶段尚未填写。

## 第 0 步状态

| 项目 | 当前记录 |
|---|---|
| 状态 | 本阶段无正式结果 |
| 结束原因 | ${escapeTableCell(parsed.noResultReason)} |
| 最迟重新处理 | ${escapeTableCell(parsed.reopenWhen)} |
| 对下一阶段的影响 | ${escapeTableCell(parsed.nextImpact)} |
| 用户最初输入 | ${escapeTableCell(userIdea)} |
| 确认时间 | ${time} |
`;
  }

  const rows = parsed.writebacks.map((item) =>
    `| ${escapeTableCell(item.field)} | ${escapeTableCell(item.content)} | ${escapeTableCell(item.necessity)} | ${escapeTableCell(item.lockTiming)} | ${escapeTableCell(item.confirmation)} |`,
  ).join("\n");

  return `# 新书开书总表

> 当前只写入“第 0 步：灵感与创作约束”的已确认结果。品类承诺、差异化和核心发动机等内容将在后续阶段补充。

## 创作起点

| 字段 | 当前结果 | 必要程度 | 锁定时机 | 确认状态 |
|---|---|---|---|---|
${rows}

## 阶段检查

- **用户已确认**：${escapeTableCell(parsed.confirmed)}
- **仍然暂定或刻意留白**：${escapeTableCell(parsed.unresolved || "无")}
- **冲突检查**：${escapeTableCell(parsed.conflictCheck)}
- **对下一阶段的影响**：${escapeTableCell(parsed.nextImpact)}
- **用户最初输入**：${escapeTableCell(userIdea)}
- **确认时间**：${time}
`;
}
