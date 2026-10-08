import React from "react";
import type { Workbench } from "../../api";
import {
  EVENT_DISCOVERY_ROUTE_LABELS,
  EVENT_LABELS,
  NARRATIVE_MODE_LABELS,
} from "./constants";

interface EventsViewProps {
  viewData: Workbench;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
  onNavigateItem: (view: string, id: string) => void;
}

export function EventsView({
  viewData,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
  onNavigateItem,
}: EventsViewProps) {
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
    <div className="formal-card-list">
      {viewData.events.map((event) => (
        <article
          className={`formal-card event-formal-card${focusClass(event.id)}`}
          data-workbench-id={event.id}
          key={event.id}
        >
          <header>
            <div>
              <span>{EVENT_LABELS[event.event_type] ?? "事件"}</span>
              <h3>{event.title}</h3>
            </div>
            <i className={event.status === "UNCERTAIN" ? "needs-review" : ""}>
              {event.status === "UNCERTAIN"
                ? "待抽查"
                : event.chapter_titles.join("、") || "章节待定"}
            </i>
          </header>
          <p>{event.summary}</p>
          <div className="event-nature">
            <strong>{NARRATIVE_MODE_LABELS[event.narrative_mode] ?? "性质待确认"}</strong>
            <span>{event.boundary_note}</span>
          </div>
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
          {event.related_entities.length > 0 && (
            <small>相关地点、组织或事物：{event.related_entities.join("、")}</small>
          )}
          {event.location && <small>发生地点：{event.location}</small>}
          <dl className="event-detail-grid">
            {event.trigger && (
              <div>
                <dt>起因</dt>
                <dd>{event.trigger}</dd>
              </div>
            )}
            {event.process && (
              <div>
                <dt>过程</dt>
                <dd>{event.process}</dd>
              </div>
            )}
            {event.outcome && (
              <div>
                <dt>结果</dt>
                <dd>{event.outcome}</dd>
              </div>
            )}
            {event.impact && (
              <div>
                <dt>后续影响</dt>
                <dd>{event.impact}</dd>
              </div>
            )}
          </dl>
          <div className="character-meta">
            <span>{event.chapter_titles.join("、") || "章节待定"}</span>
            <span>原文依据 {event.evidence_ids.length} 处</span>
            <span>
              {event.discovery_routes.length > 0
                ? `发现路线：${event.discovery_routes
                    .map((route) => EVENT_DISCOVERY_ROUTE_LABELS[route] ?? "语义识别")
                    .join("、")}`
                : "旧版本未记录发现路线"}
            </span>
            <span>置信度 {event.confidence}%</span>
            {event.mention_count > 1 && <span>合并 {event.mention_count} 次提及</span>}
          </div>
          {renderEvidenceButtons(event.evidence_ids)}
          <button
            type="button"
            className="secondary-button"
            onClick={() => onMarkProblem("EVENT", event.id, event.title)}
          >
            标记问题
          </button>
        </article>
      ))}
      {!viewData.events.length && <p className="result-empty">当前没有整理出事件。</p>}
    </div>
  );
}
