import React from "react";
import type { Workbench } from "../../api";

interface WorldViewProps {
  viewData: Workbench;
  stateChapter: number;
  onStateChapterChange: (chapter: number) => void;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
}

export function WorldView({
  viewData,
  stateChapter,
  onStateChapterChange,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
}: WorldViewProps) {
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

  const worldRules =
    viewData.deep_analysis?.world_rules.filter(
      (item) => item.discovered_chapter <= stateChapter,
    ) ?? [];

  return (
    <div className="deep-analysis-view">
      {!viewData.deep_analysis ? (
        <div className="workbench-callout">
          <strong>世界设定仍在整理</strong>
          <span>
            系统完成证据校验后，会在这里显示地点、组织、能力、限制、代价和例外。
          </span>
        </div>
      ) : (
        <>
          <div className="chapter-state-selector">
            <div>
              <strong>按章节查看当时已经出现的设定</strong>
              <span>后面的章节不会提前泄露到这个视图。</span>
            </div>
            <label htmlFor="world-state-chapter">截至</label>
            <select
              id="world-state-chapter"
              value={stateChapter}
              onChange={(event) => onStateChapterChange(Number(event.target.value))}
            >
              {viewData.chapters.map((chapter) => (
                <option value={chapter.ordinal} key={chapter.ordinal}>
                  第 {chapter.ordinal} 章 · {chapter.title}
                </option>
              ))}
            </select>
          </div>
          <section className="insight-group">
            <header>
              <div>
                <span>限制、代价和例外都会保留</span>
                <h3>世界规则</h3>
              </div>
              <b>{worldRules.length}</b>
            </header>
            <div className="formal-card-list compact-list">
              {worldRules.map((rule) => (
                <article
                  className={`formal-card${focusClass(rule.id)}`}
                  data-workbench-id={rule.id}
                  key={rule.id}
                >
                  <header>
                    <div>
                      <span>世界设定</span>
                      <h3>{rule.title}</h3>
                    </div>
                    <i>第 {rule.discovered_chapter} 章起可知</i>
                  </header>
                  <p>{rule.description}</p>
                  {rule.limitations.length > 0 && (
                    <small>限制：{rule.limitations.join("；")}</small>
                  )}
                  {rule.costs.length > 0 && <small>代价：{rule.costs.join("；")}</small>}
                  {rule.exceptions.length > 0 && (
                    <small>例外：{rule.exceptions.join("；")}</small>
                  )}
                  {renderEvidenceButtons(rule.evidence_ids)}
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => onMarkProblem("WORLD", rule.id, rule.title)}
                  >
                    标记问题
                  </button>
                </article>
              ))}
              {!worldRules.length && (
                <p className="result-empty">截至这一章，原文尚未明确建立世界规则。</p>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
