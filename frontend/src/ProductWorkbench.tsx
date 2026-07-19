import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AnalysisRun,
  api,
  EntityCandidate,
  EventCandidate,
  EvidenceContext,
  ModelSettings,
  Project,
  SourceIssue,
  SourceUnit,
  SourceUnitContent,
  SourceVersion,
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

const ENTITY_TYPES: Array<EntityCandidate["entity_type"] | "ALL"> = [
  "ALL",
  "PERSON",
  "ORGANIZATION",
  "PLACE",
  "OBJECT",
  "OTHER",
];

const EVENT_TYPES = ["ALL", "ACTION", "DISCOVERY", "CONFLICT", "DECISION", "STATE_CHANGE", "OTHER"];

function confidenceLabel(value: number) {
  if (value >= 85) return "依据较充分";
  if (value >= 65) return "依据一般";
  return "建议抽查";
}

function normalizedReferenceName(value: string) {
  return value.replace(/[【】\[\]()（）\s]/g, "").toLocaleLowerCase();
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
  const [entities, setEntities] = useState<EntityCandidate[]>([]);
  const [events, setEvents] = useState<EventCandidate[]>([]);
  const [analysisView, setAnalysisView] = useState<"entities" | "events">("entities");
  const [entityFilter, setEntityFilter] = useState<EntityCandidate["entity_type"] | "ALL">("ALL");
  const [eventFilter, setEventFilter] = useState("ALL");
  const [resultSearch, setResultSearch] = useState("");
  const [evidenceContext, setEvidenceContext] = useState<EvidenceContext | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadAnalysisResults = useCallback(async (run: AnalysisRun | null) => {
    setAnalysisRun(run);
    setEvidenceContext(null);
    if (!run || !["REVIEW", "CONFIRMED"].includes(run.status)) {
      setEntities([]);
      setEvents([]);
      return;
    }
    const [entityResult, eventResult] = await Promise.all([
      api.analysisEntities(run.id),
      api.analysisEvents(run.id),
    ]);
    setEntities(entityResult);
    setEvents(eventResult);
  }, []);

  const loadProject = useCallback(async (projectId: string) => {
    setVersions([]);
    setActiveVersion(null);
    setChapters([]);
    setIssues([]);
    setSelectedChapter("");
    setChapterContent(null);
    setAnalysisRun(null);
    setEntities([]);
    setEvents([]);
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

  const evidenceParts = evidenceContext
    ? evidenceContext.context_text.split(evidenceContext.evidence.text_snapshot)
    : [];

  const normalizedSearch = resultSearch.trim().toLocaleLowerCase();
  const filteredEntities = entities.filter((entity) => {
    const matchesType = entityFilter === "ALL" || entity.entity_type === entityFilter;
    const searchText = [entity.name, entity.description, ...entity.aliases].join(" ").toLocaleLowerCase();
    return matchesType && (!normalizedSearch || searchText.includes(normalizedSearch));
  });
  const filteredEvents = events.filter((event) => {
    const matchesType = eventFilter === "ALL" || event.event_type === eventFilter;
    const searchText = [event.title, event.summary, ...event.participants].join(" ").toLocaleLowerCase();
    return matchesType && (!normalizedSearch || searchText.includes(normalizedSearch));
  });
  const entityCounts = entities.reduce<Record<string, number>>((counts, entity) => {
    counts[entity.entity_type] = (counts[entity.entity_type] ?? 0) + 1;
    return counts;
  }, {});
  const eventCounts = events.reduce<Record<string, number>>((counts, event) => {
    counts[event.event_type] = (counts[event.event_type] ?? 0) + 1;
    return counts;
  }, {});
  const personReferenceNames = useMemo(() => new Set(
    entities
      .filter((entity) => entity.entity_type === "PERSON")
      .flatMap((entity) => [entity.name, ...entity.aliases])
      .map(normalizedReferenceName),
  ), [entities]);

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

                        {analysisRun && ["REVIEW", "CONFIRMED"].includes(analysisRun.status) && (
                          <div className="analysis-results">
                            <div className="result-summary">
                              <div><span>实体候选</span><strong>{entities.length}</strong></div>
                              <div><span>事件候选</span><strong>{events.length}</strong></div>
                              <div><span>原文证据</span><strong>{entities.reduce((sum, item) => sum + item.evidence_ids.length, 0) + events.reduce((sum, item) => sum + item.evidence_ids.length, 0)}</strong></div>
                              <div><span>整理状态</span><strong>候选整理中</strong></div>
                            </div>

                            <div className="result-notice">
                              <strong>这是自动提取的候选，不是最终人物表或剧情表。</strong>
                              <span>系统正在继续补人物归一、剧情阶段和跨章事件。现在只适合浏览和回查原文，不需要你确认这批结果。</span>
                            </div>

                            <div className="analysis-tabs" role="tablist" aria-label="分析结果">
                              <button type="button" role="tab" aria-selected={analysisView === "entities"} className={analysisView === "entities" ? "active" : ""} onClick={() => setAnalysisView("entities")}>
                                实体候选 <span>{entities.length}</span>
                              </button>
                              <button type="button" role="tab" aria-selected={analysisView === "events"} className={analysisView === "events" ? "active" : ""} onClick={() => setAnalysisView("events")}>
                                事件候选 <span>{events.length}</span>
                              </button>
                            </div>

                            <div className="result-browser">
                              <div className="result-list">
                                <div className="result-toolbar">
                                  <input
                                    aria-label="搜索当前结果"
                                    value={resultSearch}
                                    onChange={(event) => setResultSearch(event.target.value)}
                                    placeholder={analysisView === "entities" ? "搜索名称、别名或说明" : "搜索事件、摘要或参与人物"}
                                  />
                                  <div className="result-filters" role="group" aria-label="结果分类">
                                    {analysisView === "entities" ? ENTITY_TYPES.map((type) => (
                                      <button type="button" key={type} className={entityFilter === type ? "active" : ""} onClick={() => setEntityFilter(type)}>
                                        {type === "ALL" ? "全部" : ENTITY_LABELS[type]} <span>{type === "ALL" ? entities.length : entityCounts[type] ?? 0}</span>
                                      </button>
                                    )) : EVENT_TYPES.map((type) => (
                                      <button type="button" key={type} className={eventFilter === type ? "active" : ""} onClick={() => setEventFilter(type)}>
                                        {type === "ALL" ? "全部" : EVENT_LABELS[type]} <span>{type === "ALL" ? events.length : eventCounts[type] ?? 0}</span>
                                      </button>
                                    ))}
                                  </div>
                                </div>
                                {analysisView === "entities" && filteredEntities.map((entity) => (
                                  <article className="result-item" key={entity.id}>
                                    <header>
                                      <div><span>{ENTITY_LABELS[entity.entity_type]}</span><h3>{entity.name}</h3></div>
                                      <i className={entity.status === "UNCERTAIN" ? "needs-review" : ""}>{entity.status === "UNCERTAIN" ? "建议抽查" : confidenceLabel(entity.confidence)}</i>
                                    </header>
                                    <p>{entity.description || "原文中已识别到该对象。"}</p>
                                    {entity.aliases.length > 0 && <small>其他称呼：{entity.aliases.join("、")}</small>}
                                    <small>原文依据 {entity.evidence_ids.length} 处 · 置信度 {entity.confidence}%</small>
                                    <div className="evidence-buttons">
                                      {entity.evidence_ids.map((evidenceId, index) => (
                                        <button type="button" className="secondary-button" key={evidenceId} disabled={busy === `evidence-${evidenceId}`} onClick={() => void handleOpenEvidence(evidenceId)}>
                                          {busy === `evidence-${evidenceId}` ? "正在打开" : `查看原文${entity.evidence_ids.length > 1 ? ` ${index + 1}` : ""}`}
                                        </button>
                                      ))}
                                    </div>
                                  </article>
                                ))}
                                {analysisView === "events" && filteredEvents.map((event, index) => {
                                  const people = event.participants.filter((item) => personReferenceNames.has(normalizedReferenceName(item)));
                                  const relatedItems = event.participants.filter((item) => !personReferenceNames.has(normalizedReferenceName(item)));
                                  return (
                                    <article className="result-item event-item" key={event.id}>
                                      <header>
                                        <div><span>{EVENT_LABELS[event.event_type] ?? "事件"}</span><h3>{event.title}</h3></div>
                                        <i className={event.status === "UNCERTAIN" ? "needs-review" : ""}>{event.status === "UNCERTAIN" ? "建议抽查" : confidenceLabel(event.confidence)}</i>
                                      </header>
                                      <p>{event.summary}</p>
                                      {people.length > 0 && <small>参与人物：{people.join("、")}</small>}
                                      {relatedItems.length > 0 && <small>相关地点、组织或事物：{relatedItems.join("、")}</small>}
                                      <small>当前批次顺序 {index + 1} · 原文依据 {event.evidence_ids.length} 处 · 置信度 {event.confidence}%</small>
                                      <div className="evidence-buttons">
                                        {event.evidence_ids.map((evidenceId, evidenceIndex) => (
                                          <button type="button" className="secondary-button" key={evidenceId} disabled={busy === `evidence-${evidenceId}`} onClick={() => void handleOpenEvidence(evidenceId)}>
                                            {busy === `evidence-${evidenceId}` ? "正在打开" : `查看原文${event.evidence_ids.length > 1 ? ` ${evidenceIndex + 1}` : ""}`}
                                          </button>
                                        ))}
                                      </div>
                                    </article>
                                  );
                                })}
                                {analysisView === "entities" && !filteredEntities.length && <p className="result-empty">没有符合当前分类或搜索条件的实体候选。</p>}
                                {analysisView === "events" && !filteredEvents.length && <p className="result-empty">没有符合当前分类或搜索条件的事件候选。</p>}
                              </div>

                              <article className={`evidence-reader ${evidenceContext ? "open" : ""}`}>
                                {evidenceContext ? (
                                  <>
                                    <header>
                                      <div><p>原文证据</p><h3>{evidenceContext.chapter_title}</h3></div>
                                      <button type="button" className="secondary-button" onClick={() => setEvidenceContext(null)}>关闭</button>
                                    </header>
                                    <div className="evidence-text">
                                      {evidenceParts.map((part, index) => (
                                        <span key={`${part}-${index}`}>
                                          {part}
                                          {index < evidenceParts.length - 1 && <mark>{evidenceContext.evidence.text_snapshot}</mark>}
                                        </span>
                                      ))}
                                    </div>
                                  </>
                                ) : (
                                  <div className="evidence-empty">
                                    <strong>原文证据</strong>
                                    <span>点击任一结果的“查看原文”，这里会显示来源章节和上下文。</span>
                                  </div>
                                )}
                              </article>
                            </div>

                            <footer className="analysis-confirm analysis-internal-state">
                              <div><strong>当前阶段：内部整理中</strong><span>这批候选会继续进入人物归一、剧情阶段和跨章事件整理；完成后再安排你的第一次正式验收。</span></div>
                            </footer>
                          </div>
                        )}
                      </section>

                      <details className="source-reference">
                        <summary>查看已确认的章节与整章原文</summary>
                        {chapterReview}
                      </details>
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
