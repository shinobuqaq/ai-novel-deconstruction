import React from "react";
import type { Workbench } from "../../api";
import { EVENT_LABELS } from "./constants";

interface PlotViewProps {
  viewData: Workbench;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
  onNavigateItem: (view: string, id: string) => void;
}

export function PlotView({
  viewData,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
  onNavigateItem,
}: PlotViewProps) {
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
    <div className="formal-card-list">
      {viewData.phases.map((phase, index) => (
        <article
          className={`formal-card phase-detail-card${focusClass(phase.id)}`}
          data-workbench-id={phase.id}
          key={phase.id}
        >
          <header>
            <div>
              <span>阶段 {index + 1}</span>
              <h3>{phase.title}</h3>
            </div>
            <i>{phase.chapter_titles.join("、") || "章节待定"}</i>
          </header>
          <p>{phase.situation}</p>
          {phase.goal && <small>阶段目标：{phase.goal}</small>}
          {phase.obstacle && <small>主要障碍：{phase.obstacle}</small>}
          <div className="phase-event-list">
            {phase.event_ids.map((eventId) => {
              const event = viewData.events.find((item) => item.id === eventId);
              return event ? (
                <button
                  type="button"
                  key={event.id}
                  onClick={() => onNavigateItem("events", event.id)}
                >
                  <span>{EVENT_LABELS[event.event_type] ?? "事件"}</span>
                  <strong>{event.title}</strong>
                </button>
              ) : null;
            })}
          </div>
          {phase.outcome && (
            <p>
              <strong>结果：</strong>
              {phase.outcome}
            </p>
          )}
          {phase.change && (
            <p>
              <strong>局面变化：</strong>
              {phase.change}
            </p>
          )}
          {phase.next_hook && (
            <p>
              <strong>下一步悬念：</strong>
              {phase.next_hook}
            </p>
          )}
          {renderEvidenceButtons(phase.evidence_ids)}
          <button
            type="button"
            className="secondary-button"
            onClick={() => onMarkProblem("PLOT", phase.id, phase.title)}
          >
            标记问题
          </button>
        </article>
      ))}
      {!viewData.phases.length && <p className="result-empty">当前没有生成可读的剧情阶段。</p>}
    </div>
  );
}
