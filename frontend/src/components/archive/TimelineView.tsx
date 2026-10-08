import React from "react";
import type { Workbench } from "../../api";
import {
  EVENT_LABELS,
  EVENT_RELATION_LABELS,
  NARRATIVE_MODE_LABELS,
} from "./constants";

interface TimelineViewProps {
  viewData: Workbench;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
  onNavigateItem: (view: string, id: string) => void;
}

export function TimelineView({
  viewData,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
  onNavigateItem,
}: TimelineViewProps) {
  const focusClass = (id: string) =>
    focusTarget && focusTarget.id === id ? " focused-workbench-item" : "";

  const characterForName = (name: string) =>
    viewData.characters.find(
      (item) => item.name === name || item.aliases.includes(name),
    );

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
      <div className="workbench-callout">
        <strong>按原文推进顺序查看</strong>
        <span>
          事件会标明真实发生、回忆、传闻、谎言、误解、推测或重复提及；多段依据不会被错误拼成一段连续正文。
        </span>
      </div>
      <div className="event-timeline">
        {viewData.events.map((event, index) => (
          <article
            className={focusClass(event.id).trim()}
            data-workbench-id={event.id}
            key={event.id}
          >
            <span>{index + 1}</span>
            <div>
              <small>
                {event.chapter_titles.join("、") || "章节待定"} ·{" "}
                {EVENT_LABELS[event.event_type] ?? "事件"} ·{" "}
                {NARRATIVE_MODE_LABELS[event.narrative_mode] ?? "性质待确认"}
              </small>
              <h3>{event.title}</h3>
              <p>{event.summary}</p>
              {event.trigger && <small>起因：{event.trigger}</small>}
              {event.outcome && <small>结果：{event.outcome}</small>}
              {event.impact && <small>影响：{event.impact}</small>}
              {event.people.length > 0 && (
                <div className="association-links">
                  <strong>参与人物</strong>
                  {event.people.map((name, nameIndex) => {
                    const character = characterForName(name);
                    return character ? (
                      <button
                        type="button"
                        key={`${name}-${nameIndex}`}
                        onClick={() => onNavigateItem("characters", character.id)}
                      >
                        {name}
                      </button>
                    ) : (
                      <span key={`${name}-${nameIndex}`}>{name}</span>
                    );
                  })}
                </div>
              )}
              {renderEvidenceButtons(event.evidence_ids)}
              <button
                type="button"
                className="secondary-button"
                onClick={() => onMarkProblem("EVENT", event.id, event.title)}
              >
                标记问题
              </button>
            </div>
          </article>
        ))}
        {!viewData.events.length && <p className="result-empty">当前没有整理出事件时间线。</p>}
      </div>

      {viewData.event_relations.length > 0 && (
        <section className="relation-section">
          <header>
            <h3>前因与后果</h3>
            <span>{viewData.event_relations.length} 条</span>
          </header>
          {viewData.event_relations.map((relation) => (
            <article key={`${relation.source_event_id}-${relation.target_event_id}`}>
              <div className="relation-object-links">
                <button
                  type="button"
                  onClick={() => onNavigateItem("events", relation.source_event_id)}
                >
                  {relation.source_title}
                </button>
                <span>→</span>
                <button
                  type="button"
                  onClick={() => onNavigateItem("events", relation.target_event_id)}
                >
                  {relation.target_title}
                </button>
              </div>
              <span>{EVENT_RELATION_LABELS[relation.relation] ?? "关联方式待核对"}</span>
              <p>{relation.explanation}</p>
              {renderEvidenceButtons(relation.evidence_ids)}
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  onMarkProblem(
                    "RELATION",
                    null,
                    `${relation.source_title}与${relation.target_title}的因果关系`,
                  )
                }
              >
                标记问题
              </button>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
