import React, { useState, useEffect, useMemo, type FormEvent } from "react";
import {
  api,
  type AnalysisIssue,
  type AnalysisRun,
  type DeepAnalysisDiff,
  type DeepAnalysisRevision,
  type DeepRevisionImpact,
  type EvidenceContext,
  type SourceUnit,
  type SourceUnitContent,
  type Workbench,
  type WorkbenchStateAtChapter,
} from "../../api";
import { CharacterList } from "../archive/CharacterList";
import { PlotView } from "../archive/PlotView";
import { EventsView } from "../archive/EventsView";
import { TimelineView } from "../archive/TimelineView";
import { FactsView } from "../archive/FactsView";
import { WorldView } from "../archive/WorldView";
import { ForeshadowingView } from "../archive/ForeshadowingView";
import { ConflictsView } from "../archive/ConflictsView";
import { PacingView } from "../archive/PacingView";
import { OverviewView } from "../archive/OverviewView";
import { IssuesView } from "../archive/IssuesView";
import { KNOWLEDGE_TRANSFER_LABELS } from "../archive/constants";

export type ArchiveTab =
  | "characters"
  | "plot"
  | "timeline"
  | "events"
  | "facts"
  | "world"
  | "foreshadowing"
  | "conflicts"
  | "pacing"
  | "overview"
  | "issues";

interface ArchiveViewProps {
  workbench: Workbench;
  analysisStatus: AnalysisRun["status"];
  evidenceContext: EvidenceContext | null;
  onOpenEvidence: (evidenceId: string) => void;
  onCloseEvidence: () => void;
  sourceChapters: SourceUnit[];
  selectedChapterId: string;
  chapterContent: SourceUnitContent | null;
  onSelectChapter: (chapterId: string) => void;
  busy: string;
  onAnalysisRunChange: (run: AnalysisRun) => void;
  onWorkbenchChange: (workbench: Workbench) => void;
  onRepairNarrative: () => void;
  onStartDeepAnalysis: () => void;
  onStartCharacterDesign: (force: boolean) => void;
  onStartChapterEndHooks: (force: boolean) => void;
  onStartOpeningHookPayoffs: (force: boolean) => void;
  onStartOpeningStructure: (force: boolean) => void;
  onStartLearningReport: () => void;
  onConfirmAnalysis: () => void;
}

const ARCHIVE_TABS: Array<{ key: ArchiveTab; label: string; icon: string }> = [
  { key: "characters", label: "人物档案", icon: "👤" },
  { key: "plot", label: "剧情阶段", icon: "🗺️" },
  { key: "timeline", label: "事件时间线", icon: "⏳" },
  { key: "events", label: "关键事件", icon: "⚡" },
  { key: "facts", label: "事实状态", icon: "📋" },
  { key: "world", label: "世界设定", icon: "🌐" },
  { key: "foreshadowing", label: "伏笔网络", icon: "🔮" },
  { key: "conflicts", label: "核心冲突", icon: "⚔️" },
  { key: "pacing", label: "叙事节奏", icon: "📈" },
  { key: "overview", label: "故事总览", icon: "📖" },
  { key: "issues", label: "修正与问题", icon: "🛠️" },
];

