import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AnalysisIssue,
  AnalysisRun,
  AnalysisRunDiagnostics,
  api,
  DeepAnalysisDiff,
  DeepAnalysisRevision,
  EntityCandidate,
  EvidenceContext,
  ModelSettings,
  Project,
  SourceIssue,
  SourceUnit,
  SourceUnitContent,
  SourceVersion,
  Workbench,
} from "./api";

const STAGES = [
  "导入与章节",
  "人物",
  "剧情与事件",
  "事实与设定",
  "伏笔与冲突",
  "完整工作台",
];

function formatNumber(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatFileSize(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function issueLabel(severity: SourceIssue["severity"]) {
  if (severity === "BLOCKING") return "需要确认";
  if (severity === "WARNING") return "请注意";
  return "建议检查";
}

const ENTITY_LABELS: Record<EntityCandidate["entity_type"], string> = {
  PERSON: "人物",
  ORGANIZATION: "组织",
  PLACE: "地点",
  OBJECT: "重要物品",
  OTHER: "其他",
};

const EVENT_LABELS: Record<string, string> = {
  ACTION: "行动",
  DISCOVERY: "发现",
  CONFLICT: "冲突",
  DECISION: "决定",
  STATE_CHANGE: "状态变化",
  OTHER: "其他",
};

const ROLE_LABELS: Record<string, string> = {
  PROTAGONIST: "主角",
  CORE_SUPPORTING: "核心配角",
  IMPORTANT_SUPPORTING: "重要配角",
  MINOR: "次要人物",
  UNCLASSIFIED: "尚未定位",
};

const FACT_TYPE_LABELS: Record<string, string> = {
  PLACE: "地点",
  ORGANIZATION: "组织",
  OBJECT: "物品",
  ABILITY: "能力",
  RULE: "规则",
  RELATION: "关系",
  STATUS: "状态",
  OTHER: "其他",
};

const KNOWLEDGE_LABELS: Record<string, string> = {
  KNOWS: "已经知道",
  BELIEVES: "相信",
  SUSPECTS: "有所怀疑",
  MISTAKEN: "存在误解",
  HIDDEN: "主动隐瞒",
  UNKNOWN: "尚不知道",
};

const FORESHADOWING_LABELS: Record<string, string> = {
  PLANTED: "已经提出",
  REINFORCED: "再次强化",
  MISDIRECTED: "形成误导",
  TRANSFORMED: "发生变形",
  PAYOFF: "已经回收",
  INVALIDATED: "已经失效",
  OPEN: "尚未回收",
};

const CLAIM_STATUS_LABELS: Record<string, string> = {
  SUPPORTED: "证据支持",
  MIXED: "支持与反证并存",
  CONTRADICTED: "存在明确反证",
  INSUFFICIENT_EVIDENCE: "证据不足",
};

const ANALYSIS_STAGE_STATUS_LABELS: Record<string, string> = {
  PENDING: "等待开始",
  RUNNING: "正在处理",
  SUCCEEDED: "已经完成",
  FAILED: "处理失败",
  CANCELLED: "已经取消",
};

type WorkbenchView =
  | "overview"
  | "source"
  | "characters"
  | "plot"
  | "events"
  | "timeline"
  | "facts"
  | "world"
  | "foreshadowing"
  | "conflicts"
  | "pacing"
  | "issues";

type FormalWorkbenchProps = {
  data: Workbench;
  analysisStatus: AnalysisRun["status"];
  view: WorkbenchView;
  onViewChange: (view: WorkbenchView) => void;
  evidenceContext: EvidenceContext | null;
  onOpenEvidence: (evidenceId: string) => void;
  onCloseEvidence: () => void;
  sourceChapters: SourceUnit[];
  selectedChapterId: string;
  chapterContent: SourceUnitContent | null;
  onSelectChapter: (chapterId: string) => void;
  busy: string;
  onAnalysisRunChange: (run: AnalysisRun) => void;
}

function FormalWorkbench({
  data,
  analysisStatus,
  view,
  onViewChange,
  evidenceContext,
  onOpenEvidence,
  onCloseEvidence,
  sourceChapters,
  selectedChapterId,
  chapterContent,
  onSelectChapter,
  busy,
  onAnalysisRunChange,
}: FormalWorkbenchProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [stateChapter, setStateChapter] = useState(data.chapters.at(-1)?.ordinal ?? 1);
  const [issues, setIssues] = useState<AnalysisIssue[]>([]);
  const [revisions, setRevisions] = useState<DeepAnalysisRevision[]>([]);
  const [revisionDiff, setRevisionDiff] = useState<DeepAnalysisDiff | null>(null);
  const [revisionData, setRevisionData] = useState<Workbench | null>(null);
  const [revisionBusy, setRevisionBusy] = useState(false);
  const [revisionError, setRevisionError] = useState("");
  const [issueTarget, setIssueTarget] = useState<{ kind: string; id: string | null; label: string } | null>(null);
  const [issueCategory, setIssueCategory] = useState("INCORRECT");
  const [issueNote, setIssueNote] = useState("");
  const [issueBusy, setIssueBusy] = useState("");
  const [issueError, setIssueError] = useState("");
  const viewData = revisionData ?? data;
  const isHistoricalRevision = revisionData !== null && revisionData.deep_revision !== data.deep_revision;
  const sourceChapterNumbers = useMemo(() => {
    const numbers = new Map<string, number>();
    let number = 0;
    for (const unit of sourceChapters) {
      if (unit.unit_type === "CHAPTER") numbers.set(unit.id, ++number);
    }
    return numbers;
  }, [sourceChapters]);
  const evidenceParts = evidenceContext
    ? evidenceContext.context_text.split(evidenceContext.evidence.text_snapshot)
    : [];
  useEffect(() => {
    setSearchQuery("");
    setStateChapter(data.chapters.at(-1)?.ordinal ?? 1);
    setRevisionData(null);
    setRevisionError("");
  }, [data.run_id, data.chapters]);
  useEffect(() => {
    let active = true;
    void Promise.all([
      api.analysisIssues(data.run_id),
      api.deepAnalysisRevisions(data.run_id),
    ]).then(async ([nextIssues, nextRevisions]) => {
      if (!active) return;
      setIssues(nextIssues);
      setRevisions(nextRevisions);
      if (nextRevisions.length > 1) {
        const diff = await api.deepAnalysisDiff(data.run_id);
        if (active) setRevisionDiff(diff);
      } else {
        setRevisionDiff(null);
      }
    }).catch(() => {
      if (active) {
        setIssues([]);
        setRevisions([]);
        setRevisionDiff(null);
      }
    });
    return () => { active = false; };
  }, [data.run_id, data.deep_revision]);
  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase("zh-CN");
    if (!query) return [];
    const entries = [
      ...viewData.characters.map((item) => ({ key: item.id, section: "人物", title: item.name, text: `${item.description} ${item.role_reason} ${item.identities.join(" ")} ${item.goals.join(" ")} ${item.motivations.join(" ")} ${item.abilities.join(" ")} ${item.secrets.join(" ")} ${item.arc_summary}`, evidenceIds: item.evidence_ids })),
      ...viewData.events.map((item) => ({ key: item.id, section: "事件", title: item.title, text: `${item.summary} ${item.people.join(" ")} ${item.related_entities.join(" ")}`, evidenceIds: item.evidence_ids })),
      ...viewData.phases.map((item) => ({ key: item.id, section: "剧情阶段", title: item.title, text: `${item.situation} ${item.goal} ${item.obstacle} ${item.outcome} ${item.change}`, evidenceIds: item.evidence_ids })),
      ...(viewData.deep_analysis?.fact_versions.map((item) => ({ key: item.id, section: "事实", title: item.subject, text: `${item.predicate} ${item.value}`, evidenceIds: item.evidence_ids })) ?? []),
      ...(viewData.deep_analysis?.world_rules.map((item) => ({ key: item.id, section: "世界设定", title: item.title, text: `${item.description} ${item.limitations.join(" ")} ${item.costs.join(" ")}`, evidenceIds: item.evidence_ids })) ?? []),
      ...(viewData.deep_analysis?.foreshadowing.map((item) => ({ key: item.id, section: "伏笔", title: item.title, text: item.setup, evidenceIds: item.evidence_ids })) ?? []),
      ...(viewData.deep_analysis?.conflicts.map((item) => ({ key: item.id, section: "冲突", title: item.title, text: `${item.goals} ${item.obstacles} ${item.stakes} ${item.resolution}`, evidenceIds: item.evidence_ids })) ?? []),
      ...(viewData.deep_analysis?.claims.map((item) => ({ key: item.id, section: "分析结论", title: item.claim_text, text: item.scope, evidenceIds: item.evidence_ids })) ?? []),
    ];
    return entries.filter((item) => `${item.title} ${item.text}`.toLocaleLowerCase("zh-CN").includes(query));
  }, [viewData, searchQuery]);
  const pointInTime = useMemo(() => {
    const deep = viewData.deep_analysis;
    if (!deep) return { facts: [], states: [], knowledge: [], rules: [] };
    const facts = deep.fact_versions.filter((item) => (
      item.valid_from_chapter <= stateChapter
      && (item.valid_to_chapter === null || item.valid_to_chapter >= stateChapter)
    ));
    const stateByKey = new Map<string, (typeof deep.state_changes)[number]>();
    for (const item of deep.state_changes) {
      if (item.chapter_ordinal <= stateChapter) {
        const key = `${item.subject}\u0000${item.aspect}`;
        const current = stateByKey.get(key);
        if (!current || current.chapter_ordinal <= item.chapter_ordinal) stateByKey.set(key, item);
      }
    }
    const knowledgeByKey = new Map<string, (typeof deep.actor_knowledge)[number]>();
    for (const item of deep.actor_knowledge) {
      if (item.chapter_ordinal <= stateChapter) {
        const key = `${item.actor}\u0000${item.proposition}`;
        const current = knowledgeByKey.get(key);
        if (!current || current.chapter_ordinal <= item.chapter_ordinal) knowledgeByKey.set(key, item);
      }
    }
    return {
      facts,
      states: [...stateByKey.values()],
      knowledge: [...knowledgeByKey.values()],
      rules: deep.world_rules.filter((item) => item.discovered_chapter <= stateChapter),
    };
  }, [viewData.deep_analysis, stateChapter]);
  const evidenceButtons = (evidenceIds: string[], label = "查看原文") => (
    <div className="evidence-buttons">
      {evidenceIds.map((evidenceId, index) => (
        <button type="button" className="secondary-button" key={evidenceId} disabled={busy === `evidence-${evidenceId}`} onClick={() => onOpenEvidence(evidenceId)}>
          {busy === `evidence-${evidenceId}` ? "正在打开" : `${label}${evidenceIds.length > 1 ? ` ${index + 1}` : ""}`}
        </button>
      ))}
    </div>
  );
  const markProblemButton = (kind: string, id: string | null, label: string) => (
    isHistoricalRevision
      ? <span className="historical-readonly">历史版本只读</span>
      : <button type="button" className="text-action" onClick={() => { setIssueTarget({ kind, id, label }); setIssueNote(""); onViewChange("issues"); }}>
          标记问题
        </button>
  );

  async function selectRevision(revisionNo: number) {
    if (revisionNo === data.deep_revision) {
      setRevisionData(null);
      setRevisionError("");
      return;
    }
    try {
      setRevisionBusy(true);
      setRevisionError("");
      setRevisionData(await api.analysisWorkbench(data.run_id, revisionNo));
    } catch (reason) {
      setRevisionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRevisionBusy(false);
    }
  }

  async function submitIssue(event: FormEvent) {
    event.preventDefault();
    if (!issueTarget || !issueNote.trim()) return;
    try {
      setIssueBusy("create");
      setIssueError("");
      const created = await api.createAnalysisIssue(data.run_id, {
        target_kind: issueTarget.kind,
        target_id: issueTarget.id,
        target_label: issueTarget.label,
        category: issueCategory,
        note: issueNote.trim(),
      });
      setIssues((current) => [created, ...current]);
      setIssueTarget(null);
      setIssueNote("");
    } catch (reason) {
      setIssueError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setIssueBusy("");
    }
  }

  async function resolveIssue(issueId: string) {
    try {
      setIssueBusy(issueId);
      setIssueError("");
      const resolved = await api.resolveAnalysisIssue(issueId);
      setIssues((current) => current.map((item) => item.id === resolved.id ? resolved : item));
    } catch (reason) {
      setIssueError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setIssueBusy("");
    }
  }

  async function recomputeFromIssues() {
    try {
      setIssueBusy("recompute");
      setIssueError("");
      onAnalysisRunChange(await api.recomputeDeepAnalysis(data.run_id));
    } catch (reason) {
      setIssueError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setIssueBusy("");
    }
  }

  const workbenchTabs: Array<{ key: WorkbenchView; label: string; count?: number }> = [
    { key: "overview", label: "总览" },
    { key: "source", label: "原文", count: sourceChapterNumbers.size },
    { key: "characters", label: "人物", count: viewData.characters.length },
    { key: "plot", label: "剧情", count: viewData.phases.length },
    { key: "events", label: "事件", count: viewData.events.length },
    { key: "timeline", label: "时间线", count: viewData.events.length },
    { key: "facts", label: "事实与状态", count: viewData.deep_analysis?.fact_versions.length ?? 0 },
    { key: "world", label: "世界设定", count: viewData.deep_analysis?.world_rules.length ?? 0 },
    { key: "foreshadowing", label: "伏笔", count: viewData.deep_analysis?.foreshadowing.length ?? 0 },
    { key: "conflicts", label: "冲突", count: viewData.deep_analysis?.conflicts.length ?? 0 },
    { key: "pacing", label: "节奏", count: viewData.deep_analysis?.scene_analysis.length ?? 0 },
    { key: "issues", label: "问题", count: issues.filter((item) => item.status === "OPEN").length },
  ];

  return (
    <section className="formal-workbench" aria-label="人物剧情事件工作台">
      <div className="formal-workbench-heading">
        <div>
          <p>{viewData.narrative_status === "READY" ? "故事结构已整理" : "基础人物与事件已整理"}</p>
          <h2>小说拆解工作台</h2>
          <span>{viewData.narrative_status === "READY" ? "从总览理解故事，再按人物、剧情、事实、伏笔和节奏深入查看；每项重要内容都能回到原文。" : "基础抽取已完成，完整故事总览和人物角色定位尚未生成。"}</span>
        </div>
        <div className="formal-workbench-counts">
          <div><strong>{viewData.characters.length}</strong><span>人物</span></div>
          <div><strong>{viewData.events.length}</strong><span>事件</span></div>
          <div><strong>{viewData.phases.length}</strong><span>剧情阶段</span></div>
          <div><strong>{viewData.deep_analysis?.fact_versions.length ?? 0}</strong><span>事实</span></div>
        </div>
      </div>

      {revisions.length > 0 && (
        <div className="revision-toolbar">
          <div>
            <strong>{isHistoricalRevision ? `正在查看第 ${viewData.deep_revision} 版` : `当前第 ${data.deep_revision} 版`}</strong>
            <span>{isHistoricalRevision ? "这是以前保存的结果，只能查看和比较，不会覆盖当前版本。" : "问题修正后会保存新版本，旧结果不会丢失。"}</span>
          </div>
          <label htmlFor="deep-revision-select">拆解版本</label>
          <select id="deep-revision-select" value={viewData.deep_revision ?? ""} disabled={revisionBusy} onChange={(event) => void selectRevision(Number(event.target.value))}>
            {revisions.map((revision) => <option value={revision.revision_no} key={revision.revision_no}>第 {revision.revision_no} 版{revision.revision_no === data.deep_revision ? "（当前）" : ""}</option>)}
          </select>
          {revisionBusy && <span>正在读取版本</span>}
        </div>
      )}
      {revisionError && <div className="analysis-issue-error" role="alert">{revisionError}</div>}

      <div className="workbench-search">
        <label htmlFor="workbench-search-input">搜索整个拆解结果</label>
        <input id="workbench-search-input" type="search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="搜索人物、事件、设定、伏笔或分析结论" />
        {searchQuery.trim() && <span>找到 {searchResults.length} 项</span>}
      </div>

      <nav className="formal-workbench-tabs" aria-label="工作台版块">
        {workbenchTabs.map(({ key, label, count }) => (
          <button type="button" key={key} className={view === key ? "active" : ""} onClick={() => { setSearchQuery(""); onViewChange(key); }}>
            {label}
            {count !== undefined && <span>{count}</span>}
          </button>
        ))}
      </nav>

      <div className="formal-workbench-body">
        <div className="formal-workbench-content">
          {searchQuery.trim() && (
            <section className="search-results">
              <header><h3>搜索结果</h3><span>{searchResults.length} 项</span></header>
              {searchResults.map((item) => (
                <article key={`${item.section}-${item.key}`}>
                  <span>{item.section}</span>
                  <h4>{item.title}</h4>
                  <p>{item.text}</p>
                  {evidenceButtons(item.evidenceIds)}
                </article>
              ))}
              {!searchResults.length && <p className="result-empty">没有找到相关内容，可以换一个人物名、地点或情节关键词。</p>}
            </section>
          )}

          {!searchQuery.trim() && view === "overview" && (
            <>
              <div className="workbench-callout">
                <strong>{viewData.story_overview ? "先用故事骨架理解这部分小说" : "完整故事骨架尚未生成"}</strong>
                <span>{viewData.story_overview ? "总览、人物角色和剧情阶段由原文证据整理而来；仍有争议的内容会标明，不会被悄悄当成确定事实。" : "当前页面只保留基础抽取结果，系统会在叙事综合完成后显示故事总览。"}</span>
              </div>
              {viewData.story_overview && (
                <article className="overview-story-card">
                  <span className="section-kicker">故事总览</span>
                  <h3>{viewData.story_overview.premise}</h3>
                  <p>{viewData.story_overview.synopsis}</p>
                  <dl className="overview-facts">
                    <div><dt>主角</dt><dd>{viewData.story_overview.protagonist}</dd></div>
                    <div><dt>当前目标</dt><dd>{viewData.story_overview.protagonist_goal || "证据不足"}</dd></div>
                    <div><dt>核心冲突</dt><dd>{viewData.story_overview.central_conflict || "证据不足"}</dd></div>
                    <div><dt>当前局面</dt><dd>{viewData.story_overview.current_situation || "证据不足"}</dd></div>
                  </dl>
                  {viewData.story_overview.unresolved_questions.length > 0 && <div className="overview-questions"><strong>未解决问题</strong>{viewData.story_overview.unresolved_questions.map((item) => <span key={item}>{item}</span>)}</div>}
                  {evidenceButtons(viewData.story_overview.evidence_ids)}
                </article>
              )}
              <div className="phase-overview-list">
                {viewData.phases.map((phase, index) => (
                  <article className="phase-card" key={phase.id}>
                    <header><span>阶段 {index + 1}</span><h3>{phase.title}</h3></header>
                    <p>{phase.summary}</p>
                    {phase.people.length > 0 && <small>参与人物：{phase.people.join("、")}</small>}
                    {evidenceButtons(phase.evidence_ids)}
                  </article>
                ))}
                {!viewData.phases.length && <p className="result-empty">当前没有生成可读的剧情阶段。</p>}
              </div>
              <div className="related-entity-summary">
                <header><h3>地点、组织与重要事物</h3><span>{viewData.related_entities.length} 个</span></header>
                <div>{viewData.related_entities.map((entity) => <span key={entity.id}>{ENTITY_LABELS[entity.entity_type]}：{entity.name}</span>)}</div>
              </div>
              {viewData.deep_analysis?.claims.length ? <section className="insight-group overview-claims"><header><div><span>带证据的专项判断</span><h3>分析结论</h3></div><b>{viewData.deep_analysis.claims.length}</b></header><div className="formal-card-list compact-list">{viewData.deep_analysis.claims.map((claim) => <article className="formal-card" key={claim.id}><header><div><span>{claim.claim_kind === "FACT" ? "事实判断" : claim.claim_kind === "INFERENCE" ? "推断" : claim.claim_kind === "PATTERN" ? "叙事模式" : "解释"}</span><h3>{claim.claim_text}</h3></div><i className={claim.verification_status !== "SUPPORTED" ? "needs-review" : ""}>{CLAIM_STATUS_LABELS[claim.verification_status] ?? "待核验"}</i></header><small>适用范围：{claim.scope}</small>{evidenceButtons(claim.evidence_ids, "查看支持证据")}</article>)}</div></section> : null}
            </>
          )}

          {!searchQuery.trim() && view === "source" && (
            <div className="source-workbench-view">
              <div className="workbench-callout"><strong>原文与证据</strong><span>选择章节后，右侧会显示整章正文；从其他版块打开的证据会自动定位到对应上下文。</span></div>
              <div className="source-chapter-list">
                {sourceChapters.map((chapter) => (
                  <button type="button" key={chapter.id} className={selectedChapterId === chapter.id ? "active" : ""} onClick={() => onSelectChapter(chapter.id)}>
                    <span>{chapter.unit_type === "TITLE" ? "作品信息" : chapter.unit_type === "PREFACE" ? "正文前内容" : `第 ${sourceChapterNumbers.get(chapter.id) ?? chapter.ordinal} 章`}</span><strong>{chapter.title}</strong><small>{chapter.char_count.toLocaleString("zh-CN")} 字</small>
                  </button>
                ))}
                {!sourceChapters.length && <p className="result-empty">当前来源还没有可显示的章节。</p>}
              </div>
            </div>
          )}

          {!searchQuery.trim() && view === "characters" && (
            <div className="formal-card-list">
              {viewData.characters.map((character) => (
                <article className="formal-card" key={character.id}>
                  <header>
                    <div><span>{ROLE_LABELS[character.role] ?? "人物"}</span><h3>{character.name}</h3></div>
                    <i className={character.status === "UNCERTAIN" ? "needs-review" : ""}>{character.status === "UNCERTAIN" ? "待抽查" : `置信度 ${character.confidence}%`}</i>
                  </header>
                  <p>{character.description || "原文中已识别到该人物。"}</p>
                  <small>{character.role_reason}</small>
                  {character.aliases.length > 0 && <small>别名或称谓：{character.aliases.join("、")}</small>}
                  {character.identities.length > 0 && <small>身份：{character.identities.join("、")}</small>}
                  {character.goals.length > 0 && <small>目标：{character.goals.join("、")}</small>}
                  {character.motivations.length > 0 && <small>动机：{character.motivations.join("、")}</small>}
                  {character.abilities.length > 0 && <small>能力：{character.abilities.join("、")}</small>}
                  {character.secrets.length > 0 && <small>秘密：{character.secrets.join("、")}</small>}
                  {character.important_experiences.length > 0 && <small>重要经历：{character.important_experiences.join("；")}</small>}
                  {character.current_state && <small>当前状态：{character.current_state}</small>}
                  {character.arc_summary && <p><strong>人物变化：</strong>{character.arc_summary}</p>}
                  <div className="character-meta">
                    <span>出场活跃度：{character.activity_level}</span>
                    <span>证据 {character.appearance_count} 处</span>
                    <span>{character.first_chapter_ordinal ? `第 ${character.first_chapter_ordinal} 章首次出现` : "章节位置待定"}</span>
                    <span>{character.event_ids.length} 个相关事件</span>
                  </div>
                  {evidenceButtons(character.evidence_ids)}
                  {markProblemButton("CHARACTER", character.id, character.name)}
                </article>
              ))}
              {!viewData.characters.length && <p className="result-empty">当前没有整理出人物档案。</p>}
              {viewData.character_relations.length > 0 && <section className="relation-section"><header><h3>人物关系</h3><span>{viewData.character_relations.length} 条</span></header>{viewData.character_relations.map((relation, index) => <article key={`${relation.source_name}-${relation.target_name}-${index}`}><strong>{relation.source_name} ↔ {relation.target_name}</strong><span>{relation.relation}</span><p>{relation.current_state || "关系状态待补充"}</p>{relation.changes.length > 0 && <small>变化：{relation.changes.join("；")}</small>}{evidenceButtons(relation.evidence_ids)}</article>)}</section>}
            </div>
          )}

          {!searchQuery.trim() && view === "plot" && (
            <div className="formal-card-list">
              {viewData.phases.map((phase, index) => (
                <article className="formal-card phase-detail-card" key={phase.id}>
                  <header><div><span>阶段 {index + 1}</span><h3>{phase.title}</h3></div><i>{phase.chapter_titles.join("、") || "章节待定"}</i></header>
                  <p>{phase.situation}</p>
                  {phase.goal && <small>阶段目标：{phase.goal}</small>}
                  {phase.obstacle && <small>主要障碍：{phase.obstacle}</small>}
                  <div className="phase-event-list">
                    {phase.event_ids.map((eventId) => {
                      const event = viewData.events.find((item) => item.id === eventId);
                      return event ? <div key={event.id}><span>{EVENT_LABELS[event.event_type] ?? "事件"}</span><strong>{event.title}</strong></div> : null;
                    })}
                  </div>
                  {phase.outcome && <p><strong>结果：</strong>{phase.outcome}</p>}
                  {phase.change && <p><strong>局面变化：</strong>{phase.change}</p>}
                  {phase.next_hook && <p><strong>下一步悬念：</strong>{phase.next_hook}</p>}
                  {evidenceButtons(phase.evidence_ids)}
                </article>
              ))}
              {!viewData.phases.length && <p className="result-empty">当前没有生成可读的剧情阶段。</p>}
            </div>
          )}

          {!searchQuery.trim() && view === "events" && (
            <div className="formal-card-list">
              {viewData.events.map((event) => (
                <article className="formal-card event-formal-card" key={event.id}>
                  <header><div><span>{EVENT_LABELS[event.event_type] ?? "事件"}</span><h3>{event.title}</h3></div><i className={event.status === "UNCERTAIN" ? "needs-review" : ""}>{event.status === "UNCERTAIN" ? "待抽查" : event.chapter_titles.join("、") || "章节待定"}</i></header>
                  <p>{event.summary}</p>
                  {event.people.length > 0 && <small>参与人物：{event.people.join("、")}</small>}
                  {event.related_entities.length > 0 && <small>相关地点、组织或事物：{event.related_entities.join("、")}</small>}
                  <div className="character-meta"><span>{event.chapter_titles.join("、") || "章节待定"}</span><span>原文依据 {event.evidence_ids.length} 处</span><span>置信度 {event.confidence}%</span>{event.mention_count > 1 && <span>合并 {event.mention_count} 次提及</span>}</div>
                  {evidenceButtons(event.evidence_ids)}
                  {markProblemButton("EVENT", event.id, event.title)}
                </article>
              ))}
              {!viewData.events.length && <p className="result-empty">当前没有整理出事件。</p>}
            </div>
          )}

          {!searchQuery.trim() && view === "timeline" && (
            <div className="deep-analysis-view">
              <div className="workbench-callout"><strong>按故事推进顺序查看</strong><span>同一事件的重复提及会合并显示；回忆、传闻和误解仍需更多事件边界能力才能完整区分。</span></div>
              <div className="event-timeline">
                {viewData.events.map((event, index) => <article key={event.id}><span>{index + 1}</span><div><small>{event.chapter_titles.join("、") || "章节待定"} · {EVENT_LABELS[event.event_type] ?? "事件"}</small><h3>{event.title}</h3><p>{event.summary}</p>{event.people.length > 0 && <small>参与人物：{event.people.join("、")}</small>}{evidenceButtons(event.evidence_ids)}</div></article>)}
                {!viewData.events.length && <p className="result-empty">当前没有整理出事件时间线。</p>}
              </div>
              {viewData.event_relations.length > 0 && <section className="relation-section"><header><h3>前因与后果</h3><span>{viewData.event_relations.length} 条</span></header>{viewData.event_relations.map((relation) => <article key={`${relation.source_event_id}-${relation.target_event_id}`}><strong>{relation.source_title} → {relation.target_title}</strong><span>{relation.relation}</span><p>{relation.explanation}</p>{evidenceButtons(relation.evidence_ids)}</article>)}</section>}
            </div>
          )}

          {!searchQuery.trim() && view === "facts" && (
            <div className="deep-analysis-view">
              {!viewData.deep_analysis ? (
                <div className="workbench-callout"><strong>事实与状态仍在整理</strong><span>系统完成证据校验后，会在这里显示世界事实、状态变化和人物认知。</span></div>
              ) : (
                <>
                  <div className="chapter-state-selector">
                    <div><strong>查看指定章节时的状态</strong><span>后面的章节不会提前泄露到这个视图。</span></div>
                    <label htmlFor="state-chapter">截至</label>
                    <select id="state-chapter" value={stateChapter} onChange={(event) => setStateChapter(Number(event.target.value))}>
                      {viewData.chapters.map((chapter) => <option value={chapter.ordinal} key={chapter.ordinal}>第 {chapter.ordinal} 章 · {chapter.title}</option>)}
                    </select>
                  </div>
                  <section className="insight-group">
                    <header><div><span>第 {stateChapter} 章时仍然成立</span><h3>世界事实</h3></div><b>{pointInTime.facts.length}</b></header>
                    <div className="formal-card-list compact-list">
                      {pointInTime.facts.map((fact) => (
                        <article className="formal-card" key={fact.id}>
                          <header><div><span>{FACT_TYPE_LABELS[fact.fact_type] ?? "事实"}</span><h3>{fact.subject}</h3></div><i className={fact.status === "DISPUTED" || fact.status === "UNCERTAIN" ? "needs-review" : ""}>{fact.status === "CONFIRMED" ? "原文确认" : fact.status === "REPORTED" ? "人物转述" : fact.status === "DISPUTED" ? "存在争议" : "尚不确定"}</i></header>
                          <p><strong>{fact.predicate}：</strong>{fact.value}</p>
                          <small>有效范围：第 {fact.valid_from_chapter} 章起{fact.valid_to_chapter ? `，至第 ${fact.valid_to_chapter} 章` : "，当前仍成立"}</small>
                          {evidenceButtons(fact.evidence_ids)}
                          {fact.counter_evidence_ids.length > 0 && evidenceButtons(fact.counter_evidence_ids, "查看反证")}
                          {markProblemButton("FACT", fact.id, `${fact.subject}：${fact.predicate}`)}
                        </article>
                      ))}
                      {!pointInTime.facts.length && <p className="result-empty">截至这一章，没有可确认且仍然成立的世界事实。</p>}
                    </div>
                  </section>

                  <section className="insight-group">
                    <header><div><span>每项只显示截至当前章节的最新状态</span><h3>人物与事物状态</h3></div><b>{pointInTime.states.length}</b></header>
                    <div className="timeline-list">
                      {pointInTime.states.map((change) => (
                        <article key={change.id}><span>第 {change.chapter_ordinal} 章</span><div><h4>{change.subject} · {change.aspect}</h4><p>{change.before ? `${change.before} → ${change.after}` : change.after}</p>{evidenceButtons(change.evidence_ids)}</div></article>
                      ))}
                      {!pointInTime.states.length && <p className="result-empty">截至这一章，没有识别到可靠的状态变化。</p>}
                    </div>
                  </section>

                  <section className="insight-group">
                    <header><div><span>不是上帝视角</span><h3>人物当时知道什么</h3></div><b>{pointInTime.knowledge.length}</b></header>
                    <div className="knowledge-grid">
                      {pointInTime.knowledge.map((knowledge) => (
                        <article key={knowledge.id}><span>{KNOWLEDGE_LABELS[knowledge.state] ?? "认知状态"}</span><h4>{knowledge.actor}</h4><p>{knowledge.proposition}</p><small>截至第 {knowledge.chapter_ordinal} 章</small>{evidenceButtons(knowledge.evidence_ids)}</article>
                      ))}
                      {!pointInTime.knowledge.length && <p className="result-empty">截至这一章，没有足够证据区分人物认知。</p>}
                    </div>
                  </section>

                </>
              )}
            </div>
          )}

          {!searchQuery.trim() && view === "world" && (
            <div className="deep-analysis-view">
              {!viewData.deep_analysis ? <div className="workbench-callout"><strong>世界设定仍在整理</strong><span>系统完成证据校验后，会在这里显示地点、组织、能力、限制、代价和例外。</span></div> : <>
                <div className="chapter-state-selector"><div><strong>按章节查看当时已经出现的设定</strong><span>后面的章节不会提前泄露到这个视图。</span></div><label htmlFor="world-state-chapter">截至</label><select id="world-state-chapter" value={stateChapter} onChange={(event) => setStateChapter(Number(event.target.value))}>{viewData.chapters.map((chapter) => <option value={chapter.ordinal} key={chapter.ordinal}>第 {chapter.ordinal} 章 · {chapter.title}</option>)}</select></div>
                <section className="insight-group"><header><div><span>限制、代价和例外都会保留</span><h3>世界规则</h3></div><b>{pointInTime.rules.length}</b></header><div className="formal-card-list compact-list">{pointInTime.rules.map((rule) => <article className="formal-card" key={rule.id}><header><div><span>世界设定</span><h3>{rule.title}</h3></div><i>第 {rule.discovered_chapter} 章起可知</i></header><p>{rule.description}</p>{rule.limitations.length > 0 && <small>限制：{rule.limitations.join("；")}</small>}{rule.costs.length > 0 && <small>代价：{rule.costs.join("；")}</small>}{rule.exceptions.length > 0 && <small>例外：{rule.exceptions.join("；")}</small>}{evidenceButtons(rule.evidence_ids)}</article>)}{!pointInTime.rules.length && <p className="result-empty">截至这一章，原文尚未明确建立世界规则。</p>}</div></section>
              </>}
            </div>
          )}

          {!searchQuery.trim() && view === "foreshadowing" && (
            <div className="deep-analysis-view">
              {!viewData.deep_analysis ? (
                <div className="workbench-callout"><strong>核心分析仍在整理</strong><span>系统会先验证事实和状态，再生成伏笔、冲突、节奏和场景分析。</span></div>
              ) : (
                <section className="insight-group">
                    <header><div><span>叙事承诺账本</span><h3>伏笔与回收</h3></div><b>{viewData.deep_analysis.foreshadowing.length}</b></header>
                    <div className="formal-card-list compact-list">
                      {viewData.deep_analysis.foreshadowing.map((item) => (
                        <article className="formal-card" key={item.id}><header><div><span>{FORESHADOWING_LABELS[item.lifecycle] ?? "伏笔"}</span><h3>{item.title}</h3></div><i>第 {item.setup_chapter} 章提出</i></header><p>{item.setup}</p>{item.payoff_chapter && <small>第 {item.payoff_chapter} 章出现回收</small>}{evidenceButtons(item.evidence_ids)}{markProblemButton("FORESHADOWING", item.id, item.title)}</article>
                      ))}
                      {!viewData.deep_analysis.foreshadowing.length && <p className="result-empty">当前没有足够证据确认伏笔。</p>}
                    </div>
                  </section>
              )}
            </div>
          )}

          {!searchQuery.trim() && view === "conflicts" && (
            <div className="deep-analysis-view">
              {!viewData.deep_analysis ? <div className="workbench-callout"><strong>冲突分析仍在整理</strong><span>系统会整理参与者目标、障碍、赌注、升级过程和当前结果。</span></div> : <section className="insight-group"><header><div><span>目标、障碍与赌注</span><h3>冲突</h3></div><b>{viewData.deep_analysis.conflicts.length}</b></header><div className="formal-card-list compact-list">{viewData.deep_analysis.conflicts.map((conflict) => <article className="formal-card" key={conflict.id}><header><div><span>{conflict.status === "RESOLVED" ? "已经解决" : conflict.status === "ESCALATING" ? "正在升级" : conflict.status === "SHIFTED" ? "冲突转向" : conflict.status === "UNCERTAIN" ? "尚不确定" : "仍未解决"}</span><h3>{conflict.title}</h3></div></header>{conflict.participants.length > 0 && <small>参与者：{conflict.participants.join("、")}</small>}{conflict.goals && <p><strong>目标：</strong>{conflict.goals}</p>}{conflict.obstacles && <p><strong>障碍：</strong>{conflict.obstacles}</p>}{conflict.stakes && <p><strong>赌注：</strong>{conflict.stakes}</p>}{conflict.escalation.length > 0 && <small>升级过程：{conflict.escalation.join("；")}</small>}{conflict.resolution && <p><strong>当前结果：</strong>{conflict.resolution}</p>}{evidenceButtons(conflict.evidence_ids)}{markProblemButton("CONFLICT", conflict.id, conflict.title)}</article>)}{!viewData.deep_analysis.conflicts.length && <p className="result-empty">当前没有整理出证据充分的冲突。</p>}</div></section>}
            </div>
          )}

          {!searchQuery.trim() && view === "pacing" && (
            <div className="deep-analysis-view">
              {!viewData.deep_analysis ? <div className="workbench-callout"><strong>节奏分析仍在整理</strong><span>系统会按章节说明场景作用、信息释放和节奏变化。</span></div> : <section className="insight-group"><header><div><span>章节如何发挥作用</span><h3>场景与节奏</h3></div><b>{viewData.deep_analysis.scene_analysis.length}</b></header><div className="scene-analysis-list">{viewData.deep_analysis.scene_analysis.map((scene) => <article key={scene.id}><div><span>第 {scene.chapter_ordinal} 章</span><b>{scene.function === "SETUP" ? "铺垫" : scene.function === "TRANSITION" ? "过渡" : scene.function === "REVELATION" ? "揭示" : scene.function === "CONFLICT" ? "冲突" : scene.function === "DECISION" ? "决定" : scene.function === "AFTERMATH" ? "余波" : "其他功能"}</b></div><h4>{scene.summary}</h4><p>节奏：{scene.pace === "SLOW" ? "较慢" : scene.pace === "STEADY" ? "平稳" : scene.pace === "FAST" ? "较快" : scene.pace === "ACCELERATING" ? "正在加速" : scene.pace === "BRAKING" ? "明显放缓" : "尚不确定"}</p>{scene.information_released.length > 0 && <small>释放信息：{scene.information_released.join("；")}</small>}{scene.action_dialogue_balance && <small>动作与对话：{scene.action_dialogue_balance}</small>}{evidenceButtons(scene.evidence_ids)}</article>)}{!viewData.deep_analysis.scene_analysis.length && <p className="result-empty">当前没有完成场景与节奏分析。</p>}</div></section>}
            </div>
          )}

          {!searchQuery.trim() && view === "issues" && (
            <section className="analysis-problem-center embedded">
              <header><div><p>问题与修正</p><h3>告诉系统哪里需要重新检查</h3><span>只描述内容问题即可，系统会自行重新分析并保留旧版本。</span></div><b>{issues.filter((item) => item.status === "OPEN").length} 项待处理</b></header>
              {issueError && <div className="analysis-issue-error" role="alert">{issueError}</div>}
              {issueTarget && <form className="analysis-issue-form" onSubmit={submitIssue}><div><span>正在标记</span><strong>{issueTarget.label}</strong></div><label>问题类型<select value={issueCategory} onChange={(event) => setIssueCategory(event.target.value)}><option value="INCORRECT">内容不正确</option><option value="EVIDENCE">原文依据不对</option><option value="UNCLEAR">表达看不懂</option><option value="MISSING">遗漏重要内容</option><option value="OTHER">其他问题</option></select></label><label>具体说明<textarea value={issueNote} onChange={(event) => setIssueNote(event.target.value)} placeholder="例如：这里把人物的猜测写成了确定事实" maxLength={2000} /></label><div className="issue-form-actions"><button type="button" className="secondary-button" onClick={() => setIssueTarget(null)}>取消</button><button type="submit" disabled={!issueNote.trim() || issueBusy === "create"}>{issueBusy === "create" ? "正在保存" : "保存问题"}</button></div></form>}
              {issues.length > 0 ? <div className="analysis-issue-list">{issues.map((issue) => <article key={issue.id} className={issue.status === "RESOLVED" ? "resolved" : ""}><div><span>{issue.status === "OPEN" ? "等待处理" : "已经处理"}</span><strong>{issue.target_label}</strong><p>{issue.note}</p></div>{issue.status === "OPEN" && <button type="button" className="secondary-button" disabled={issueBusy === issue.id} onClick={() => void resolveIssue(issue.id)}>{issueBusy === issue.id ? "处理中" : "不再处理"}</button>}</article>)}</div> : <p className="result-empty">目前没有标记问题。可以在人物、事件、事实、伏笔和冲突内容中点击“标记问题”。</p>}
              <footer><div><strong>{data.deep_revision ? `当前为第 ${data.deep_revision} 版深层拆解` : "尚未生成深层拆解版本"}</strong><span>{revisions.length > 1 && revisionDiff ? `上一版到当前版：新增 ${Object.values(revisionDiff.added).flat().length} 项，移除 ${Object.values(revisionDiff.removed).flat().length} 项，修改 ${Object.values(revisionDiff.changed_counts).reduce((sum, value) => sum + value, 0)} 项。` : "重新分析完成后会在这里显示版本变化。"}</span></div><button type="button" disabled={isHistoricalRevision || !issues.some((item) => item.status === "OPEN") || issueBusy === "recompute"} onClick={() => void recomputeFromIssues()}>{issueBusy === "recompute" ? "正在准备重新分析" : "根据待处理问题重新分析"}</button></footer>
            </section>
          )}
        </div>

        <aside className={`formal-evidence-reader ${evidenceContext ? "open" : ""}`}>
          {view === "source" ? (
            chapterContent ? <><header><div><p>整章原文</p><h3>{chapterContent.title}</h3></div><span>{sourceChapterNumbers.has(chapterContent.id) ? `第 ${sourceChapterNumbers.get(chapterContent.id)} 章` : "作品信息"}</span></header><div className="evidence-text full-chapter-text">{chapterContent.content}</div></> : <div className="evidence-empty"><strong>选择章节</strong><span>从左侧章节列表选择后，这里会显示完整正文。</span></div>
          ) : evidenceContext ? (
            <>
              <header><div><p>原文证据</p><h3>{evidenceContext.chapter_title}</h3></div><button type="button" className="secondary-button" onClick={onCloseEvidence}>关闭</button></header>
              <div className="evidence-text">{evidenceParts.map((part, index) => <span key={`${part}-${index}`}>{part}{index < evidenceParts.length - 1 && <mark>{evidenceContext.evidence.text_snapshot}</mark>}</span>)}</div>
            </>
          ) : <div className="evidence-empty"><strong>原文证据</strong><span>点击任一人物、事件、事实或分析结论的“查看原文”，这里会显示来源章节和上下文。</span></div>}
        </aside>
      </div>

      <footer className="formal-workbench-footer">
        {isHistoricalRevision ? (
          <div><strong>正在查看以前的拆解版本</strong><span>切换回标有“当前”的版本后，才能继续标记问题和重新分析。</span></div>
        ) : analysisStatus === "CONFIRMED" ? (
          <div><strong>当前拆解结果已经确认</strong><span>人物、剧情、事实状态和核心分析均已保存，可以继续回查原文。</span></div>
        ) : viewData.narrative_status !== "READY" ? (
          <div><strong>完整故事结构尚未完成</strong><span>当前内容仅供内部检查，不能作为正式拆解结果确认。</span></div>
        ) : viewData.deep_status !== "READY" ? (
          <div><strong>深层拆解仍在生成</strong><span>系统正在整理事实状态、世界设定、伏笔、冲突和节奏，当前不需要用户确认。</span></div>
        ) : (
          <div><strong>核心拆解已经生成</strong><span>所有重要结论均保留原文依据；证据不足或存在反证的内容会明确标出。</span></div>
        )}
      </footer>
    </section>
  );
}

