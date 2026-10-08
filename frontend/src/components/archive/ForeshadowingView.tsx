import React from "react";
import type { Workbench } from "../../api";
import { FORESHADOWING_LABELS } from "./constants";

interface ForeshadowingViewProps {
  viewData: Workbench;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
}

export function ForeshadowingView({
  viewData,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
}: ForeshadowingViewProps) {
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
          <strong>核心分析仍在整理</strong>
          <span>系统会先验证事实和状态，再生成伏笔、冲突、节奏和场景分析。</span>
        </div>
      ) : (
        <section className="insight-group">
          <header>
            <div>
              <span>叙事承诺账本</span>
              <h3>伏笔与回收</h3>
            </div>
            <b>{viewData.deep_analysis.foreshadowing.length}</b>
          </header>
          <div className="formal-card-list compact-list">
            {viewData.deep_analysis.foreshadowing.map((item) => (
              <article
                className={`formal-card${focusClass(item.id)}`}
                data-workbench-id={item.id}
                key={item.id}
              >
                <header>
                  <div>
                    <span>{FORESHADOWING_LABELS[item.lifecycle] ?? "伏笔"}</span>
                    <h3>{item.title}</h3>
                  </div>
                  <i>第 {item.setup_chapter} 章提出</i>
                </header>
                <p>{item.setup}</p>
                {item.payoff_chapter && <small>第 {item.payoff_chapter} 章出现回收</small>}
                {renderEvidenceButtons(item.evidence_ids)}
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => onMarkProblem("FORESHADOWING", item.id, item.title)}
                >
                  标记问题
                </button>
              </article>
            ))}
            {!viewData.deep_analysis.foreshadowing.length && (
              <p className="result-empty">当前没有足够证据确认伏笔。</p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
