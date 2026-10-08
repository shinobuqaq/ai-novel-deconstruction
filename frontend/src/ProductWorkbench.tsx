import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import LearningHandbook from "./LearningHandbook";
import {
  AnalysisCallContent,
  AnalysisIssue,
  AnalysisUsageEstimate,
  AnalysisRun,
  AnalysisRunDiagnostics,
  api,
  DeepAnalysisDiff,
  DeepRevisionImpact,
  DeepAnalysisRevision,
  EntityCandidate,
  EvidenceContext,
  ModelSettings,
  Project,
  SourceIssue,
  SourceStructure,
  SourceUnit,
  SourceUnitContent,
  SourceVersion,
  Workbench,
  WorkbenchStateAtChapter,
} from "./api";

import { AppLayout } from "./components/layout/AppLayout";
import type { StudioTab } from "./components/layout/Sidebar";
import { LearningView } from "./components/views/LearningView";
import { ArchiveView } from "./components/views/ArchiveView";
import { SourceView } from "./components/views/SourceView";
import { AnalysisView } from "./components/views/AnalysisView";

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

function formatDuration(value: number) {
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} 小时 ${remainingMinutes} 分` : `${hours} 小时`;
}

function formatFileSize(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function sourceLineStarts(content: string) {
  const options: Array<{ offset: number; line: number; preview: string }> = [];
  let line = 1;
  let start = 0;
  while (start < content.length) {
    const newline = content.indexOf("\n", start);
    const end = newline === -1 ? content.length : newline;
    const preview = content.slice(start, end).replace(/\r$/, "").trim();
    if (start > 0 && preview) {
      options.push({
        offset: start,
        line,
        preview: preview.length > 48 ? `${preview.slice(0, 48)}…` : preview,
      });
    }
    if (newline === -1) break;
    start = newline + 1;
    line += 1;
  }
  return options;
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
  const [chapterTitleDraft, setChapterTitleDraft] = useState("");
  const [chapterTypeDraft, setChapterTypeDraft] = useState("CHAPTER");
  const [splitTitleDraft, setSplitTitleDraft] = useState("");
  const [splitTypeDraft, setSplitTypeDraft] = useState<"VOLUME" | "CHAPTER">("CHAPTER");
  const [splitOffset, setSplitOffset] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [modelSettings, setModelSettings] = useState<ModelSettings | null>(null);
  const [analysisRun, setAnalysisRun] = useState<AnalysisRun | null>(null);
  const [analysisDiagnostics, setAnalysisDiagnostics] = useState<AnalysisRunDiagnostics | null>(null);
  const [analysisCallContents, setAnalysisCallContents] = useState<Record<string, AnalysisCallContent>>({});
  const [loadingCallContent, setLoadingCallContent] = useState("");
  const [analysisEstimate, setAnalysisEstimate] = useState<AnalysisUsageEstimate | null>(null);
  const [workbench, setWorkbench] = useState<Workbench | null>(null);
  const [activeStudioTab, setActiveStudioTab] = useState<StudioTab>("learn");
  const [evidenceContext, setEvidenceContext] = useState<EvidenceContext | null>(null);
  const [targetEvidence, setTargetEvidence] = useState<EvidenceContext["evidence"] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [showDiagnostics, setShowDiagnostics] = useState(false);

  const loadAnalysisResults = useCallback(async (run: AnalysisRun | null, preserveState = false) => {
    setAnalysisRun(run);
    if (!run) {
      setWorkbench(null);
      setEvidenceContext(null);
      setAnalysisDiagnostics(null);
      setAnalysisCallContents({});
      return;
    }
    if (!preserveState) {
      setEvidenceContext(null);
    }
    const diagnostics = await api.analysisDiagnostics(run.id);
    setAnalysisDiagnostics(diagnostics);
    if (!run.has_usable_result && !["REVIEW", "CONFIRMED", "FAILED"].includes(run.status)) {
      if (!preserveState) {
        setWorkbench(null);
      }
      return;
    }
    try {
      const nextWorkbench = await api.analysisWorkbench(run.id);
      setWorkbench(nextWorkbench);
    } catch (reason) {
      if (!run.has_usable_result && run.status !== "FAILED") throw reason;
    }
  }, []);

  const loadProject = useCallback(async (projectId: string) => {
    setActiveStudioTab("learn");
    setVersions([]);
    setActiveVersion(null);
    setChapters([]);
    setIssues([]);
    setSelectedChapter("");
    setChapterContent(null);
    setAnalysisRun(null);
    setAnalysisDiagnostics(null);
    setAnalysisCallContents({});
    setAnalysisEstimate(null);
    setWorkbench(null);
    setEvidenceContext(null);
    setTargetEvidence(null);
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
    const unit = chapters.find((item) => item.id === selectedChapter);
    setChapterTitleDraft(unit?.title ?? "");
    setChapterTypeDraft(unit?.unit_type ?? "CHAPTER");
    setSplitTitleDraft("");
    setSplitTypeDraft("CHAPTER");
    setSplitOffset(0);
  }, [chapters, selectedChapter]);

  useEffect(() => {
    if (!activeVersion || !analysisRun || !["PENDING", "RUNNING"].includes(analysisRun.status)) {
      return;
    }
    let active = true;
    const refresh = async () => {
      try {
        const next = await api.latestAnalysis(activeVersion.id);
        if (active) await loadAnalysisResults(next, true);
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
  const volumeUnitCount = chapters.filter((chapter) => chapter.unit_type === "VOLUME").length;
  const selectedChapterIndex = chapters.findIndex((chapter) => chapter.id === selectedChapter);
  let currentStage = 0;
  if (activeVersion?.status === "CONFIRMED") currentStage = 1;
  if (workbench?.narrative_status === "READY") currentStage = 3;
  if (workbench?.deep_status === "READY") currentStage = 5;
  if (analysisRun?.status === "CONFIRMED") currentStage = STAGES.length;
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
  const splitLineOptions = useMemo(
    () => sourceLineStarts(chapterContent?.content ?? ""),
    [chapterContent],
  );

  function selectSplitLine(selectionStart: number) {
    if (!chapterContent || selectionStart <= 0) {
      setSplitOffset(0);
      return;
    }
    setSplitOffset(chapterContent.content.lastIndexOf("\n", selectionStart - 1) + 1);
  }

  async function handleCreateProjectByName(name: string) {
    if (!name.trim()) return;
    try {
      setBusy("create-project");
      setError("");
      const project = await api.createProject(name.trim());
      setProjects((current) => [project, ...current]);
      setSelectedProject(project.id);
      setProjectName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleCreateProject(event: FormEvent) {
    event.preventDefault();
    await handleCreateProjectByName(projectName);
  }

  async function handleImport(event?: FormEvent) {
    if (event) event.preventDefault();
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

  async function applySourceStructure(result: SourceStructure) {
    setActiveVersion(result.version);
    setVersions((current) => current.map((item) => (
      item.id === result.version.id ? result.version : item
    )));
    setChapters(result.units);
    setIssues(result.issues);
    setSelectedChapter(result.selected_unit_id);
    setChapterContent(await api.chapterContent(result.selected_unit_id));
  }

  async function handleSaveSourceUnit() {
    if (!selectedChapter || !chapterTitleDraft.trim()) return;
    try {
      setBusy("save-source-unit");
      setError("");
      await applySourceStructure(await api.updateSourceUnit(selectedChapter, {
        title: chapterTitleDraft.trim(),
        unit_type: chapterTypeDraft,
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleSplitSourceUnit() {
    if (
      !selectedChapter
      || !chapterContent
      || !splitTitleDraft.trim()
      || splitOffset <= 0
      || splitOffset >= chapterContent.content.length
    ) return;
    try {
      setBusy("split-source-unit");
      setError("");
      await applySourceStructure(await api.splitSourceUnit(selectedChapter, {
        split_char: chapterContent.start_char + splitOffset,
        title: splitTitleDraft.trim(),
        unit_type: splitTypeDraft,
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleMergeSourceUnit(direction: "PREVIOUS" | "NEXT") {
    if (!selectedChapter) return;
    const label = direction === "PREVIOUS" ? "上一单元" : "下一单元";
    if (!window.confirm(`将当前单元并入${label}，原文不会删除。是否继续？`)) return;
    try {
      setBusy(`merge-source-unit-${direction}`);
      setError("");
      await applySourceStructure(
        await api.mergeSourceUnit(selectedChapter, direction),
      );
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

  async function handleStartDeepAnalysis() {
    if (!analysisRun) return;
    try {
      setBusy("start-deep-analysis");
      setError("");
      await loadAnalysisResults(await api.startDeepAnalysis(analysisRun.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleStartCharacterDesign(force = false) {
    if (!analysisRun) return;
    const confirmed = window.confirm("这一步会读取现有拆书数据并发起一次在线 AI 请求，用于生成独立的 2.2 主角证据账本，并计入 Token（令牌）用量。完成后 2.2 可以独立形成学习答案，但不会写入新书开书包。是否继续？");
    if (!confirmed) return;
    try {
      setBusy("start-character-design");
      setError("");
      await loadAnalysisResults(await api.startCharacterDesignEvidence(analysisRun.id, force));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleStartChapterEndHooks(force = false) {
    if (!analysisRun) return;
    const confirmed = window.confirm("这一步会从现有拆书数据中固定真实章末原文，并发起在线 AI 请求，用于生成独立的 4.9 逐章钩子类型与全书节律账本，会计入 Token（令牌）用量；不会追踪回应，也不会写入新书开书包。是否继续？");
    if (!confirmed) return;
    try {
      setBusy("start-chapter-end-hooks");
      setError("");
      await loadAnalysisResults(await api.startChapterEndHooks(analysisRun.id, force));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleStartOpeningHookPayoffs(force = false) {
    if (!analysisRun) return;
    const confirmed = window.confirm("这一步会基于当前 4.9 账本，连续检查前三章之后的正文，发起在线 AI 请求来寻找每个开篇钩子的最早真实回应，并由程序计算章数与字数距离，会计入 Token（令牌）用量；只形成独立的 3.4 证据账本，不会写入新书开书包。是否继续？");
    if (!confirmed) return;
    try {
      setBusy("start-opening-hook-payoffs");
      setError("");
      await loadAnalysisResults(await api.startOpeningHookPayoffs(analysisRun.id, force));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleStartOpeningStructure(force = false) {
    if (!analysisRun) return;
    const confirmed = window.confirm("这一步只读取前三章全部段落，并发起一次在线 AI 请求，同时生成 3.1 开场卡与 3.2 段落任务/信息装载共享账本，会计入 Token（令牌）用量；不会自动生成其余问题，也不会写入新书开书包。是否继续？");
    if (!confirmed) return;
    try {
      setBusy("start-opening-structure");
      setError("");
      await loadAnalysisResults(await api.startOpeningStructure(analysisRun.id, force));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleStartLearningReport() {
    if (!analysisRun) return;
    const readiness = workbench?.learning_report.readiness;
    if (!readiness?.ready) {
      setError("创作问题的证据原料尚未就绪，系统没有创建在线任务。请先查看学习报告里的数据就绪检查。");
      return;
    }
    const confirmed = window.confirm(`这一步只会提交当前可回答且需要更新的创作问题，并发起一次在线 AI 请求，会计入 Token（令牌）用量。当前首组有 ${readiness.complete_question_count} 项可完整回答、${readiness.partial_question_count} 项可部分回答；其他问题不会阻塞或被包装成已完成。是否继续？`);
    if (!confirmed) return;
    try {
      setBusy("start-learning-report");
      setError("");
      await loadAnalysisResults(await api.startLearningReport(analysisRun.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleRepairNarrative() {
    if (!analysisRun) return;
    // Exit learn view before repair so the UI stays stable during background update
    if (activeStudioTab === "learn") setActiveStudioTab("archive");
    try {
      setBusy("repair-narrative");
      setError("");
      await loadAnalysisResults(await api.repairNarrativeAnalysis(analysisRun.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleLoadCallContent(attemptId: string) {
    if (!analysisRun || analysisCallContents[attemptId] || loadingCallContent === attemptId) return;
    try {
      setLoadingCallContent(attemptId);
      setError("");
      const content = await api.analysisCallContent(analysisRun.id, attemptId);
      setAnalysisCallContents((current) => ({ ...current, [attemptId]: content }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoadingCallContent("");
    }
  }

  async function handleRetryNarrativeComponent(component: "overview" | "characters" | "plot" | "relations", label: string) {
    if (!analysisRun) return;
    if (!window.confirm(`将只重新生成“${label}”，其他已经成功的版块不会重做。这个版块会发起新的在线 AI 请求；如果服务失败或输出不合格，系统可能按当前设置自动重试，因此会增加令牌用量。是否继续？`)) return;
    try {
      setBusy(`retry-component-${component}`);
      setError("");
      await loadAnalysisResults(await api.retryNarrativeComponent(analysisRun.id, component));
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

  async function handleProviderSwitch(decision: "SWITCH" | "RETRY_CURRENT" | "STOP") {
    if (!analysisRun) return;
    try {
      setBusy(
        decision === "SWITCH"
          ? "switch-provider"
          : decision === "RETRY_CURRENT"
            ? "retry-current-provider"
            : "stop-provider-switch",
      );
      setError("");
      await loadAnalysisResults(await api.confirmProviderSwitch(analysisRun.id, decision));
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


  const analysisPercent = analysisRun && analysisRun.total_batches
    ? Math.round((analysisRun.completed_batches / analysisRun.total_batches) * 100)
    : 0;

  const analysisProfile = modelSettings?.analysis_profiles.find((item) => item.id === "entities-events") ?? null;
  const analysisService = modelSettings?.services.find((item) => item.id === analysisProfile?.service_id) ?? null;
  const analysisConfigured = Boolean(analysisService?.configured && analysisProfile?.model);

  useEffect(() => {
    let active = true;
    if (!activeVersion || activeVersion.status !== "CONFIRMED" || !analysisConfigured || analysisRun) {
      setAnalysisEstimate(null);
      return () => { active = false; };
    }
    void api.analysisEstimate(activeVersion.id)
      .then((estimate) => {
        if (active) setAnalysisEstimate(estimate);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => { active = false; };
  }, [activeVersion, analysisConfigured, analysisProfile, analysisRun]);

  return (
    <AppLayout
      projects={projects}
      activeProjectId={selectedProject}
      onSelectProject={setSelectedProject}
      onCreateProject={handleCreateProjectByName}
      activeTab={activeStudioTab}
      onTabChange={setActiveStudioTab}
      health={health}
      hasAnalysis={Boolean(workbench || analysisRun)}
      learningReadyCount={
        workbench?.learning_report?.questions.filter(
          (q) => q.answer_status === "ANSWERED" || q.has_current_answer
        ).length
      }
      totalLearningQuestions={workbench?.learning_report?.questions.length ?? 42}
      chapterCount={chapterCount}
      busy={busy}
      evidenceContext={evidenceContext}
      onCloseEvidence={() => setEvidenceContext(null)}
      onNavigateToChapter={(chapterId) => {
        setSelectedChapter(chapterId);
        setActiveStudioTab("source");
        if (evidenceContext?.evidence) {
          setTargetEvidence(evidenceContext.evidence);
        }
      }}
      showDiagnostics={showDiagnostics}
      onCloseDiagnostics={() => setShowDiagnostics(false)}
      analysisDiagnostics={analysisDiagnostics}
      analysisCallContents={analysisCallContents}
      loadingCallContent={loadingCallContent}
      onLoadCallContent={(attemptId) => void handleLoadCallContent(attemptId)}
      onRetryComponent={(component, label) => void handleRetryNarrativeComponent(component, label)}
    >
      {error && <div className="product-error" role="alert">{error}</div>}

      {activeStudioTab === "learn" && (
        workbench ? (
          <LearningView
            workbench={workbench}
            onOpenEvidence={(evidenceId) => void handleOpenEvidence(evidenceId)}
            onStartLearningReport={() => void handleStartLearningReport()}
            busy={busy}
          />
        ) : (
          <div className="studio-empty-state">
            <div className="empty-card">
              <span className="empty-icon">📖</span>
              <h3>暂无拆解学习数据</h3>
              <p>请先在“分析控制”中导入小说并启动拆解流水线，完成后将在此自动生成 42 项创作学习问答与写作手法研报。</p>
              <button
                type="button"
                className="primary-button"
                onClick={() => setActiveStudioTab("pipeline")}
              >
                前往分析控制中心
              </button>
            </div>
          </div>
        )
      )}

      {activeStudioTab === "archive" && (
        workbench ? (
          <ArchiveView
            workbench={workbench}
            analysisStatus={analysisRun?.status ?? "REVIEW"}
            evidenceContext={evidenceContext}
            onOpenEvidence={(evidenceId) => void handleOpenEvidence(evidenceId)}
            onCloseEvidence={() => setEvidenceContext(null)}
            sourceChapters={chapters}
            selectedChapterId={selectedChapter}
            chapterContent={chapterContent}
            onSelectChapter={setSelectedChapter}
            busy={busy}
            onAnalysisRunChange={(run) => void loadAnalysisResults(run)}
            onWorkbenchChange={setWorkbench}
            onRepairNarrative={() => void handleRepairNarrative()}
            onStartDeepAnalysis={() => void handleStartDeepAnalysis()}
            onStartCharacterDesign={(force) => void handleStartCharacterDesign(force)}
            onStartChapterEndHooks={(force) => void handleStartChapterEndHooks(force)}
            onStartOpeningHookPayoffs={(force) => void handleStartOpeningHookPayoffs(force)}
            onStartOpeningStructure={(force) => void handleStartOpeningStructure(force)}
            onStartLearningReport={() => void handleStartLearningReport()}
            onConfirmAnalysis={() => void handleConfirmAnalysis()}
          />
        ) : (
          <div className="studio-empty-state">
            <div className="empty-card">
              <span className="empty-icon">🗂️</span>
              <h3>暂无故事元素档案</h3>
              <p>故事结构拆解尚未完成。导入小说并开始分析后，人物档案、剧情阶段、事实推演与伏笔网络将在此呈现。</p>
              <button
                type="button"
                className="primary-button"
                onClick={() => setActiveStudioTab("pipeline")}
              >
                前往分析控制中心
              </button>
            </div>
          </div>
        )
      )}

      {activeStudioTab === "source" && (
        <SourceView
          chapters={chapters}
          selectedChapterId={selectedChapter}
          onSelectChapter={(id) => {
            setSelectedChapter(id);
            setTargetEvidence(null);
          }}
          chapterContent={chapterContent}
          activeVersion={activeVersion}
          chapterDisplayNumbers={chapterDisplayNumbers}
          chapterIssueMap={chapterIssueMap}
          chapterTitleDraft={chapterTitleDraft}
          setChapterTitleDraft={setChapterTitleDraft}
          chapterTypeDraft={chapterTypeDraft}
          setChapterTypeDraft={setChapterTypeDraft}
          onSaveSourceUnit={handleSaveSourceUnit}
          onMergeSourceUnit={handleMergeSourceUnit}
          splitTitleDraft={splitTitleDraft}
          setSplitTitleDraft={setSplitTitleDraft}
          splitTypeDraft={splitTypeDraft}
          setSplitTypeDraft={setSplitTypeDraft}
          splitOffset={splitOffset}
          setSplitOffset={setSplitOffset}
          splitLineOptions={splitLineOptions}
          onSplitSourceUnit={handleSplitSourceUnit}
          busy={busy}
          targetEvidence={targetEvidence}
          onClearTargetEvidence={() => setTargetEvidence(null)}
        />
      )}

      {activeStudioTab === "pipeline" && (
        <AnalysisView
          activeVersion={activeVersion}
          file={file}
          onSelectFile={setFile}
          onImport={handleImport}
          blockingCount={blockingCount}
          openIssues={openIssues}
          onConfirmSource={handleConfirmSource}
          onStartAnalysis={handleStartAnalysis}
          analysisRun={analysisRun}
          analysisEstimate={analysisEstimate}
          modelSettings={modelSettings}
          workbench={workbench}
          onOpenDiagnostics={() => setShowDiagnostics(true)}
          onJumpToLearn={() => setActiveStudioTab("learn")}
          onJumpToArchive={() => setActiveStudioTab("archive")}
          busy={busy}
        />
      )}
    </AppLayout>
  );
}