export default function ProductWorkbench() {
  const [health, setHealth] = useState("checking");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [projectName, setProjectName] = useState("");
  const [versions, setVersions] = useState<SourceVersion[]>([]);
  const [activeVersion, setActiveVersion] = useState<SourceVersion | null>(null);
  const [chapters, setChapters] = useState<SourceUnit[]>([]);
  const [issues, setIssues] = useState<SourceIssue[]>([]);
  const [selectedChapter, setSelectedChapter] = useState("");
  const [chapterContent, setChapterContent] = useState<SourceUnitContent | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [modelSettings, setModelSettings] = useState<ModelSettings | null>(null);
  const [analysisRun, setAnalysisRun] = useState<AnalysisRun | null>(null);
  const [analysisDiagnostics, setAnalysisDiagnostics] = useState<AnalysisRunDiagnostics | null>(null);
  const [workbench, setWorkbench] = useState<Workbench | null>(null);
  const [workbenchView, setWorkbenchView] = useState<WorkbenchView>("overview");
  const [evidenceContext, setEvidenceContext] = useState<EvidenceContext | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadAnalysisResults = useCallback(async (run: AnalysisRun | null) => {
    setAnalysisRun(run);
    setWorkbench(null);
    setEvidenceContext(null);
    if (!run) {
      setAnalysisDiagnostics(null);
      return;
    }
    setAnalysisDiagnostics(await api.analysisDiagnostics(run.id));
    if (!["REVIEW", "CONFIRMED"].includes(run.status)) return;
    const workbenchResult = await api.analysisWorkbench(run.id);
    setWorkbench(workbenchResult);
  }, []);

  const loadProject = useCallback(async (projectId: string) => {
    setVersions([]);
    setActiveVersion(null);
    setChapters([]);
    setIssues([]);
    setSelectedChapter("");
    setChapterContent(null);
    setAnalysisRun(null);
    setAnalysisDiagnostics(null);
    setWorkbench(null);
    setEvidenceContext(null);
    if (!projectId) return;
    try {
      const sourceVersions = await api.sourceVersions(projectId);
      const latest = sourceVersions[0] ?? null;
      setVersions(sourceVersions);
      setActiveVersion(latest);
      if (latest) {
        const [sourceChapters, sourceIssues] = await Promise.all([
          api.sourceChapters(latest.id),
          api.sourceIssues(latest.id),
        ]);
        setChapters(sourceChapters);
        setIssues(sourceIssues);
        setSelectedChapter(
          sourceChapters.find((item) => item.unit_type === "CHAPTER")?.id
          ?? sourceChapters[0]?.id
          ?? "",
        );
        if (latest.status === "CONFIRMED") {
          await loadAnalysisResults(await api.latestAnalysis(latest.id));
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [loadAnalysisResults]);

  const loadProjects = useCallback(async () => {
    let healthResult: { status: string };
    try {
      healthResult = await api.health();
      setHealth(healthResult.status);
    } catch (reason) {
      setHealth("offline");
      setError(reason instanceof Error ? reason.message : String(reason));
      return;
    }

    try {
      const [projectResult, settingsResult] = await Promise.all([
        api.projects(),
        api.modelSettings(),
      ]);
      setProjects(projectResult);
      setModelSettings(settingsResult);
      setSelectedProject((current) => current || projectResult[0]?.id || "");
      setError("");
    } catch (reason) {
      setError(`后台已连接，但页面数据接口暂时不可用：${reason instanceof Error ? reason.message : String(reason)}`);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
    const timer = window.setInterval(() => void loadProjects(), 5000);
    return () => window.clearInterval(timer);
  }, [loadProjects]);

  useEffect(() => {
    void loadProject(selectedProject);
  }, [loadProject, selectedProject]);

  useEffect(() => {
    let active = true;
    if (!selectedChapter) {
      setChapterContent(null);
      return () => { active = false; };
    }
    void api.chapterContent(selectedChapter)
      .then((content) => {
        if (active) setChapterContent(content);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => { active = false; };
  }, [selectedChapter]);

  useEffect(() => {
    if (!activeVersion || !analysisRun || !["PENDING", "RUNNING"].includes(analysisRun.status)) {
      return;
    }
    let active = true;
    const refresh = async () => {
      try {
        const next = await api.latestAnalysis(activeVersion.id);
        if (active) await loadAnalysisResults(next);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      }
    };
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [activeVersion, analysisRun, loadAnalysisResults]);

  const activeProject = projects.find((project) => project.id === selectedProject) ?? null;
  const openIssues = issues.filter((issue) => issue.status === "OPEN");
  const blockingCount = openIssues.filter((issue) => issue.severity === "BLOCKING").length;
  const chapterCount = chapters.filter((chapter) => chapter.unit_type === "CHAPTER").length;
  const titleUnitCount = chapters.filter((chapter) => chapter.unit_type === "TITLE").length;
  const prefaceUnitCount = chapters.filter((chapter) => chapter.unit_type === "PREFACE").length;
  const currentStage = activeVersion?.status !== "CONFIRMED"
    ? 0
    : analysisRun?.status === "CONFIRMED"
      ? 3
      : 1;
  const chapterIssueMap = useMemo(() => {
    const counts = new Map<string, number>();
    for (const issue of openIssues) {
      if (issue.source_unit_id) {
        counts.set(issue.source_unit_id, (counts.get(issue.source_unit_id) ?? 0) + 1);
      }
    }
    return counts;
  }, [openIssues]);
  const chapterDisplayNumbers = useMemo(() => {
    const numbers = new Map<string, number>();
    let number = 0;
    for (const chapter of chapters) {
      if (chapter.unit_type === "CHAPTER") {
        number += 1;
        numbers.set(chapter.id, number);
      }
    }
    return numbers;
  }, [chapters]);

  async function handleCreateProject(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim()) return;
    try {
      setBusy("create-project");
      setError("");
      const project = await api.createProject(projectName.trim());
      setProjects((current) => [project, ...current]);
      setSelectedProject(project.id);
      setProjectName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleImport(event: FormEvent) {
    event.preventDefault();
    if (!file || !selectedProject) return;
    try {
      setBusy("import");
      setError("");
      const imported = await api.importSource(selectedProject, file);
      setVersions((current) => [
        imported.version,
        ...current.filter((item) => item.id !== imported.version.id),
      ]);
      setActiveVersion(imported.version);
      setChapters(imported.units);
      setIssues(imported.issues);
      setSelectedChapter(
        imported.units.find((item) => item.unit_type === "CHAPTER")?.id
        ?? imported.units[0]?.id
        ?? "",
      );
      setFile(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleResolveIssue(issue: SourceIssue) {
    try {
      setBusy(`issue-${issue.id}`);
      setError("");
      const resolved = await api.resolveSourceIssue(issue.id);
      setIssues((current) => current.map((item) => item.id === resolved.id ? resolved : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleConfirmSource() {
    if (!activeVersion) return;
    try {
      setBusy("confirm-source");
      setError("");
      const confirmed = await api.confirmSourceVersion(activeVersion.id);
      setActiveVersion(confirmed);
      setVersions((current) => current.map((item) => item.id === confirmed.id ? confirmed : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleStartAnalysis() {
    if (!activeVersion) return;
    try {
      setBusy("start-analysis");
      setError("");
      await loadAnalysisResults(await api.startAnalysis(activeVersion.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleOpenEvidence(evidenceId: string) {
    try {
      setBusy(`evidence-${evidenceId}`);
      setError("");
      setEvidenceContext(await api.evidenceContext(evidenceId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  const chapterReview = (
    <section className="chapter-review">
      <div className="chapter-list" aria-label="章节列表">
        <div className="chapter-list-heading">
          <h2>章节目录</h2>
          <span>
            {chapterCount} 章
            {titleUnitCount ? ` · ${titleUnitCount} 项作品信息` : ""}
            {prefaceUnitCount ? ` · ${prefaceUnitCount} 项正文前内容` : ""}
          </span>
        </div>
        <div className="chapter-scroll">
          {chapters.map((chapter) => (
            <button
              type="button"
              key={chapter.id}
              className={selectedChapter === chapter.id ? "active" : ""}
              onClick={() => setSelectedChapter(chapter.id)}
            >
              <span>
                {chapter.unit_type === "TITLE"
                  ? "作品"
                  : chapter.unit_type === "PREFACE"
                    ? "正文前"
                  : chapterDisplayNumbers.get(chapter.id) ?? chapter.ordinal}
              </span>
              <b>{chapter.title}</b>
              <small>{formatNumber(chapter.char_count)} 字符</small>
              {(chapterIssueMap.get(chapter.id) ?? 0) > 0 && <i>{chapterIssueMap.get(chapter.id)}</i>}
            </button>
          ))}
        </div>
      </div>
      <article className="chapter-reader">
        <header>
          <div>
            <p>原文抽查</p>
            <h2>{chapterContent?.title ?? "选择一个章节"}</h2>
          </div>
          {chapterContent && <span>{formatNumber(chapterContent.content.length)} 字符</span>}
        </header>
        <div className="chapter-text">
          {chapterContent?.content ?? "从左侧选择章节后查看原文。"}
        </div>
      </article>
    </section>
  );

  const analysisPercent = analysisRun && analysisRun.total_batches
    ? Math.round((analysisRun.completed_batches / analysisRun.total_batches) * 100)
    : 0;

  const analysisProfile = modelSettings?.analysis_profiles.find((item) => item.id === "entities-events") ?? null;
  const analysisService = modelSettings?.services.find((item) => item.id === analysisProfile?.service_id) ?? null;
  const analysisConfigured = Boolean(analysisService?.configured && analysisProfile?.model);

  return (
    <div className="product-shell">
      <header className="workbench-topbar">
        <div>
          <p className="product-kicker">AI 小说拆解工作台</p>
          <h1>{activeProject?.name ?? "我的小说项目"}</h1>
        </div>
        <div className="topbar-actions">
          <a className="button-link secondary-button" href="/settings"><span aria-hidden="true">⚙</span> 设置</a>
          <div className={`api-status ${health}`}>
            <span className="status-dot" />
            {health === "ok" ? "系统正常" : health === "offline" ? "系统未连接" : "正在连接"}
          </div>
        </div>
      </header>

      {error && <div className="product-error" role="alert">{error}</div>}

      <div className="workbench-layout">
        <aside className="project-sidebar">
          <div className="sidebar-heading">
            <h2>小说项目</h2>
            <span>{projects.length}</span>
          </div>
          <form className="project-create" onSubmit={handleCreateProject}>
            <label htmlFor="new-project">新小说名称</label>
            <div className="inline-control">
              <input
                id="new-project"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="例如：测试小说"
                maxLength={200}
              />
              <button type="submit" disabled={!projectName.trim() || busy === "create-project"}>
                {busy === "create-project" ? "创建中" : "新建"}
              </button>
            </div>
          </form>
          <nav className="novel-list" aria-label="小说项目">
            {projects.map((project) => (
              <button
                type="button"
                key={project.id}
                className={project.id === selectedProject ? "active" : ""}
                onClick={() => setSelectedProject(project.id)}
              >
                <span>{project.name}</span>
                <small>{project.id === selectedProject ? "当前项目" : "打开"}</small>
              </button>
            ))}
            {!projects.length && <p className="sidebar-empty">还没有小说项目</p>}
          </nav>
        </aside>

        <section className="product-main">
          {!activeProject ? (
            <div className="product-empty">
              <h2>先创建一本小说项目</h2>
              <p>创建后即可导入 TXT、Markdown、DOCX 或 EPUB 文件。</p>
            </div>
          ) : (
            <>
              <ol className="stage-progress" aria-label="拆解步骤">
                {STAGES.map((stage, index) => (
                  <li key={stage} className={index < currentStage ? "done" : index === currentStage ? "current" : "pending"}>
                    <span>{index + 1}</span>
                    <b>{stage}</b>
                  </li>
                ))}
              </ol>

              {!activeVersion ? (
                <section className="import-section">
                  <div className="section-title">
                    <p>第 1 步</p>
                    <h2>导入整本小说</h2>
                  </div>
                  <form className="file-import" onSubmit={handleImport}>
                    <label className="file-picker" htmlFor="novel-file">
                      <span>{file ? file.name : "选择小说文件"}</span>
                      <small>{file ? formatFileSize(file.size) : "TXT、Markdown、DOCX 或 EPUB"}</small>
                      <input
                        id="novel-file"
                        type="file"
                        accept=".txt,.md,.markdown,.docx,.epub"
                        onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                      />
                    </label>
                    <button type="submit" disabled={!file || busy === "import"}>
                      {busy === "import" ? "正在读取并识别章节" : "导入并检查章节"}
                    </button>
                  </form>
                </section>
              ) : (
                <>
                  <section className="source-overview">
                    <div className="section-title">
                      <p>第 1 步</p>
                      <h2>{activeVersion.status === "CONFIRMED" ? "章节已经确认" : "检查章节结构"}</h2>
                    </div>
                    <div className="source-metrics">
                      <div><span>导入字符</span><strong>{formatNumber(activeVersion.total_chars)}</strong></div>
                      <div><span>识别章节</span><strong>{activeVersion.chapter_count}</strong></div>
                      <div><span>需要确认</span><strong>{blockingCount}</strong></div>
                      <div><span>导入版本</span><strong>第 {activeVersion.version_no} 版</strong></div>
                    </div>
                  </section>

                  {activeVersion.status !== "CONFIRMED" && openIssues.length > 0 && (
                    <section className="source-issues" aria-label="导入问题">
                      <div className="section-title compact">
                        <p>导入检查</p>
                        <h2>发现 {openIssues.length} 项需要留意的内容</h2>
                      </div>
                      <div className="issue-list">
                        {openIssues.map((issue) => (
                          <div className={`source-issue ${issue.severity.toLowerCase()}`} key={issue.id}>
                            <span>{issueLabel(issue.severity)}</span>
                            <p>{issue.message}</p>
                            {issue.severity === "BLOCKING" && (
                              <button
                                type="button"
                                className="secondary-button"
                                disabled={busy === `issue-${issue.id}`}
                                onClick={() => void handleResolveIssue(issue)}
                              >
                                {busy === `issue-${issue.id}` ? "处理中" : "确认保留"}
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {activeVersion.status !== "CONFIRMED" ? (
                    <>
                      {chapterReview}
                      <footer className="stage-actions">
                        <div>
                          <strong>{blockingCount ? `还有 ${blockingCount} 项需要确认` : "可以进入下一步"}</strong>
                          <span>确认后系统才会开始人物和事件分析。</span>
                        </div>
                        <button
                          type="button"
                          disabled={blockingCount > 0 || busy === "confirm-source"}
                          onClick={() => void handleConfirmSource()}
                        >
                          {busy === "confirm-source" ? "正在确认" : "确认章节并进入下一步"}
                        </button>
                      </footer>
                    </>
                  ) : (
                    <>
                      <section className="analysis-workspace">
                        <div className="analysis-heading">
                          <div className="section-title">
                            <p>第 2 步</p>
                            <h2>识别人物和关键事件</h2>
                          </div>
                          {analysisConfigured && (
                            <div className="provider-status"><span />{analysisService?.name} · {analysisProfile?.model}</div>
                          )}
                        </div>

                        {!analysisConfigured && (
                          <div className="provider-required">
                            <div>
                              <strong>开始分析前需要连接在线 AI</strong>
                              <span>模型服务、API Key 和分析参数统一在设置中心管理。</span>
                            </div>
                            <a className="button-link" href="/settings">前往设置中心</a>
                          </div>
                        )}

                        {analysisConfigured && !analysisRun && (
                          <div className="analysis-start">
                            <div>
                              <strong>人物和事件分析尚未开始</strong>
                              <span>当前使用“{analysisProfile?.name}”，系统会按篇幅自动分批；中断后可以继续。</span>
                            </div>
                            <div className="analysis-start-actions">
                              <a className="button-link secondary-button" href="/settings">查看分析设置</a>
                              <button type="button" disabled={busy === "start-analysis"} onClick={() => void handleStartAnalysis()}>
                                {busy === "start-analysis" ? "正在准备" : "开始分析人物和事件"}
                              </button>
                            </div>
                          </div>
                        )}

                        {analysisRun && ["PENDING", "RUNNING"].includes(analysisRun.status) && (
                          <div className="analysis-running" aria-live="polite">
                            <div className="analysis-running-copy">
                              <div>
                                <strong>{analysisRun.status === "PENDING" ? "正在等待开始" : "正在阅读整本小说"}</strong>
                                <span>已完成 {analysisRun.completed_batches} / {analysisRun.total_batches} 批</span>
                              </div>
                              <b>{analysisPercent}%</b>
                            </div>
                            <div className="analysis-progress"><span style={{ width: `${analysisPercent}%` }} /></div>
                            <p>可以关闭页面，后台会继续处理；再次打开项目会恢复当前进度。</p>
                          </div>
                        )}

                        {analysisRun && analysisDiagnostics && (
                          <section className="analysis-diagnostics" aria-label="分析过程">
                            <header>
                              <div>
                                <strong>当前阶段：{analysisDiagnostics.current_step}</strong>
                                <span>本次已经调用在线 AI {analysisDiagnostics.attempt_count} 次{analysisDiagnostics.retry_count ? `，自动重试 ${analysisDiagnostics.retry_count} 次` : "，暂未发生重试"}</span>
                              </div>
                              <div className="analysis-usage">
                                <span>输入令牌约 {formatNumber(analysisDiagnostics.prompt_tokens)}</span>
                                <span>输出令牌约 {formatNumber(analysisDiagnostics.completion_tokens)}</span>
                              </div>
                            </header>
                            <div className="analysis-stage-list">
                              {analysisDiagnostics.stages.map((stage, index) => (
                                <article className={stage.status.toLowerCase()} key={stage.key}>
                                  <b>{index + 1}</b>
                                  <div>
                                    <strong>{stage.label}</strong>
                                    <span>{ANALYSIS_STAGE_STATUS_LABELS[stage.status] ?? stage.status}{stage.attempt_count ? ` · ${stage.attempt_count} 次调用` : ""}</span>
                                    {stage.latest_error && <small>{stage.latest_error}</small>}
                                  </div>
                                </article>
                              ))}
                            </div>
                          </section>
                        )}

                        {analysisRun?.status === "FAILED" && (
                          <div className="analysis-failed" role="alert">
                            <div>
                              <strong>这次分析没有完成</strong>
                              <span>{analysisRun.failure_message || "系统已停止当前批次，原文和已确认章节不会受到影响。"}</span>
                            </div>
                            <div className="analysis-start-actions">
                              <a className="button-link secondary-button" href="/settings">检查在线 AI 设置</a>
                              <button type="button" disabled={busy === "start-analysis"} onClick={() => void handleStartAnalysis()}>
                                {busy === "start-analysis" ? "正在准备" : "重新开始分析"}
                              </button>
                            </div>
                          </div>
                        )}

                        {analysisRun && ["REVIEW", "CONFIRMED"].includes(analysisRun.status) && workbench && (
                          <FormalWorkbench
                            data={workbench}
                            analysisStatus={analysisRun.status}
                            view={workbenchView}
                            onViewChange={setWorkbenchView}
                            evidenceContext={evidenceContext}
                            onOpenEvidence={(evidenceId) => void handleOpenEvidence(evidenceId)}
                            onCloseEvidence={() => setEvidenceContext(null)}
                            sourceChapters={chapters}
                            selectedChapterId={selectedChapter}
                            chapterContent={chapterContent}
                            onSelectChapter={setSelectedChapter}
                            busy={busy}
                            onAnalysisRunChange={(run) => void loadAnalysisResults(run)}
                          />
                        )}

                      </section>

                      {!workbench && <details className="source-reference">
                        <summary>查看已确认的章节与整章原文</summary>
                        {chapterReview}
                      </details>}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
