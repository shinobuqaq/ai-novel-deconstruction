import React from "react";
import type {
  AnalysisIssue,
  DeepAnalysisDiff,
  DeepAnalysisRevision,
  DeepRevisionImpact,
} from "../../api";

interface IssuesViewProps {
  issues: AnalysisIssue[];
  issueError: string;
  issueBusy: string;
  issueTarget: { kind: string; id: string | null; label: string } | null;
  issueCategory: string;
  issueNote: string;
  revisionImpact: DeepRevisionImpact | null;
  revisions: DeepAnalysisRevision[];
  revisionDiff: DeepAnalysisDiff | null;
  currentRevision: number | null;
  isHistoricalRevision: boolean;
  onSetIssueCategory: (category: string) => void;
  onSetIssueNote: (note: string) => void;
  onCancelIssue: () => void;
  onSubmitIssue: (event: React.FormEvent) => void;
  onResolveIssue: (issueId: string) => void;
  onRecomputeFromIssues: () => void;
}

export function IssuesView({
  issues,
  issueError,
  issueBusy,
  issueTarget,
  issueCategory,
  issueNote,
  revisionImpact,
  revisions,
  revisionDiff,
  currentRevision,
  isHistoricalRevision,
  onSetIssueCategory,
  onSetIssueNote,
  onCancelIssue,
  onSubmitIssue,
  onResolveIssue,
  onRecomputeFromIssues,
}: IssuesViewProps) {
  const openIssues = issues.filter((item) => item.status === "OPEN");

  return (
    <section className="analysis-problem-center embedded">
      <header>
        <div>
          <p>问题与修正</p>
          <h3>告诉系统哪里需要重新检查</h3>
          <span>只描述内容问题即可，系统会自行重新分析并保留旧版本。</span>
        </div>
        <b>{openIssues.length} 项待处理</b>
      </header>
      {issueError && (
        <div className="analysis-issue-error" role="alert">
          {issueError}
        </div>
      )}
      {revisionImpact && revisionImpact.issue_count > 0 && (
        <section className="recompute-impact" aria-label="重新分析范围">
          <header>
            <div>
              <p>程序判断</p>
              <h3>这次会重新检查什么</h3>
              <span>{revisionImpact.summary}</span>
            </div>
            <b>{revisionImpact.mode === "STORY_WIDE" ? "故事结构联动" : "按问题对象检查"}</b>
          </header>
          <div className="recompute-impact-list">
            {revisionImpact.sections.map((section) => (
              <article key={section.key}>
                <div>
                  <strong>{section.label}</strong>
                  <span>{section.reason}</span>
                </div>
                <b>{section.item_count} 项</b>
                {section.item_labels.length > 0 && (
                  <p>
                    {section.item_labels.join("、")}
                    {section.item_count > section.item_labels.length
                      ? ` 等 ${section.item_count - section.item_labels.length} 项`
                      : ""}
                  </p>
                )}
                {!section.item_labels.length && (
                  <p className="impact-empty">暂未锁定具体条目，重新分析时会按原文证据核对。</p>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
      {issueTarget && (
        <form className="analysis-issue-form" onSubmit={onSubmitIssue}>
          <div>
            <span>正在标记</span>
            <strong>{issueTarget.label}</strong>
          </div>
          <label>
            问题类型
            <select
              value={issueCategory}
              onChange={(event) => onSetIssueCategory(event.target.value)}
            >
              <option value="INCORRECT">内容不正确</option>
              <option value="EVIDENCE">原文依据不对</option>
              <option value="UNCLEAR">表达看不懂</option>
              <option value="MISSING">遗漏重要内容</option>
              <option value="OTHER">其他问题</option>
            </select>
          </label>
          <label>
            具体说明
            <textarea
              value={issueNote}
              onChange={(event) => onSetIssueNote(event.target.value)}
              placeholder="例如：这里把人物的猜测写成了确定事实"
              maxLength={2000}
            />
          </label>
          <div className="issue-form-actions">
            <button type="button" className="secondary-button" onClick={onCancelIssue}>
              取消
            </button>
            <button
              type="submit"
              disabled={!issueNote.trim() || issueBusy === "create"}
            >
              {issueBusy === "create" ? "正在保存" : "保存问题"}
            </button>
          </div>
        </form>
      )}
      {issues.length > 0 ? (
        <div className="analysis-issue-list">
          {issues.map((issue) => (
            <article key={issue.id} className={issue.status === "RESOLVED" ? "resolved" : ""}>
              <div>
                <span>{issue.status === "OPEN" ? "等待处理" : "已经处理"}</span>
                <strong>{issue.target_label}</strong>
                <p>{issue.note}</p>
              </div>
              {issue.status === "OPEN" && (
                <button
                  type="button"
                  className="secondary-button"
                  disabled={issueBusy === issue.id}
                  onClick={() => onResolveIssue(issue.id)}
                >
                  {issueBusy === issue.id ? "处理中" : "不再处理"}
                </button>
              )}
            </article>
          ))}
        </div>
      ) : (
        <p className="result-empty">
          目前没有标记问题。可以在故事总览、人物关系、剧情阶段、事件、事实状态、世界设定和核心分析内容中点击“标记问题”。
        </p>
      )}
      <footer>
        <div>
          <strong>
            {currentRevision
              ? `当前为第 ${currentRevision} 版深层拆解`
              : "尚未生成深层拆解版本"}
          </strong>
          <span>
            {revisions.length > 1 && revisionDiff
              ? `上一版到当前版：新增 ${
                  Object.values(revisionDiff.added).flat().length
                } 项，移除 ${
                  Object.values(revisionDiff.removed).flat().length
                } 项，修改 ${Object.values(revisionDiff.changed_counts).reduce(
                  (sum, value) => sum + value,
                  0,
                )} 项。`
              : "重新分析完成后会在这里显示版本变化。"}
          </span>
        </div>
        <button
          type="button"
          disabled={
            isHistoricalRevision ||
            !openIssues.length ||
            issueBusy === "recompute"
          }
          onClick={onRecomputeFromIssues}
        >
          {issueBusy === "recompute" ? "正在准备重新分析" : "根据待处理问题重新分析"}
        </button>
      </footer>
    </section>
  );
}
