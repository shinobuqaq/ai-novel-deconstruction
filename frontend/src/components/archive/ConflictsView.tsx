import React from "react";
import type { Workbench } from "../../api";
import { CONFLICT_TYPE_LABELS } from "./constants";

interface ConflictsViewProps {
  viewData: Workbench;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
}

export function ConflictsView({
  viewData,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
}: ConflictsViewProps) {
  const focusClass = (id: string) =>
    focusTarget && focusTarget.id === id ? " focused-workbench-item" : "";

  const renderEvidenceButtons = (evidenceIds: string[], label = "查看原文依据") => {
    if (!evidenceIds.length) return null;
    return (
      <div className="card-evidence-actions">
        {evidenceIds.map((evidenceId, index) => (
          <button
            key={evidenceId}
            type="button"
            className="evidence-chip-button"
            onClick={() => onOpenEvidence(evidenceId)}
          >
            {evidenceIds.length > 1 ? `${label} (${index + 1})` : label}
          </button>
        ))}
      </div>
    );
  };

  return (
    <div className="deep-analysis-view">
      {!viewData.deep_analysis ? (
        <div className="workbench-callout">
          <strong>冲突分析仍在整理</strong>
          <span>系统会整理参与者目标、障碍、赌注、升级过程和当前结果。</span>
        </div>
      ) : (
        <section className="insight-group">
          <header>
            <div>
              <span>目标、障碍与赌注</span>
              <h3>冲突</h3>
            </div>
            <b>{viewData.deep_analysis.conflicts.length}</b>
          </header>
          <div className="formal-card-list compact-list">
            {viewData.deep_analysis.conflicts.map((conflict) => (
              <article
                className={`formal-card${focusClass(conflict.id)}`}
                data-workbench-id={conflict.id}
                key={conflict.id}
              >
                <header>
                  <div>
                    <span>
                      {CONFLICT_TYPE_LABELS[conflict.conflict_type] ?? "冲突类型待核对"} ·{" "}
                      {conflict.status === "RESOLVED"
                        ? "已经解决"
                        : conflict.status === "ESCALATING"
                        ? "正在升级"
                        : conflict.status === "SHIFTED"
                        ? "冲突转向"
                        : conflict.status === "UNCERTAIN"
                        ? "尚不确定"
                        : "仍未解决"}
                    </span>
                    <h3>{conflict.title}</h3>
                  </div>
                </header>
                {conflict.participants.length > 0 && (
                  <small>参与者：{conflict.participants.join("、")}</small>
                )}
                {conflict.goals && (
                  <p>
                    <strong>目标：</strong>
                    {conflict.goals}
                  </p>
                )}
                {conflict.obstacles && (
                  <p>
                    <strong>障碍：</strong>
                    {conflict.obstacles}
                  </p>
                )}
                {conflict.stakes && (
                  <p>
                    <strong>赌注：</strong>
                    {conflict.stakes}
                  </p>
                )}
                {conflict.escalation.length > 0 && (
                  <small>升级过程：{conflict.escalation.join("；")}</small>
                )}
                {conflict.resolution && (
                  <p>
                    <strong>当前结果：</strong>
                    {conflict.resolution}
                  </p>
                )}
                {renderEvidenceButtons(conflict.evidence_ids)}
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => onMarkProblem("CONFLICT", conflict.id, conflict.title)}
                >
                  标记问题
                </button>
              </article>
            ))}
            {!viewData.deep_analysis.conflicts.length && (
              <p className="result-empty">当前没有整理出证据充分的冲突。</p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
