import React from "react";
import type {
  AnalysisRun,
  AnalysisUsageEstimate,
  ModelSettings,
  SourceIssue,
  SourceVersion,
  Workbench,
} from "../../api";

interface AnalysisViewProps {
  activeVersion: SourceVersion | null;
  file: File | null;
  onSelectFile: (file: File | null) => void;
  onImport: () => Promise<void>;
  blockingCount: number;
  openIssues: SourceIssue[];
  onConfirmSource: () => Promise<void>;
  onStartAnalysis: () => Promise<void>;
  analysisRun: AnalysisRun | null;
  analysisEstimate: AnalysisUsageEstimate | null;
  modelSettings: ModelSettings | null;
  workbench: Workbench | null;
  onOpenDiagnostics: () => void;
  onJumpToLearn: () => void;
  onJumpToArchive: () => void;
  busy?: string;
}

const STAGES = [
  "导入与章节结构",
  "主角与配角人设",
  "剧情阶段与事件链",
  "世界设定与事实演变",
  "伏笔铺设与核心冲突",
  "42问创作学习手册",
];

export function AnalysisView({
  activeVersion,
  file,
  onSelectFile,
  onImport,
  blockingCount,
  openIssues,
  onConfirmSource,
  onStartAnalysis,
  analysisRun,
  analysisEstimate,
  modelSettings,
  workbench,
  onOpenDiagnostics,
  onJumpToLearn,
  onJumpToArchive,
  busy,
}: AnalysisViewProps) {
  const analysisProfile = modelSettings?.analysis_profiles.find((p) => p.id === "entities-events") ?? null;
  const analysisService = modelSettings?.services.find((s) => s.id === analysisProfile?.service_id) ?? null;
  const isConfigured = Boolean(analysisService?.configured && analysisProfile?.model);

  const isRunning = analysisRun && ["PENDING", "RUNNING"].includes(analysisRun.status);
  const isSucceeded = analysisRun && (analysisRun.status === "REVIEW" || analysisRun.status === "CONFIRMED" || analysisRun.has_usable_result);
  const isFailed = analysisRun && (analysisRun.status === "FAILED" || analysisRun.latest_update_failed);

  const analysisPercent = analysisRun?.total_batches
    ? Math.round((analysisRun.completed_batches / analysisRun.total_batches) * 100)
    : 0;

  return (
    <div className="analysis-view-container">
      <header className="view-header-strip">
        <div className="view-header-main">
          <h2>分析控制中心</h2>
          <p className="view-header-desc">
            管理小说导入、章节结构确认、AI 流水线推进与拆解任务状态。
          </p>
        </div>
        <div className="view-header-actions">
          {analysisRun && (
            <button
              type="button"
              className="secondary-button"
              onClick={onOpenDiagnostics}
            >
              🔍 技术排查与调用记录
            </button>
          )}
        </div>
      </header>

      <div className="analysis-view-body">
        {/* Step 1: Upload Novel File (if no version) */}
        {!activeVersion ? (
          <section className="analysis-card upload-card">
            <div className="card-header">
              <span className="step-badge">第 1 步</span>
              <h3>导入小说文件</h3>
            </div>
            <p className="card-desc">
              支持导入 TXT、Markdown (.md)、DOCX 或 EPUB 格式整本小说，系统将自动识别章节与卷结构。
            </p>

            <div className="file-drop-zone">
              <input
                id="file-upload"
                type="file"
                className="file-input-hidden"
                accept=".txt,.md,.markdown,.docx,.epub"
                onChange={(e) => onSelectFile(e.target.files?.[0] ?? null)}
              />
              <label htmlFor="file-upload" className="file-drop-label">
                <span className="file-icon">📄</span>
                <span className="file-name-text">
                  {file ? file.name : "点击选择或拖放小说文件至此"}
                </span>
                <small className="file-size-hint">
                  {file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : "TXT / Markdown / DOCX / EPUB"}
                </small>
              </label>
            </div>

            <div className="card-footer">
              <button
                type="button"
                className="primary-button"
                disabled={!file || busy === "import"}
                onClick={() => void onImport()}
              >
                {busy === "import" ? "正在读取并识别章节…" : "开始导入并解析章节"}
              </button>
            </div>
          </section>
        ) : (
          /* Source File Status Overview */
          <section className="analysis-card file-info-card">
            <div className="card-header">
              <span className="step-badge">当前版本</span>
              <h3>第 {activeVersion.version_no} 版小说源文件</h3>
              <span className={`status-pill ${activeVersion.status.toLowerCase()}`}>
                {activeVersion.status === "CONFIRMED" ? "章节已确认" : "待确认章节"}
              </span>
            </div>

            <div className="metrics-summary-row">
              <div className="metric-box">
                <span>总字数</span>
                <strong>{activeVersion.total_chars.toLocaleString()} 字</strong>
              </div>
              <div className="metric-box">
                <span>识别章节</span>
                <strong>{activeVersion.chapter_count} 章</strong>
              </div>
              <div className="metric-box">
                <span>需确认边界</span>
                <strong>{blockingCount} 项</strong>
              </div>
            </div>

            {activeVersion.status !== "CONFIRMED" && (
              <div className="confirm-source-actions">
                <p>
                  {blockingCount > 0
                    ? `还有 ${blockingCount} 项卷章边界问题需要确认，请先在“原文与章节”中核对。`
                    : "章节边界识别正常，确认后即可开启 AI 拆解流水线。"}
                </p>
                <button
                  type="button"
                  className="primary-button"
                  disabled={blockingCount > 0 || busy === "confirm-source"}
                  onClick={() => void onConfirmSource()}
                >
                  {busy === "confirm-source" ? "确认中…" : "确认章节无误，进入分析"}
                </button>
              </div>
            )}
          </section>
        )}

        {/* Step 2: AI Pipeline Control (after chapters confirmed) */}
        {activeVersion && activeVersion.status === "CONFIRMED" && (
          <section className="analysis-card pipeline-card">
            <div className="card-header">
              <span className="step-badge">第 2 步</span>
              <h3>AI 深度拆解流水线</h3>
              {analysisProfile && (
                <span className="model-tag">
                  模型: {analysisProfile.model || analysisProfile.name}
                </span>
              )}
            </div>

            {/* Model Not Configured Prompt */}
            {!isConfigured && (
              <div className="callout-box warning-callout">
                <strong>未配置在线 AI 服务</strong>
                <p>开始全书分析前，请先在“设置中心”配置 API Key 与模型服务（支持 DeepSeek、OpenAI、Gemini 等）。</p>
                <a href="/settings" className="secondary-button small-btn">前往设置中心</a>
              </div>
            )}

            {/* Ready to Start Analysis */}
            {isConfigured && !analysisRun && (
              <div className="pipeline-start-box">
                <p>整本小说章节已确认，准备开始执行人物角色、情节阶段、事实推演与伏笔全量拆解。</p>
                {analysisEstimate && (
                  <div className="usage-estimate-banner">
                    <span>预计基础批次调用 {analysisEstimate.planned_call_count} 次</span>
                    <span>预计输入上限 ~{Math.round(analysisEstimate.estimated_input_tokens / 1000)}k Token</span>
                    <small>{analysisEstimate.basis}</small>
                  </div>
                )}
                <button
                  type="button"
                  className="primary-button large-btn"
                  disabled={busy === "start-analysis"}
                  onClick={() => void onStartAnalysis()}
                >
                  {busy === "start-analysis" ? "正在启动任务…" : "🚀 启动整本小说深度拆解"}
                </button>
              </div>
            )}

            {/* Running Pipeline Progress */}
            {isRunning && (
              <div className="pipeline-progress-box">
                <div className="progress-header">
                  <strong>{analysisRun.status === "PENDING" ? "任务排队中…" : "正在进行全书分析…"}</strong>
                  <span className="progress-num">{analysisPercent}%</span>
                </div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${analysisPercent}%` }} />
                </div>
                <p className="progress-subtext">
                  已完成 {analysisRun.completed_batches} / {analysisRun.total_batches} 批次。后台持续处理，可随时切换到其他页面。
                </p>
              </div>
            )}

            {/* Completed Success Box */}
            {isSucceeded && (
              <div className="pipeline-completed-box">
                <div className="success-banner">
                  <span className="success-icon">✓</span>
                  <div>
                    <strong>整本小说拆解分析已完成！</strong>
                    <span>基础故事档案与 42 项创作学习问答已全部生成完毕。</span>
                  </div>
                </div>
                <div className="success-actions">
                  <button type="button" className="primary-button" onClick={onJumpToLearn}>
                    📖 进入“学习手册”查阅创作心得
                  </button>
                  <button type="button" className="secondary-button" onClick={onJumpToArchive}>
                    📊 进入“故事档案”查看人物与剧情
                  </button>
                </div>
              </div>
            )}

            {/* Failed Box */}
            {isFailed && (
              <div className="pipeline-failed-box">
                <div className="error-banner">
                  <strong>分析遇到问题</strong>
                  <p>{analysisRun?.failure_message || "部分请求超时或未完成，请检查网络和 API Key 后重试。"}</p>
                </div>
                <div className="failed-actions">
                  <button
                    type="button"
                    className="primary-button"
                    disabled={Boolean(busy)}
                    onClick={() => void onStartAnalysis()}
                  >
                    重试分析任务
                  </button>
                  <a href="/settings" className="secondary-button">检查 AI 设置</a>
                </div>
              </div>
            )}

            {/* 6 Stages Stepper Checklist */}
            <div className="stages-checklist">
              <h4>拆解流水线阶段</h4>
              <div className="stages-grid">
                {STAGES.map((st, i) => {
                  const isDone = isSucceeded || (isRunning && i < 2);
                  return (
                    <div key={st} className={`stage-step-card ${isDone ? "done" : isRunning ? "active" : ""}`}>
                      <span className="step-num">{isDone ? "✓" : i + 1}</span>
                      <span className="step-name">{st}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
