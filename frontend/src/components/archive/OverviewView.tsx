import React from "react";
import type { Workbench } from "../../api";
import { CLAIM_STATUS_LABELS, ENTITY_LABELS } from "./constants";

interface OverviewViewProps {
  viewData: Workbench;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
}

export function OverviewView({
  viewData,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
}: OverviewViewProps) {
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
    <>
      <div className="workbench-callout">
        <strong>{viewData.story_overview ? "先用故事骨架理解这部分小说" : "完整故事骨架尚未生成"}</strong>
        <span>
          {viewData.story_overview
            ? "总览、人物角色和剧情阶段由原文证据整理而来；仍有争议的内容会标明，不会被悄悄当成确定事实。"
            : "当前页面只保留基础抽取结果，系统会在叙事综合完成后显示故事总览。"}
        </span>
      </div>

      {viewData.story_overview && (
        <article className="overview-story-card">
          <span className="section-kicker">故事总览</span>
          <h3>{viewData.story_overview.premise}</h3>
          <p>{viewData.story_overview.synopsis}</p>
          <dl className="overview-facts">
            <div>
              <dt>主角</dt>
              <dd>{viewData.story_overview.protagonist}</dd>
            </div>
            <div>
              <dt>当前目标</dt>
              <dd>{viewData.story_overview.protagonist_goal || "证据不足"}</dd>
            </div>
            <div>
              <dt>核心冲突</dt>
              <dd>{viewData.story_overview.central_conflict || "证据不足"}</dd>
            </div>
            <div>
              <dt>开局局面</dt>
              <dd>{viewData.story_overview.opening_situation || "证据不足"}</dd>
            </div>
            <div>
              <dt>当前局面</dt>
              <dd>{viewData.story_overview.current_situation || "证据不足"}</dd>
            </div>
            <div>
              <dt>当前结果</dt>
              <dd>{viewData.story_overview.current_result || "证据不足"}</dd>
            </div>
          </dl>
          {viewData.story_overview.development_path.length > 0 && (
            <section className="story-progression">
              <strong>故事如何发展</strong>
              <ol>
                {viewData.story_overview.development_path.map((item, index) => (
                  <li key={`${index}-${item}`}>
                    <span>{index + 1}</span>
                    <p>{item}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}
          {viewData.story_overview.turning_points.length > 0 && (
            <section className="turning-points">
              <strong>关键转折</strong>
              {viewData.story_overview.turning_points.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </section>
          )}
          {viewData.story_overview.unresolved_questions.length > 0 && (
            <div className="overview-questions">
              <strong>未解决问题</strong>
              {viewData.story_overview.unresolved_questions.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
          )}
          {renderEvidenceButtons(viewData.story_overview.evidence_ids)}
          <button
            type="button"
            className="secondary-button"
            onClick={() => onMarkProblem("STORY", null, "故事总览")}
          >
            标记问题
          </button>
        </article>
      )}

      <div className="phase-overview-list">
        {viewData.phases.map((phase, index) => (
          <article
            className={`phase-card${focusClass(phase.id)}`}
            data-workbench-id={phase.id}
            key={phase.id}
          >
            <header>
              <span>阶段 {index + 1}</span>
              <h3>{phase.title}</h3>
            </header>
            <p>{phase.summary}</p>
            {phase.people.length > 0 && <small>参与人物：{phase.people.join("、")}</small>}
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

      <div className="related-entity-summary">
        <header>
          <h3>地点、组织与重要事物</h3>
          <span>{viewData.related_entities.length} 个</span>
        </header>
        <div>
          {viewData.related_entities.map((entity) => (
            <span key={entity.id}>
              {ENTITY_LABELS[entity.entity_type]}：{entity.name}
            </span>
          ))}
        </div>
      </div>

      {viewData.deep_analysis?.claims.length ? (
        <section className="insight-group overview-claims">
          <header>
            <div>
              <span>带证据的专项判断</span>
              <h3>分析结论</h3>
            </div>
            <b>{viewData.deep_analysis.claims.length}</b>
          </header>
          <div className="formal-card-list compact-list">
            {viewData.deep_analysis.claims.map((claim) => (
              <article
                className={`formal-card${focusClass(claim.id)}`}
                data-workbench-id={claim.id}
                key={claim.id}
              >
                <header>
                  <div>
                    <span>
                      {claim.claim_kind === "FACT"
                        ? "事实判断"
                        : claim.claim_kind === "INFERENCE"
                        ? "推断"
                        : claim.claim_kind === "PATTERN"
                        ? "叙事模式"
                        : "解释"}
                    </span>
                    <h3>{claim.claim_text}</h3>
                  </div>
                  <i
                    className={
                      claim.verification_status !== "SUPPORTED" ? "needs-review" : ""
                    }
                  >
                    {CLAIM_STATUS_LABELS[claim.verification_status] ?? "待核验"}
                  </i>
                </header>
                <small>适用范围：{claim.scope}</small>
                {claim.verification_note && <small>{claim.verification_note}</small>}
                {renderEvidenceButtons(claim.evidence_ids, "查看支持证据")}
                {claim.counter_evidence_ids.length > 0 &&
                  renderEvidenceButtons(claim.counter_evidence_ids, "查看反面证据")}
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => onMarkProblem("CLAIM", claim.id, claim.claim_text)}
                >
                  标记问题
                </button>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}
