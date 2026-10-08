export const ENTITY_LABELS: Record<string, string> = {
  PERSON: "人物",
  ORGANIZATION: "组织",
  PLACE: "地点",
  OBJECT: "重要物品",
  OTHER: "其他",
};

export const EVENT_LABELS: Record<string, string> = {
  ACTION: "行动",
  DISCOVERY: "发现",
  CONFLICT: "冲突",
  DECISION: "决定",
  STATE_CHANGE: "状态变化",
  OTHER: "其他",
};

export const EVENT_DISCOVERY_ROUTE_LABELS: Record<string, string> = {
  ACTION: "明确行动",
  STATE_CHANGE: "状态变化",
  INFORMATION_CHANGE: "信息变化",
  RELATION_CHANGE: "关系变化",
  DOCUMENT_CONTEXT: "跨段上下文",
};

export const NARRATIVE_MODE_LABELS: Record<string, string> = {
  ACTUAL: "真实发生",
  MEMORY: "回忆",
  REPORT: "传闻或转述",
  LIE: "谎言",
  MISUNDERSTANDING: "误解",
  HYPOTHESIS: "推测",
  REPEATED_MENTION: "重复提及",
  UNCERTAIN: "叙事性质尚未核对",
};

export const EVENT_RELATION_LABELS: Record<string, string> = {
  CAUSES: "直接导致",
  ENABLES: "为后续创造条件",
  REVEALS: "揭示信息",
  ESCALATES: "使局势升级",
  RESOLVES: "推动解决",
  PRECEDES: "发生在前",
  SUBEVENT: "属于其中一部分",
};

export const ROLE_LABELS: Record<string, string> = {
  PROTAGONIST: "主角",
  CORE_SUPPORTING: "核心配角",
  IMPORTANT_SUPPORTING: "重要配角",
  MINOR: "次要人物",
  UNCLASSIFIED: "尚未定位",
};

export const ROLE_ORDER = [
  "PROTAGONIST",
  "CORE_SUPPORTING",
  "IMPORTANT_SUPPORTING",
  "MINOR",
  "UNCLASSIFIED",
] as const;

export const FACT_TYPE_LABELS: Record<string, string> = {
  PLACE: "地点",
  ORGANIZATION: "组织",
  OBJECT: "物品",
  ABILITY: "能力",
  RULE: "规则",
  RELATION: "关系",
  STATUS: "状态",
  OTHER: "其他",
};

export const FACT_TIMELINE_LABELS: Record<string, string> = {
  ACTIVE: "当前仍成立",
  EXPIRED: "此版本后来失效",
  REESTABLISHED: "失效后再次成立",
  CONFLICTING: "冲突说法并存",
};

export const KNOWLEDGE_LABELS: Record<string, string> = {
  KNOWS: "已经知道",
  BELIEVES: "相信",
  SUSPECTS: "有所怀疑",
  MISTAKEN: "存在误解",
  HIDDEN: "主动隐瞒",
  UNKNOWN: "尚不知道",
};

export const KNOWLEDGE_TRANSFER_LABELS: Record<string, string> = {
  WITNESSED: "亲眼见证",
  TOLD: "明确告知",
  OVERHEARD: "无意听见",
  RUMOR: "传闻传播",
  MISREPRESENTED: "信息被歪曲",
  RETRACTED: "信息被撤回",
};

export const FORESHADOWING_LABELS: Record<string, string> = {
  PLANTED: "已经提出",
  REINFORCED: "再次强化",
  MISDIRECTED: "形成误导",
  TRANSFORMED: "发生变形",
  PAYOFF: "已经回收",
  INVALIDATED: "已经失效",
  OPEN: "尚未回收",
};

export const CLAIM_STATUS_LABELS: Record<string, string> = {
  SUPPORTED: "证据支持",
  PARTIAL: "部分支持",
  DISPUTED: "存在争议",
  MIXED: "支持与反证并存",
  CONTRADICTED: "存在明确反证",
  INSUFFICIENT: "证据不足",
  INSUFFICIENT_EVIDENCE: "证据不足",
};

export const CHARACTER_DESIGN_STATUS_LABELS: Record<string, string> = {
  READY: "证据账本已生成",
  GENERATING: "正在核对全书主角事件",
  OUTDATED: "需要基于最新拆解重做",
  FAILED: "上次生成失败",
  NOT_GENERATED: "尚未生成",
};

export const CHARACTER_DESIGN_FIELD_LABELS: Record<string, string> = {
  surface_desire: "表层欲望",
  deep_desire: "深层欲望",
  motivation: "行动动机",
  contrast: "性格反差",
  boundary: "行为底线",
  core_ability: "核心能力",
};

export const CHAPTER_END_HOOK_TYPE_LABELS: Record<string, string> = {
  CRISIS_SUSPENSION: "危机悬置",
  NEW_INFORMATION: "新信息抛出",
  PAYOFF_PRIMING: "期待兑现前置",
  REVERSAL: "反转",
  EMOTIONAL_FREEZE: "情绪定格",
  NONE: "无钩",
};

export const CHAPTER_END_HOOK_STRENGTH_LABELS: Record<string, string> = {
  STRONG: "强钩",
  MEDIUM: "中钩",
  LIGHT: "缓钩",
  NONE: "无钩",
};

export const CONFLICT_TYPE_LABELS: Record<string, string> = {
  PERSON_V_PERSON: "人物之间",
  PERSON_V_SELF: "人物内心",
  PERSON_V_WORLD: "人物与环境或规则",
  PERSON_V_GROUP: "人物与群体",
  GROUP_V_GROUP: "群体之间",
  OTHER: "其他冲突",
};

export const ACTION_DIALOGUE_LABELS: Record<string, string> = {
  ACTION_HEAVY: "以行动为主",
  DIALOGUE_HEAVY: "以对话为主",
  BALANCED: "行动与对话较均衡",
  REFLECTIVE: "以内心活动或思考为主",
  UNCERTAIN: "暂时无法判断",
};