export function ArchiveView({
  workbench: data,
  analysisStatus,
  evidenceContext,
  onOpenEvidence,
  onCloseEvidence,
  sourceChapters,
  selectedChapterId,
  chapterContent,
  onSelectChapter,
  busy,
  onAnalysisRunChange,
  onWorkbenchChange,
  onRepairNarrative,
  onStartDeepAnalysis,
  onStartCharacterDesign,
  onStartChapterEndHooks,
  onStartOpeningHookPayoffs,
  onStartOpeningStructure,
  onStartLearningReport,
  onConfirmAnalysis,
}: ArchiveViewProps) {
  const [activeTab, setActiveTab] = useState<ArchiveTab>("characters");
  const [searchQuery, setSearchQuery] = useState("");
  const [stateChapter, setStateChapter] = useState(data.chapters.at(-1)?.ordinal ?? 1);
  const [issues, setIssues] = useState<AnalysisIssue[]>([]);
  const [revisions, setRevisions] = useState<DeepAnalysisRevision[]>([]);
  const [revisionDiff, setRevisionDiff] = useState<DeepAnalysisDiff | null>(null);
  const [revisionImpact, setRevisionImpact] = useState<DeepRevisionImpact | null>(null);
  const [revisionData, setRevisionData] = useState<Workbench | null>(null);
  const [stateProjection, setStateProjection] = useState<WorkbenchStateAtChapter | null>(null);
  const [stateProjectionBusy, setStateProjectionBusy] = useState(false);
  const [stateProjectionError, setStateProjectionError] = useState("");
  const [revisionBusy, setRevisionBusy] = useState(false);
  const [revisionError, setRevisionError] = useState("");
  const [issueTarget, setIssueTarget] = useState<{
    kind: string;
    id: string | null;
    label: string;
  } | null>(null);
  const [issueCategory, setIssueCategory] = useState("INCORRECT");
  const [issueNote, setIssueNote] = useState("");
  const [issueBusy, setIssueBusy] = useState("");
  const [issueError, setIssueError] = useState("");
  const [identityBusy, setIdentityBusy] = useState("");
  const [identityError, setIdentityError] = useState("");
  const [focusTarget, setFocusTarget] = useState<{ view: string; id: string } | null>(null);

  const viewData = revisionData ?? data;
  const isHistoricalRevision =
    revisionData !== null && revisionData.deep_revision !== data.deep_revision;

  useEffect(() => {
    setSearchQuery("");
    setStateChapter(data.chapters.at(-1)?.ordinal ?? 1);
    setRevisionData(null);
    setRevisionError("");
  }, [data.run_id, data.chapters]);

  useEffect(() => {
    if (!focusTarget || focusTarget.view !== activeTab || searchQuery.trim()) return;
    const timer = window.setTimeout(() => {
      const element = document.querySelector<HTMLElement>(
        `[data-workbench-id="${focusTarget.id}"]`,
      );
      element?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 40);
    return () => window.clearTimeout(timer);
  }, [focusTarget, searchQuery, activeTab, viewData]);

  useEffect(() => {
    let active = true;
    void Promise.all([
      api.analysisIssues(data.run_id),
      api.deepAnalysisRevisions(data.run_id),
    ])
      .then(async ([nextIssues, nextRevisions]) => {
        if (!active) return;
        setIssues(nextIssues);
        setRevisions(nextRevisions);
        if (nextRevisions.length > 1) {
          const diff = await api.deepAnalysisDiff(data.run_id);
          if (active) setRevisionDiff(diff);
        } else {
          setRevisionDiff(null);
        }
      })
      .catch(() => {
        if (active) {
          setIssues([]);
          setRevisions([]);
          setRevisionDiff(null);
        }
      });
    return () => {
      active = false;
    };
  }, [data.run_id, data.deep_revision]);

  useEffect(() => {
    let active = true;
    if (!issues.some((item) => item.status === "OPEN")) {
      setRevisionImpact(null);
      return () => {
        active = false;
      };
    }
    void api
      .deepAnalysisImpact(data.run_id)
      .then((impact) => {
        if (active) setRevisionImpact(impact);
      })
      .catch(() => {
        if (active) setRevisionImpact(null);
      });
    return () => {
      active = false;
    };
  }, [data.run_id, issues]);

  useEffect(() => {
    let active = true;
    if (!viewData.deep_analysis) {
      setStateProjection(null);
      setStateProjectionError("");
      return () => {
        active = false;
      };
    }
    setStateProjectionBusy(true);
    setStateProjectionError("");
    void api
      .stateAtChapter(
        viewData.run_id,
        stateChapter,
        viewData.deep_revision ?? undefined,
      )
      .then((result) => {
        if (active) setStateProjection(result);
      })
      .catch((reason) => {
        if (active) {
          setStateProjection(null);
          setStateProjectionError(
            reason instanceof Error ? reason.message : String(reason),
          );
        }
      })
      .finally(() => {
        if (active) setStateProjectionBusy(false);
      });
    return () => {
      active = false;
    };
  }, [viewData.run_id, viewData.deep_revision, viewData.deep_analysis, stateChapter]);

  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase("zh-CN");
    if (!query) return [];
    const entries = [
      ...viewData.characters.map((item) => ({
        key: item.id,
        view: "characters" as ArchiveTab,
        section: "人物",
        title: item.name,
        text: `${item.description} ${item.role_reason} ${item.identities.join(" ")} ${item.goals.join(" ")} ${item.motivations.join(" ")} ${item.abilities.join(" ")} ${item.secrets.join(" ")} ${item.arc_summary}`,
        evidenceIds: item.evidence_ids,
      })),
      ...viewData.events.map((item) => ({
        key: item.id,
        view: "events" as ArchiveTab,
        section: "事件",
        title: item.title,
        text: `${item.summary} ${item.people.join(" ")} ${item.related_entities.join(" ")} ${item.location} ${item.trigger} ${item.process} ${item.outcome} ${item.impact}`,
        evidenceIds: item.evidence_ids,
      })),
      ...viewData.phases.map((item) => ({
        key: item.id,
        view: "plot" as ArchiveTab,
        section: "剧情阶段",
        title: item.title,
        text: `${item.situation} ${item.goal} ${item.obstacle} ${item.outcome} ${item.change}`,
        evidenceIds: item.evidence_ids,
      })),
      ...(viewData.deep_analysis?.fact_versions.map((item) => ({
        key: item.id,
        view: "facts" as ArchiveTab,
        section: "事实",
        title: item.subject,
        text: `${item.predicate} ${item.value} ${item.timeline_note}`,
        evidenceIds: item.evidence_ids,
      })) ?? []),
      ...(viewData.deep_analysis?.knowledge_transfers.map((item) => ({
        key: item.id,
        view: "facts" as ArchiveTab,
        section: "认知传播",
        title: `${item.source_actor} → ${item.target_actor}`,
        text: `${item.proposition} ${KNOWLEDGE_TRANSFER_LABELS[item.transfer_type] ?? "信息传播"}`,
        evidenceIds: item.evidence_ids,
      })) ?? []),
      ...(viewData.deep_analysis?.world_rules.map((item) => ({
        key: item.id,
        view: "world" as ArchiveTab,
        section: "世界设定",
        title: item.title,
        text: `${item.description} ${item.limitations.join(" ")} ${item.costs.join(" ")}`,
        evidenceIds: item.evidence_ids,
      })) ?? []),
      ...(viewData.deep_analysis?.foreshadowing.map((item) => ({
        key: item.id,
        view: "foreshadowing" as ArchiveTab,
        section: "伏笔",
        title: item.title,
        text: item.setup,
        evidenceIds: item.evidence_ids,
      })) ?? []),
      ...(viewData.deep_analysis?.conflicts.map((item) => ({
        key: item.id,
        view: "conflicts" as ArchiveTab,
        section: "冲突",
        title: item.title,
        text: `${item.goals} ${item.obstacles} ${item.stakes} ${item.resolution}`,
        evidenceIds: item.evidence_ids,
      })) ?? []),
      ...(viewData.deep_analysis?.claims.map((item) => ({
        key: item.id,
        view: "overview" as ArchiveTab,
        section: "分析结论",
        title: item.claim_text,
        text: item.scope,
        evidenceIds: item.evidence_ids,
      })) ?? []),
    ];
    return entries.filter((item) =>
      `${item.title} ${item.text}`.toLocaleLowerCase("zh-CN").includes(query),
    );
  }, [viewData, searchQuery]);

  async function selectRevision(revisionNo: number) {
    if (revisionNo === data.deep_revision) {
      setRevisionData(null);
      return;
    }
    try {
      setRevisionBusy(true);
      setRevisionError("");
      const nextWorkbench = await api.analysisWorkbench(data.run_id, revisionNo);
      setRevisionData(nextWorkbench);
    } catch (reason) {
      setRevisionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRevisionBusy(false);
    }
  }

  async function decidePersonIdentity(
    pairKey: string,
    decision: "SAME" | "DIFFERENT",
  ) {
    try {
      setIdentityBusy(pairKey);
      setIdentityError("");
      setRevisionData(null);
      onWorkbenchChange(
        await api.decidePersonIdentity(data.run_id, pairKey, decision),
      );
    } catch (reason) {
      setIdentityError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setIdentityBusy("");
    }
  }

  async function undoPersonIdentityDecision(decisionId: string) {
    try {
      setIdentityBusy(decisionId);
      setIdentityError("");
      setRevisionData(null);
      onWorkbenchChange(await api.undoPersonIdentityDecision(decisionId));
    } catch (reason) {
      setIdentityError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setIdentityBusy("");
    }
  }

  async function submitIssue(event: FormEvent) {
    event.preventDefault();
    if (!issueTarget || !issueNote.trim()) return;
    try {
      setIssueBusy("create");
      setIssueError("");
      const issue = await api.createAnalysisIssue(data.run_id, {
        target_kind: issueTarget.kind,
        target_id: issueTarget.id,
        target_label: issueTarget.label,
        category: issueCategory,
        note: issueNote.trim(),
      });
      setIssues((prev) => [issue, ...prev]);
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
      setIssues((prev) =>
        prev.map((item) => (item.id === issueId ? resolved : item)),
      );
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

  const openWorkbenchItem = (tab: ArchiveTab, key: string) => {
    setSearchQuery("");
    setActiveTab(tab);
    setFocusTarget({ view: tab, id: key });
  };

  const handleMarkProblem = (kind: string, id: string | null, label: string) => {
    setIssueTarget({ kind, id, label });
    setActiveTab("issues");
  };

  return (
    <div className="archive-view-container formal-workbench">
      {/* Archive Header Strip */}
      <header className="view-header-strip archive-header-strip">
        <div className="view-header-main">
          <h2>故事结构档案</h2>
          <p className="view-header-desc">
            全书角色、情节、事件、世界规则与叙事节奏的细粒度证据数据库。
          </p>
        </div>
        <div className="formal-workbench-counts">
          <div>
            <strong>{viewData.characters.length}</strong>
            <span>人物证据</span>
          </div>
          <div>
            <strong>{viewData.events.length}</strong>
            <span>事件证据</span>
          </div>
          <div>
            <strong>{viewData.phases.length}</strong>
            <span>剧情阶段</span>
          </div>
          <div>
            <strong>
              {viewData.deep_analysis
                ? viewData.deep_analysis.fact_versions.length
                : "未生成"}
            </strong>
            <span>深层事实</span>
          </div>
        </div>
      </header>

      {/* Revision Toolbar */}
      {revisions.length > 0 && (
        <div className="revision-toolbar">
          <div>
            <strong>
              {isHistoricalRevision
                ? `正在查看第 ${viewData.deep_revision} 版`
                : `当前第 ${data.deep_revision} 版`}
            </strong>
            <span>
              {isHistoricalRevision
                ? "这是以前保存的结果，只能查看和比较，不会覆盖当前版本。"
                : "问题修正后会保存新版本，旧结果不会丢失。"}
            </span>
          </div>
          <label htmlFor="deep-revision-select">拆解版本</label>
          <select
            id="deep-revision-select"
            value={viewData.deep_revision ?? ""}
            disabled={revisionBusy}
            onChange={(event) => void selectRevision(Number(event.target.value))}
          >
            {revisions.map((revision) => (
              <option value={revision.revision_no} key={revision.revision_no}>
                第 {revision.revision_no} 版
                {revision.revision_no === data.deep_revision ? "（当前）" : ""}
              </option>
            ))}
          </select>
          {revisionBusy && <span>正在读取版本</span>}
        </div>
      )}

      {revisionDiff && revisions.length > 1 && (
        <details className="revision-audit">
          <summary>
            <span>查看上一版到当前版的具体变化</span>
            <small>
              新增 {Object.values(revisionDiff.added).flat().length} 项，修改{" "}
              {Object.values(revisionDiff.changed_counts).reduce((a, b) => a + b, 0)} 项，删除{" "}
              {Object.values(revisionDiff.removed).flat().length} 项
            </small>
          </summary>
          <div className="revision-audit-groups">
            {Object.entries(revisionDiff.added).map(([key, items]) =>
              items.length > 0 ? (
                <section key={`added-${key}`}>
                  <h3>新增 {key}</h3>
                  {items.map((item) => (
                    <p className="added" key={`added-${key}-${item}`}>
                      <strong>新增</strong>
                      <span>{item}</span>
                    </p>
                  ))}
                </section>
              ) : null,
            )}
            {Object.entries(revisionDiff.changed).map(([key, items]) =>
              items.length > 0 ? (
                <section key={`changed-${key}`}>
                  <h3>修改 {key}</h3>
                  {items.map((item) => (
                    <p className="changed" key={`changed-${key}-${item}`}>
                      <strong>修改</strong>
                      <span>{item}</span>
                    </p>
                  ))}
                </section>
              ) : null,
            )}
            {Object.entries(revisionDiff.removed).map(([key, items]) =>
              items.length > 0 ? (
                <section key={`removed-${key}`}>
                  <h3>删除 {key}</h3>
                  {items.map((item) => (
                    <p className="removed" key={`removed-${key}-${item}`}>
                      <strong>删除</strong>
                      <span>{item}</span>
                    </p>
                  ))}
                </section>
              ) : null,
            )}
          </div>
        </details>
      )}

      {revisionError && (
        <div className="analysis-issue-error" role="alert">
          {revisionError}
        </div>
      )}

      {/* Segmented Control Bar */}
      <nav className="archive-segment-nav" aria-label="档案分类">
        {ARCHIVE_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`archive-segment-btn ${activeTab === tab.key ? "active" : ""}`}
            onClick={() => {
              setSearchQuery("");
              setActiveTab(tab.key);
            }}
          >
            <span className="segment-icon">{tab.icon}</span>
            <span className="segment-label">{tab.label}</span>
          </button>
        ))}
      </nav>

      {/* Search Input */}
      <div className="workbench-search">
        <label htmlFor="workbench-search-input">搜索证据资料库</label>
        <input
          id="workbench-search-input"
          type="search"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          placeholder="搜索人物、事件、设定、伏笔或分析结论"
        />
        {searchQuery.trim() && <span>找到 {searchResults.length} 项</span>}
      </div>

      {/* Main Content Body */}
      <div className="archive-view-body reading-wide">
        <div className="formal-workbench-content">
          {searchQuery.trim() ? (
            <section className="search-results">
              <header>
                <h3>搜索结果</h3>
                <span>{searchResults.length} 项</span>
              </header>
              {searchResults.map((item) => (
                <article key={`${item.section}-${item.key}`}>
                  <span>{item.section}</span>
                  <h4>{item.title}</h4>
                  <p>{item.text}</p>
                  <button
                    type="button"
                    className="text-action"
                    onClick={() => openWorkbenchItem(item.view, item.key)}
                  >
                    打开这项内容
                  </button>
                  {item.evidenceIds.length > 0 && (
                    <div className="card-evidence-actions">
                      {item.evidenceIds.map((id) => (
                        <button
                          key={id}
                          type="button"
                          className="evidence-chip-button"
                          onClick={() => onOpenEvidence(id)}
                        >
                          查看原文依据
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              ))}
              {!searchResults.length && (
                <p className="result-empty">
                  没有找到相关内容，可以换一个人物名、地点或情节关键词。
                </p>
              )}
            </section>
          ) : (
            <>
              {activeTab === "characters" && (
                <CharacterList
                  viewData={viewData}
                  analysisStatus={analysisStatus}
                  isHistoricalRevision={isHistoricalRevision}
                  busy={busy}
                  identityBusy={identityBusy}
                  identityError={identityError}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                  onNavigateItem={(view, id) => openWorkbenchItem(view as ArchiveTab, id)}
                  onDecidePersonIdentity={(key, decision) =>
                    void decidePersonIdentity(key, decision)
                  }
                  onUndoPersonIdentityDecision={(id) =>
                    void undoPersonIdentityDecision(id)
                  }
                  onStartCharacterDesign={onStartCharacterDesign}
                />
              )}

              {activeTab === "plot" && (
                <PlotView
                  viewData={viewData}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                  onNavigateItem={(view, id) => openWorkbenchItem(view as ArchiveTab, id)}
                />
              )}

              {activeTab === "events" && (
                <EventsView
                  viewData={viewData}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                  onNavigateItem={(view, id) => openWorkbenchItem(view as ArchiveTab, id)}
                />
              )}

              {activeTab === "timeline" && (
                <TimelineView
                  viewData={viewData}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                  onNavigateItem={(view, id) => openWorkbenchItem(view as ArchiveTab, id)}
                />
              )}

              {activeTab === "facts" && (
                <FactsView
                  viewData={viewData}
                  stateChapter={stateChapter}
                  onStateChapterChange={setStateChapter}
                  stateProjection={stateProjection}
                  stateProjectionBusy={stateProjectionBusy}
                  stateProjectionError={stateProjectionError}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                />
              )}

              {activeTab === "world" && (
                <WorldView
                  viewData={viewData}
                  stateChapter={stateChapter}
                  onStateChapterChange={setStateChapter}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                />
              )}

              {activeTab === "foreshadowing" && (
                <ForeshadowingView
                  viewData={viewData}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                />
              )}

              {activeTab === "conflicts" && (
                <ConflictsView
                  viewData={viewData}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                />
              )}

              {activeTab === "pacing" && (
                <PacingView
                  viewData={viewData}
                  isHistoricalRevision={isHistoricalRevision}
                  busy={busy}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                  onStartChapterEndHooks={onStartChapterEndHooks}
                />
              )}

              {activeTab === "overview" && (
                <OverviewView
                  viewData={viewData}
                  focusTarget={focusTarget}
                  onOpenEvidence={onOpenEvidence}
                  onMarkProblem={handleMarkProblem}
                />
              )}

              {activeTab === "issues" && (
                <IssuesView
                  issues={issues}
                  issueError={issueError}
                  issueBusy={issueBusy}
                  issueTarget={issueTarget}
                  issueCategory={issueCategory}
                  issueNote={issueNote}
                  revisionImpact={revisionImpact}
                  revisions={revisions}
                  revisionDiff={revisionDiff}
                  currentRevision={data.deep_revision}
                  isHistoricalRevision={isHistoricalRevision}
                  onSetIssueCategory={setIssueCategory}
                  onSetIssueNote={setIssueNote}
                  onCancelIssue={() => setIssueTarget(null)}
                  onSubmitIssue={(e) => void submitIssue(e)}
                  onResolveIssue={(id) => void resolveIssue(id)}
                  onRecomputeFromIssues={() => void recomputeFromIssues()}
                />
              )}
            </>
          )}
        </div>
      </div>

      <footer className="formal-workbench-footer">
        {isHistoricalRevision ? (
          <div>
            <strong>正在查看以前的拆解版本</strong>
            <span>切换回标有“当前”的版本后，才能继续标记问题和重新分析。</span>
          </div>
        ) : analysisStatus === "CONFIRMED" ? (
          <div>
            <strong>当前拆解结果已经确认</strong>
            <span>人物、剧情、事实状态和核心分析均已保存，可以继续回查原文。</span>
          </div>
        ) : (
          <div>
            <strong>拆解结果可随时审查与复核</strong>
            <span>
              每个版块均由真实原文段落为依据。如需更正，请在相应卡片点击“标记问题”。
            </span>
          </div>
        )}
      </footer>
    </div>
  );
}
