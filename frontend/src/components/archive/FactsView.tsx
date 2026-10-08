import React from "react";
import type { Workbench, WorkbenchStateAtChapter } from "../../api";
import {
  FACT_TIMELINE_LABELS,
  FACT_TYPE_LABELS,
  KNOWLEDGE_LABELS,
  KNOWLEDGE_TRANSFER_LABELS,
} from "./constants";

interface FactsViewProps {
  viewData: Workbench;
  stateChapter: number;
  onStateChapterChange: (chapter: number) => void;
  stateProjection: WorkbenchStateAtChapter | null;
  stateProjectionBusy: boolean;
  stateProjectionError: string;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
}

export function FactsView({
  viewData,
  stateChapter,
  onStateChapterChange,
  stateProjection,
  stateProjectionBusy,
  stateProjectionError,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
}: FactsViewProps) {
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

  const pointInTime = {
    facts:
      stateProjection?.facts ??
      viewData.deep_analysis?.fact_versions.filter((item) => item.timeline_status === "ACTIVE") ??
      [],
    states:
      stateProjection?.states ??
      viewData.deep_analysis?.state_changes.filter(
        (item) => item.chapter_ordinal <= stateChapter,
      ) ??
      [],
    knowledge:
      stateProjection?.knowledge ??
      viewData.deep_analysis?.actor_knowledge.filter(
        (item) => item.chapter_ordinal <= stateChapter,
      ) ??
      [],
    knowledge_transfers:
      stateProjection?.knowledge_transfers ??
      viewData.deep_analysis?.knowledge_transfers.filter(
        (item) => item.chapter_ordinal <= stateChapter,
      ) ??
      [],
  };

  return (
    <div className="deep-analysis-view">
      {!viewData.deep_analysis ? (
        <div className="workbench-callout">
          <strong>事实与状态仍在整理</strong>
          <span>系统完成证据校验后，会在这里显示世界事实、状态变化和人物认知。</span>
        </div>
      ) : (
        <>
          <div className="chapter-state-selector">
            <div>
              <strong>查看指定章节时的状态</strong>
              <span>后面的章节不会提前泄露到这个视图。</span>
            </div>
            <label htmlFor="state-chapter">截至</label>
            <select
              id="state-chapter"
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
          {stateProjectionBusy && (
            <div className="workbench-callout">
              <strong>正在重放这一章的状态</strong>
              <span>系统正在统一核对事实有效期、状态变化和人物认知。</span>
            </div>
          )}
          {stateProjectionError && (
            <div className="analysis-issue-error">{stateProjectionError}</div>
          )}
          <section className="insight-group">
            <header>
              <div>
                <span>第 {stateChapter} 章时仍然成立</span>
                <h3>世界事实</h3>
              </div>
              <b>{pointInTime.facts.length}</b>
            </header>
            <div className="formal-card-list compact-list">
              {pointInTime.facts.map((fact) => (
                <article
                  className={`formal-card${focusClass(fact.id)}`}
                  data-workbench-id={fact.id}
                  key={fact.id}
                >
                  <header>
                    <div>
                      <span>{FACT_TYPE_LABELS[fact.fact_type] ?? "事实"}</span>
                      <h3>{fact.subject}</h3>
                    </div>
                    <i
                      className={
                        fact.timeline_status === "CONFLICTING" || fact.status === "UNCERTAIN"
                          ? "needs-review"
                          : ""
                      }
                    >
                      {FACT_TIMELINE_LABELS[fact.timeline_status] ?? "事实版本"}
                    </i>
                  </header>
                  <p>
                    <strong>{fact.predicate}：</strong>
                    {fact.value}
                  </p>
                  <small>
                    有效范围：第 {fact.valid_from_chapter} 章起
                    {fact.valid_to_chapter ? `，至第 ${fact.valid_to_chapter} 章` : "，当前仍成立"}
                  </small>
                  <small>{fact.timeline_note}</small>
                  {renderEvidenceButtons(fact.evidence_ids)}
                  {fact.counter_evidence_ids.length > 0 &&
                    renderEvidenceButtons(fact.counter_evidence_ids, "查看反证")}
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      onMarkProblem("FACT", fact.id, `${fact.subject}：${fact.predicate}`)
                    }
                  >
                    标记问题
                  </button>
                </article>
              ))}
              {!pointInTime.facts.length && (
                <p className="result-empty">截至这一章，没有可确认且仍然成立的世界事实。</p>
              )}
            </div>
          </section>

          <details className="fact-history">
            <summary>
              查看全部事实版本与变化记录（{viewData.deep_analysis.fact_versions.length} 项）
            </summary>
            <div className="timeline-list">
              {[...viewData.deep_analysis.fact_versions]
                .sort(
                  (left, right) =>
                    left.subject.localeCompare(right.subject, "zh-CN") ||
                    left.predicate.localeCompare(right.predicate, "zh-CN") ||
                    left.valid_from_chapter - right.valid_from_chapter,
                )
                .map((fact) => (
                  <article key={`history-${fact.id}`}>
                    <span>
                      第 {fact.valid_from_chapter} 章
                      {fact.valid_to_chapter ? `-${fact.valid_to_chapter}` : "起"}
                    </span>
                    <div>
                      <h4>
                        {fact.subject} · {fact.predicate}
                      </h4>
                      <p>{fact.value}</p>
                      <small>
                        {FACT_TIMELINE_LABELS[fact.timeline_status] ?? "事实版本"}：
                        {fact.timeline_note}
                      </small>
                      {renderEvidenceButtons(fact.evidence_ids)}
                      {fact.counter_evidence_ids.length > 0 &&
                        renderEvidenceButtons(fact.counter_evidence_ids, "查看反证")}
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() =>
                          onMarkProblem("FACT", fact.id, `${fact.subject}：${fact.predicate}`)
                        }
                      >
                        标记问题
                      </button>
                    </div>
                  </article>
                ))}
            </div>
          </details>

          <section className="insight-group">
            <header>
              <div>
                <span>每项只显示截至当前章节的最新状态</span>
                <h3>人物与事物状态</h3>
              </div>
              <b>{pointInTime.states.length}</b>
            </header>
            <div className="timeline-list">
              {pointInTime.states.map((change) => (
                <article key={change.id}>
                  <span>第 {change.chapter_ordinal} 章</span>
                  <div>
                    <h4>
                      {change.subject} · {change.aspect}
                    </h4>
                    <p>{change.before ? `${change.before} → ${change.after}` : change.after}</p>
                    {renderEvidenceButtons(change.evidence_ids)}
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        onMarkProblem("STATE", change.id, `${change.subject}：${change.aspect}`)
                      }
                    >
                      标记问题
                    </button>
                  </div>
                </article>
              ))}
              {!pointInTime.states.length && (
                <p className="result-empty">截至这一章，没有识别到可靠的状态变化。</p>
              )}
            </div>
          </section>

          <section className="insight-group">
            <header>
              <div>
                <span>不是上帝视角</span>
                <h3>人物当时知道什么</h3>
              </div>
              <b>{pointInTime.knowledge.length}</b>
            </header>
            <div className="knowledge-grid">
              {pointInTime.knowledge.map((knowledge) => (
                <article key={knowledge.id}>
                  <span>{KNOWLEDGE_LABELS[knowledge.state] ?? "认知状态"}</span>
                  <h4>{knowledge.actor}</h4>
                  <p>{knowledge.proposition}</p>
                  <small>截至第 {knowledge.chapter_ordinal} 章</small>
                  {renderEvidenceButtons(knowledge.evidence_ids)}
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      onMarkProblem(
                        "KNOWLEDGE",
                        knowledge.id,
                        `${knowledge.actor}：${knowledge.proposition}`,
                      )
                    }
                  >
                    标记问题
                  </button>
                </article>
              ))}
              {!pointInTime.knowledge.length && (
                <p className="result-empty">截至这一章，没有足够证据区分人物认知。</p>
              )}
            </div>
          </section>

          <section className="insight-group">
            <header>
              <div>
                <span>见证、告知、误传与撤回</span>
                <h3>信息如何传到人物手中</h3>
              </div>
              <b>{pointInTime.knowledge_transfers.length}</b>
            </header>
            <div className="timeline-list knowledge-transfer-list">
              {pointInTime.knowledge_transfers.map((transfer) => (
                <article key={transfer.id}>
                  <span>第 {transfer.chapter_ordinal} 章</span>
                  <div>
                    <h4>
                      {transfer.source_actor} → {transfer.target_actor}
                    </h4>
                    <p>{transfer.proposition}</p>
                    <small>
                      {KNOWLEDGE_TRANSFER_LABELS[transfer.transfer_type] ?? "信息传播"}；结果：
                      {KNOWLEDGE_LABELS[transfer.resulting_state] ?? "认知状态待核对"}
                    </small>
                    {renderEvidenceButtons(transfer.evidence_ids)}
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        onMarkProblem(
                          "KNOWLEDGE",
                          transfer.id,
                          `${transfer.source_actor}传给${transfer.target_actor}的信息`,
                        )
                      }
                    >
                      标记问题
                    </button>
                  </div>
                </article>
              ))}
              {!pointInTime.knowledge_transfers.length && (
                <p className="result-empty">
                  截至这一章，没有足够证据还原人物之间的信息传播过程。
                </p>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
