import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
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
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadProject = useCallback(async (projectId: string) => {
    setVersions([]);
    setActiveVersion(null);
    setChapters([]);
    setIssues([]);
    setSelectedChapter("");
    setChapterContent(null);
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
        setSelectedChapter(sourceChapters[0]?.id ?? "");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  const loadProjects = useCallback(async () => {
    try {
      const [healthResult, projectResult] = await Promise.all([
        api.health(),
        api.projects(),
      ]);
      setHealth(healthResult.status);
      setProjects(projectResult);
      setSelectedProject((current) => current || projectResult[0]?.id || "");
      setError("");
    } catch (reason) {
      setHealth("offline");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void loadProjects();
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

  const activeProject = projects.find((project) => project.id === selectedProject) ?? null;
  const openIssues = issues.filter((issue) => issue.status === "OPEN");
  const blockingCount = openIssues.filter((issue) => issue.severity === "BLOCKING").length;
  const chapterCount = chapters.filter((chapter) => chapter.unit_type === "CHAPTER").length;
  const otherUnitCount = chapters.length - chapterCount;
  const currentStage = activeVersion?.status === "CONFIRMED" ? 1 : 0;
  const chapterIssueMap = useMemo(() => {
    const counts = new Map<string, number>();
    for (const issue of openIssues) {
      if (issue.source_unit_id) {
        counts.set(issue.source_unit_id, (counts.get(issue.source_unit_id) ?? 0) + 1);
      }
    }
    return counts;
  }, [openIssues]);

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
      setSelectedChapter(imported.units[0]?.id ?? "");
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

  return (
    <div className="product-shell">
      <header className="workbench-topbar">
        <div>
          <p className="product-kicker">AI 小说拆解工作台</p>
          <h1>{activeProject?.name ?? "我的小说项目"}</h1>
        </div>
        <div className={`api-status ${health}`}>
          <span className="status-dot" />
          {health === "ok" ? "系统正常" : health === "offline" ? "系统未连接" : "正在连接"}
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
                      <div><span>正文字符</span><strong>{formatNumber(activeVersion.total_chars)}</strong></div>
                      <div><span>识别章节</span><strong>{activeVersion.chapter_count}</strong></div>
                      <div><span>需要确认</span><strong>{blockingCount}</strong></div>
                      <div><span>导入版本</span><strong>第 {activeVersion.version_no} 版</strong></div>
                    </div>
                  </section>

                  {openIssues.length > 0 && (
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

                  <section className="chapter-review">
                    <div className="chapter-list" aria-label="章节列表">
                      <div className="chapter-list-heading">
                        <h2>章节目录</h2>
                        <span>{chapterCount} 章{otherUnitCount ? ` · ${otherUnitCount} 项前置内容` : ""}</span>
                      </div>
                      <div className="chapter-scroll">
                        {chapters.map((chapter) => (
                          <button
                            type="button"
                            key={chapter.id}
                            className={selectedChapter === chapter.id ? "active" : ""}
                            onClick={() => setSelectedChapter(chapter.id)}
                          >
                            <span>{chapter.ordinal}</span>
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

                  <footer className="stage-actions">
                    {activeVersion.status === "CONFIRMED" ? (
                      <div className="confirmed-message">
                        <strong>导入与章节检查已完成</strong>
                        <span>下一步将识别人物、剧情和关键事件。</span>
                      </div>
                    ) : (
                      <>
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
                      </>
                    )}
                  </footer>
                </>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
