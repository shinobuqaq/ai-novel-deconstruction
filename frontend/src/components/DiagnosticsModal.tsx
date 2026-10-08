import React from "react";
import type { AnalysisCallContent, AnalysisRunDiagnostics } from "../api";

const ANALYSIS_STAGE_STATUS_LABELS: Record<string, string> = {
  PENDING: "等待中",
  RUNNING: "运行中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  RETRY_WAIT: "等待重试",
  WAITING_CONFIRMATION: "等待确认",
  CANCELLED: "已取消",
};

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatDuration(value: number): string {
  const seconds = Math.max(0, Math.round(value));
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours > 0) {
    return `${hours}小时${remainingMinutes ? ` ${remainingMinutes}分` : ""}`;
  }
  if (minutes > 0) {
    return `${minutes}分${remainingSeconds ? ` ${remainingSeconds}秒` : ""}`;
  }
  return `${seconds}秒`;
}

export interface DiagnosticsModalProps {
  isOpen: boolean;
  onClose: () => void;
  diagnostics: AnalysisRunDiagnostics | null;
  callContents: Record<string, AnalysisCallContent>;
  loadingCallContent: string;
  onLoadCallContent: (attemptId: string) => void;
  onRetryComponent?: (component: "overview" | "characters" | "plot" | "relations", label: string) => void;
  busy?: string;
}

export function DiagnosticsModal({
  isOpen,
  onClose,
  diagnostics,
  callContents,
  loadingCallContent,
  onLoadCallContent,
  onRetryComponent,
  busy,
}: DiagnosticsModalProps) {
  if (!isOpen || !diagnostics) return null;

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="diagnostics-modal-title">
      <div className="modal-panel diagnostics-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h2 id="diagnostics-modal-title">AI 调用日志与底层诊断</h2>
            <p className="modal-subtitle">
              当前阶段：{diagnostics.current_step} · 累计调用在线 AI {diagnostics.attempt_count} 次
              {diagnostics.retry_count ? `，自动重试 ${diagnostics.retry_count} 次` : "，无重试"}
            </p>
          </div>
          <button type="button" className="close-button" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        <div className="diagnostics-summary-bar">
          <div>
            <small>模型等待时间</small>
            <strong>{formatDuration(diagnostics.duration_seconds)}</strong>
          </div>
          <div>
            <small>输入 Token 规模</small>
            <strong>{formatNumber(diagnostics.prompt_tokens)}</strong>
          </div>
          <div>
            <small>输出 Token 规模</small>
            <strong>{formatNumber(diagnostics.completion_tokens)}</strong>
          </div>
        </div>

        <div className="modal-scroll-body analysis-stage-list">
          {diagnostics.stages.map((stage, index) => (
            <details className={`analysis-stage-detail ${stage.status.toLowerCase()}`} key={stage.key}>
              <summary>
                <b>{index + 1}</b>
                <span>
                  <strong>{stage.label}</strong>
                  <small>
                    {ANALYSIS_STAGE_STATUS_LABELS[stage.status] ?? stage.status}
                    {stage.attempt_count ? ` · ${stage.attempt_count} 次调用 · ${formatDuration(stage.duration_seconds)}` : ""}
                  </small>
                </span>
              </summary>
              <div className="analysis-stage-body">
                {stage.latest_error && <p className="analysis-call-error">{stage.latest_error}</p>}
                {stage.calls.length === 0 ? (
                  <p className="analysis-stage-empty">这个阶段还没有调用在线 AI。</p>
                ) : (
                  stage.calls.map((call, callIndex) => {
                    const content = callContents[call.attempt_id];
                    const callLabel = call.component_label || `${stage.label}第 ${callIndex + 1} 批`;
                    return (
                      <details className="analysis-call-detail" key={call.attempt_id}>
                        <summary>
                          <span>
                            <strong>第 {callIndex + 1} 次 · {callLabel}</strong>
                            <small>
                              {ANALYSIS_STAGE_STATUS_LABELS[call.status] ?? call.status}
                              {call.attempt_no > 1 ? ` · 第 ${call.attempt_no} 次尝试` : ""}
                              {call.finished_at ? ` · ${formatDuration(call.duration_seconds)}` : ""}
                              {call.transport_mode === "STREAMING" ? " · 流式传输" : call.transport_mode === "LOCAL_FULL_RESPONSE" ? " · 本机整包" : ""}
                            </small>
                          </span>
                          <span className="analysis-call-totals">
                            <small>
                              {call.prompt_tokens || call.input_chars
                                ? `输入约 ${formatNumber(call.prompt_tokens)} 令牌 / ${formatNumber(call.input_chars)} 字符`
                                : "输入规模未记录"}
                            </small>
                            <small>
                              {call.completion_tokens || call.output_chars
                                ? `输出约 ${formatNumber(call.completion_tokens)} 令牌 / ${formatNumber(call.output_chars)} 字符`
                                : "输出规模未记录"}
                            </small>
                          </span>
                        </summary>
                        <div className="analysis-call-body">
                          {call.selected_material_count > 0 && (
                            <p className="material-budget-explanation">
                              本次调用放入了 <strong>{formatNumber(call.selected_material_count)}</strong> 份候选输入片段
                              {call.omitted_material_count > 0
                                ? `，另有 ${formatNumber(call.omitted_material_count)} 份低优先级片段因本次输入长度有限而未放入。`
                                : "，候选片段均已放入。"}
                            </p>
                          )}
                          {call.error_message && <p className="analysis-call-error">{call.error_message}</p>}
                          <details
                            className="analysis-call-transcript"
                            onToggle={(event) => {
                              if (event.currentTarget.open) void onLoadCallContent(call.attempt_id);
                            }}
                          >
                            <summary>展开查看具体输入与输出 (Raw Transcript)</summary>
                            {loadingCallContent === call.attempt_id && !content ? (
                              <p className="loading-note">正在读取调用记录……</p>
                            ) : content ? (
                              <div className="analysis-transcript-grid">
                                <section>
                                  <h4>实际输入 Prompt</h4>
                                  {content.input_text !== null ? <pre>{content.input_text}</pre> : <p>{content.input_note}</p>}
                                </section>
                                <section>
                                  <h4>实际输出 Completion</h4>
                                  {content.output_text !== null ? <pre>{content.output_text}</pre> : <p>{content.output_note}</p>}
                                </section>
                              </div>
                            ) : null}
                          </details>
                          {call.can_retry_component && call.component && call.status === "SUCCEEDED" && onRetryComponent && (
                            <div className="analysis-call-actions">
                              <button
                                type="button"
                                className="secondary-button"
                                disabled={Boolean(busy)}
                                onClick={() => onRetryComponent(call.component as "overview" | "characters" | "plot" | "relations", callLabel)}
                              >
                                {busy === `retry-component-${call.component}` ? `正在重新生成${callLabel}` : `只重新生成${callLabel}`}
                              </button>
                              <small>只为这一版块新建任务，其他成功结果保持不变。</small>
                            </div>
                          )}
                        </div>
                      </details>
                    );
                  })
                )}
              </div>
            </details>
          ))}
        </div>

        <div className="modal-footer">
          <button type="button" className="primary-button" onClick={onClose}>
            关闭诊断面板
          </button>
        </div>
      </div>
    </div>
  );
}
