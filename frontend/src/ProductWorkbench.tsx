import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AnalysisRun,
  api,
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

type FormalWorkbenchProps = {
  data: Workbench;
  analysisStatus: AnalysisRun["status"];
  view: "overview" | "characters" | "plot" | "events";
  onViewChange: (view: "overview" | "characters" | "plot" | "events") => void;
  evidenceContext: EvidenceContext | null;
  onOpenEvidence: (evidenceId: string) => void;
  onCloseEvidence: () => void;
  busy: string;
  onConfirm: () => void;
}

function FormalWorkbench({
  data,
  analysisStatus,
  view,
  onViewChange,
  evidenceContext,
  onOpenEvidence,
  onCloseEvidence,
  busy,
  onConfirm,
}: FormalWorkbenchProps) {
  const evidenceParts = evidenceContext
    ? evidenceContext.context_text.split(evidenceContext.evidence.text_snapshot)
    : [];
  const evidenceButtons = (evidenceIds: string[]) => (
    <div className="evidence-buttons">
      {evidenceIds.map((evidenceId, index) => (
        <button type="button" className="secondary-button" key={evidenceId} disabled={busy === `evidence-${evidenceId}`} onClick={() => onOpenEvidence(evidenceId)}>
          {busy === `evidence-${evidenceId}` ? "正在打开" : `查看原文${evidenceIds.length > 1 ? ` ${index + 1}` : ""}`}
        </button>
      ))}
    </div>
  );

  return (
    <section className="formal-workbench" aria-label="人物剧情事件工作台">
      <div className="formal-workbench-heading">
        <div>
          <p>第 2 步已完成自动整理</p>
          <h2>人物、剧情与事件</h2>
          <span>下面是根据原文证据整理出的可浏览结果。每项内容都能回到来源章节。</span>
        </div>
        <div className="formal-workbench-counts">
          <div><strong>{data.characters.length}</strong><span>人物</span></div>
          <div><strong>{data.events.length}</strong><span>事件</span></div>
          <div><strong>{data.phases.length}</strong><span>剧情阶段</span></div>
        </div>
      </div>

      <nav className="formal-workbench-tabs" aria-label="工作台版块">
        {([
          ["overview", "总览"],
          ["characters", "人物"],
          ["plot", "剧情阶段"],
          ["events", "事件时间线"],
        ] as const).map(([key, label]) => (
          <button type="button" key={key} className={view === key ? "active" : ""} onClick={() => onViewChange(key)}>
            {label}
            <span>{key === "characters" ? data.characters.length : key === "plot" ? data.phases.length : key === "events" ? data.events.length : ""}</span>
          </button>
        ))}
      </nav>

      <div className="formal-workbench-body">
        <div className="formal-workbench-content">
          {view === "overview" && (
            <>
              <div className="workbench-callout">
                <strong>这一步已经从“候选平铺”升级为可浏览的正式投影。</strong>
                <span>人物保留别名和出现范围；事件按时间排序；剧情阶段只由带章节位置的事件组成。语义上仍有争议的结果会保留“待抽查”，不会被悄悄合并。</span>
              </div>
              <div className="phase-overview-list">
                {data.phases.map((phase, index) => (
                  <article className="phase-card" key={phase.id}>
                    <header><span>阶段 {index + 1}</span><h3>{phase.title}</h3></header>
                    <p>{phase.summary}</p>
                    {phase.people.length > 0 && <small>参与人物：{phase.people.join("、")}</small>}
                    {evidenceButtons(phase.evidence_ids)}
                  </article>
                ))}
                {!data.phases.length && <p className="result-empty">当前没有足够的事件组成剧情阶段。</p>}
              </div>
              <div className="related-entity-summary">
                <header><h3>地点、组织与重要事物</h3><span>{data.related_entities.length} 个</span></header>
                <div>{data.related_entities.map((entity) => <span key={entity.id}>{ENTITY_LABELS[entity.entity_type]}：{entity.name}</span>)}</div>
              </div>
            </>
          )}

          {view === "characters" && (
            <div className="formal-card-list">
              {data.characters.map((character) => (
                <article className="formal-card" key={character.id}>
                  <header>
                    <div><span>人物</span><h3>{character.name}</h3></div>
                    <i className={character.status === "UNCERTAIN" ? "needs-review" : ""}>{character.status === "UNCERTAIN" ? "待抽查" : `置信度 ${character.confidence}%`}</i>
                  </header>
                  <p>{character.description || "原文中已识别到该人物。"}</p>
                  {character.aliases.length > 0 && <small>别名或称谓：{character.aliases.join("、")}</small>}
                  <div className="character-meta">
                    <span>出场活跃度：{character.activity_level}</span>
                    <span>证据 {character.appearance_count} 处</span>
                    <span>{character.first_chapter_ordinal ? `第 ${character.first_chapter_ordinal} 章首次出现` : "章节位置待定"}</span>
                    <span>{character.event_ids.length} 个相关事件</span>
                  </div>
                  {evidenceButtons(character.evidence_ids)}
                </article>
              ))}
              {!data.characters.length && <p className="result-empty">当前没有整理出人物档案。</p>}
            </div>
          )}

          {view === "plot" && (
            <div className="formal-card-list">
              {data.phases.map((phase, index) => (
                <article className="formal-card phase-detail-card" key={phase.id}>
                  <header><div><span>阶段 {index + 1}</span><h3>{phase.title}</h3></div><i>{phase.chapter_titles.join("、") || "章节待定"}</i></header>
                  <p>{phase.summary}</p>
                  <div className="phase-event-list">
                    {phase.event_ids.map((eventId) => {
                      const event = data.events.find((item) => item.id === eventId);
                      return event ? <div key={event.id}><span>{EVENT_LABELS[event.event_type] ?? "事件"}</span><strong>{event.title}</strong></div> : null;
                    })}
                  </div>
                  {evidenceButtons(phase.evidence_ids)}
                </article>
              ))}
              {!data.phases.length && <p className="result-empty">当前没有足够的事件组成剧情阶段。</p>}
            </div>
          )}

          {view === "events" && (
            <div className="formal-card-list">
              {data.events.map((event, index) => (
                <article className="formal-card event-formal-card" key={event.id}>
                  <header><div><span>{EVENT_LABELS[event.event_type] ?? "事件"}</span><h3>{event.title}</h3></div><i className={event.status === "UNCERTAIN" ? "needs-review" : ""}>{event.status === "UNCERTAIN" ? "待抽查" : `第 ${index + 1} 项`}</i></header>
                  <p>{event.summary}</p>
                  {event.people.length > 0 && <small>参与人物：{event.people.join("、")}</small>}
                  {event.related_entities.length > 0 && <small>相关地点、组织或事物：{event.related_entities.join("、")}</small>}
                  <div className="character-meta"><span>{event.chapter_titles.join("、") || "章节待定"}</span><span>原文依据 {event.evidence_ids.length} 处</span><span>置信度 {event.confidence}%</span>{event.mention_count > 1 && <span>合并 {event.mention_count} 次提及</span>}</div>
                  {evidenceButtons(event.evidence_ids)}
                </article>
              ))}
              {!data.events.length && <p className="result-empty">当前没有整理出事件时间线。</p>}
            </div>
          )}
        </div>

        <aside className={`formal-evidence-reader ${evidenceContext ? "open" : ""}`}>
          {evidenceContext ? (
            <>
              <header><div><p>原文证据</p><h3>{evidenceContext.chapter_title}</h3></div><button type="button" className="secondary-button" onClick={onCloseEvidence}>关闭</button></header>
              <div className="evidence-text">{evidenceParts.map((part, index) => <span key={`${part}-${index}`}>{part}{index < evidenceParts.length - 1 && <mark>{evidenceContext.evidence.text_snapshot}</mark>}</span>)}</div>
            </>
          ) : <div className="evidence-empty"><strong>原文证据</strong><span>点击任一人物、阶段或事件的“查看原文”，这里会显示来源章节和上下文。</span></div>}
        </aside>
      </div>

      <footer className="formal-workbench-footer">
        {analysisStatus === "CONFIRMED" ? (
          <div><strong>人物、剧情和事件阶段已确认</strong><span>后续事实与设定版块将在下一阶段继续生成。</span></div>
        ) : (
          <>
            <div><strong>现在可以进行第一次产品验收</strong><span>请重点检查人物是否可读、事件顺序是否合理、每项内容能否回到原文。</span></div>
            <button type="button" disabled={busy === "confirm-analysis"} onClick={onConfirm}>{busy === "confirm-analysis" ? "正在确认" : "确认人物、剧情和事件"}</button>
          </>
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
  const [workbench, setWorkbench] = useState<Workbench | null>(null);
  const [workbenchView, setWorkbenchView] = useState<"overview" | "characters" | "plot" | "events">("overview");
  const [evidenceContext, setEvidenceContext] = useState<EvidenceContext | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadAnalysisResults = useCallback(async (run: AnalysisRun | null) => {
    setAnalysisRun(run);
    setWorkbench(null);
    setEvidenceContext(null);
    if (!run || !["REVIEW", "CONFIRMED"].includes(run.status)) {
      return;
    }
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

  async function handleConfirmAnalysis() {
    if (!analysisRun) return;
    try {
      setBusy("confirm-analysis");
      setError("");
      await loadAnalysisResults(await api.confirmAnalysis(analysisRun.id));
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

                        {analysisRun && ["REVIEW", "CONFIRMED"].includes(analysisRun.status) && workbench && (
                          <FormalWorkbench
                            data={workbench}
                            analysisStatus={analysisRun.status}
                            view={workbenchView}
                            onViewChange={setWorkbenchView}
                            evidenceContext={evidenceContext}
                            onOpenEvidence={(evidenceId) => void handleOpenEvidence(evidenceId)}
                            onCloseEvidence={() => setEvidenceContext(null)}
                            busy={busy}
                            onConfirm={() => void handleConfirmAnalysis()}
                          />
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
