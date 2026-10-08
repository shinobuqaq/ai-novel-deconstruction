import React from "react";
import type { AnalysisRun, Workbench } from "../../api";
import {
  CHARACTER_DESIGN_FIELD_LABELS,
  CHARACTER_DESIGN_STATUS_LABELS,
  ROLE_LABELS,
  ROLE_ORDER,
} from "./constants";

interface CharacterListProps {
  viewData: Workbench;
  analysisStatus: AnalysisRun["status"];
  isHistoricalRevision: boolean;
  busy: string;
  identityBusy: string;
  identityError: string;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
  onNavigateItem: (view: string, id: string) => void;
  onDecidePersonIdentity: (pairKey: string, decision: "SAME" | "DIFFERENT") => void;
  onUndoPersonIdentityDecision: (decisionId: string) => void;
  onStartCharacterDesign: (force: boolean) => void;
}

export function CharacterList({
  viewData,
  analysisStatus,
  isHistoricalRevision,
  busy,
  identityBusy,
  identityError,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
  onNavigateItem,
  onDecidePersonIdentity,
  onUndoPersonIdentityDecision,
  onStartCharacterDesign,
}: CharacterListProps) {
  const blockingIdentityCandidates = viewData.person_identity_candidates.filter(
    (item) => item.review_priority === "BLOCKING",
  );
  const optionalIdentityCandidates = viewData.person_identity_candidates.filter(
    (item) => item.review_priority === "OPTIONAL",
  );
  const primaryIdentityCandidates = blockingIdentityCandidates.slice(0, 8);
  const remainingBlockingIdentityCandidates = blockingIdentityCandidates.slice(8);

  const characterGroups = ROLE_ORDER.map((role) => ({
    role,
    label: ROLE_LABELS[role] ?? role,
    characters: viewData.characters.filter((item) => item.role === role),
  })).filter((group) => group.characters.length > 0);

  const characterForName = (name: string) =>
    viewData.characters.find(
      (item) => item.name === name || item.aliases.includes(name),
    );

  const characterDesign = viewData.character_design_evidence;
  const characterDesignReadiness = viewData.learning_report.readiness.checks.find(
    (item) => item.question_id === "2.2",
  );

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

  const renderIdentityCandidateCard = (
    candidate: Workbench["person_identity_candidates"][number],
  ) => (
    <article className="identity-candidate-card" key={candidate.candidate_key}>
      <div className="identity-candidate-names">
        <strong>{candidate.left_name}</strong>
        <span>可能是同一人</span>
        <strong>{candidate.right_name}</strong>
      </div>
      <p>{candidate.reason}</p>
      <small>
        {candidate.review_priority === "BLOCKING"
          ? "完成这项确认后才能最终确认拆解"
          : "这项不阻止最终确认，可按重要性抽查"}
        {candidate.recommended_decision === "SAME"
          ? ` · 若为同一人，建议统一显示为“${candidate.recommended_name}”`
          : " · 结构线索更支持保留为不同人物"}
        {` · 候选置信度 ${candidate.confidence}%`}
      </small>
      {candidate.cooccurrence_count > 0 && (
        <small className="identity-warning">
          有 {candidate.cooccurrence_count} 个事件同时提到这两个名字，请优先查看原文再决定。
        </small>
      )}
      <div className="identity-signal-list">
        {candidate.signals.map((signal) => (
          <span key={signal}>{signal}</span>
        ))}
      </div>
      {renderEvidenceButtons(candidate.evidence_ids, "查看身份依据")}
      {!isHistoricalRevision && analysisStatus !== "CONFIRMED" && (
        <div className="identity-candidate-actions">
          {candidate.recommended_decision === "SAME" ? (
            <>
              <button
                type="button"
                disabled={Boolean(identityBusy)}
                onClick={() => onDecidePersonIdentity(candidate.candidate_key, "SAME")}
              >
                {identityBusy === candidate.candidate_key ? "正在保存" : "确认为同一人"}
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={Boolean(identityBusy)}
                onClick={() => onDecidePersonIdentity(candidate.candidate_key, "DIFFERENT")}
              >
                确认是不同人物
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                disabled={Boolean(identityBusy)}
                onClick={() => onDecidePersonIdentity(candidate.candidate_key, "DIFFERENT")}
              >
                {identityBusy === candidate.candidate_key ? "正在保存" : "建议：确认为不同人物"}
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={Boolean(identityBusy)}
                onClick={() => onDecidePersonIdentity(candidate.candidate_key, "SAME")}
              >
                仍确认为同一人
              </button>
            </>
          )}
        </div>
      )}
    </article>
  );

  return (
    <div className="character-workbench-view">
      {(viewData.person_identity_candidates.length > 0 ||
        viewData.person_identity_decisions.length > 0) && (
        <section className="identity-review-panel" aria-label="人物身份候选">
          <header>
            <div>
              <span>人物身份校对</span>
              <h3>
                {viewData.person_identity_candidates.length > 0
                  ? `${blockingIdentityCandidates.length} 组需要确认 · ${optionalIdentityCandidates.length} 组可选抽查`
                  : "人物身份候选已经处理"}
              </h3>
            </div>
            <b>{viewData.person_identity_decisions.length} 项已裁决</b>
          </header>
          <p>
            这里只列出系统有直接别名线索、但不敢自动合并的人物。确认后，事件、剧情、关系和事实状态会同步使用统一姓名；原始模型结果不会被改写。
          </p>
          {identityError && <div className="inline-error">{identityError}</div>}
          {primaryIdentityCandidates.map(renderIdentityCandidateCard)}
          {remainingBlockingIdentityCandidates.length > 0 && (
            <details className="identity-optional-candidates">
              <summary>展开其余 {remainingBlockingIdentityCandidates.length} 组需要确认的候选</summary>
              <div>{remainingBlockingIdentityCandidates.map(renderIdentityCandidateCard)}</div>
            </details>
          )}
          {optionalIdentityCandidates.length > 0 && (
            <details className="identity-optional-candidates">
              <summary>展开 {optionalIdentityCandidates.length} 组可选抽查候选</summary>
              <div>{optionalIdentityCandidates.map(renderIdentityCandidateCard)}</div>
            </details>
          )}
          {viewData.person_identity_decisions.length > 0 && (
            <div className="identity-decision-list">
              <strong>已保存的身份裁决</strong>
              {viewData.person_identity_decisions.map((decision) => (
                <div key={decision.id}>
                  <span>
                    {decision.decision === "SAME"
                      ? `同一人：${decision.left_name}、${decision.right_name} → ${decision.canonical_name}`
                      : `不同人物：${decision.left_name}、${decision.right_name}`}
                  </span>
                  {!isHistoricalRevision && analysisStatus !== "CONFIRMED" && (
                    <button
                      type="button"
                      className="text-action"
                      disabled={Boolean(identityBusy)}
                      onClick={() => onUndoPersonIdentityDecision(decision.id)}
                    >
                      {identityBusy === decision.id ? "正在撤销" : "撤销"}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <div className="character-role-summary">
        <strong>按人物在故事中的作用分组</strong>
        <span>角色定位结合人物目标、关键事件、人物关系和剧情作用；出现次数只作为辅助信息。</span>
        <div>
          {characterGroups.map((group) => (
            <span key={group.role}>
              {group.label} {group.characters.length}
            </span>
          ))}
        </div>
      </div>

      {characterGroups.map((group) => (
        <section className={`character-role-group role-${group.role.toLocaleLowerCase()}`} key={group.role}>
          <header>
            <div>
              <span>人物层级</span>
              <h3>{group.label}</h3>
            </div>
            <b>{group.characters.length} 人</b>
          </header>
          <div className="formal-card-list">
            {group.characters.map((character) => (
              <article
                className={`formal-card${focusClass(character.id)}`}
                data-workbench-id={character.id}
                key={character.id}
              >
                <header>
                  <div>
                    <span>{ROLE_LABELS[character.role] ?? "人物"}</span>
                    <h3>{character.name}</h3>
                  </div>
                  <i
                    className={
                      character.status === "UNCERTAIN" ||
                      (character.role_required && character.role === "UNCLASSIFIED")
                        ? "needs-review"
                        : ""
                    }
                  >
                    {character.status === "UNCERTAIN"
                      ? "身份待抽查"
                      : character.role === "UNCLASSIFIED"
                      ? character.role_required
                        ? "角色作用待确认"
                        : "背景层暂未深挖"
                      : "角色定位已生成"}
                  </i>
                </header>
                <p>{character.description || "原文中已识别到该人物。"}</p>
                <small>{character.role_reason}</small>
                {character.identity_notes.map((note) => (
                  <small className="identity-note" key={note}>
                    {note}
                  </small>
                ))}
                {character.aliases.length > 0 && (
                  <small>别名或称谓：{character.aliases.join("、")}</small>
                )}
                {character.identities.length > 0 && (
                  <small>身份：{character.identities.join("、")}</small>
                )}
                {character.goals.length > 0 && (
                  <small>目标：{character.goals.join("、")}</small>
                )}
                {character.motivations.length > 0 && (
                  <small>动机：{character.motivations.join("、")}</small>
                )}
                {character.abilities.length > 0 && (
                  <small>能力：{character.abilities.join("、")}</small>
                )}
                {character.secrets.length > 0 && (
                  <small>秘密：{character.secrets.join("、")}</small>
                )}
                {character.important_experiences.length > 0 && (
                  <small>重要经历：{character.important_experiences.join("；")}</small>
                )}
                {character.current_state && (
                  <small>当前状态：{character.current_state}</small>
                )}
                {character.arc_summary && (
                  <p>
                    <strong>人物变化：</strong>
                    {character.arc_summary}
                  </p>
                )}
                <div className="character-meta">
                  <span>出场活跃度：{character.activity_level}</span>
                  <span>人物识别置信度：{character.confidence}%</span>
                  <span>证据 {character.appearance_count} 处</span>
                  <span>
                    {character.first_chapter_ordinal
                      ? `第 ${character.first_chapter_ordinal} 章首次出现`
                      : "章节位置待定"}
                  </span>
                  <span>{character.event_ids.length} 个相关事件</span>
                </div>
                {character.event_ids.length > 0 && (
                  <div className="association-links">
                    <strong>相关事件</strong>
                    {character.event_ids.map((eventId) => {
                      const relatedEvent = viewData.events.find((item) => item.id === eventId);
                      return relatedEvent ? (
                        <button
                          type="button"
                          key={eventId}
                          onClick={() => onNavigateItem("events", eventId)}
                        >
                          {relatedEvent.title}
                        </button>
                      ) : null;
                    })}
                  </div>
                )}
                {renderEvidenceButtons(character.evidence_ids)}
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => onMarkProblem("CHARACTER", character.id, character.name)}
                >
                  标记问题
                </button>
              </article>
            ))}
          </div>
        </section>
      ))}

      {!viewData.characters.length && <p className="result-empty">当前没有整理出人物档案。</p>}

      {viewData.character_relations.length > 0 && (
        <section className="relation-section">
          <header>
            <h3>人物关系</h3>
            <span>{viewData.character_relations.length} 条</span>
          </header>
          {viewData.character_relations.map((relation, index) => (
            <article key={`${relation.source_name}-${relation.target_name}-${index}`}>
              <div className="relation-object-links">
                {[relation.source_name, relation.target_name].map((name, nameIndex) => {
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
                    <strong key={`${name}-${nameIndex}`}>{name}</strong>
                  );
                })}
              </div>
              <span>{relation.relation}</span>
              <p>{relation.current_state || "关系状态待补充"}</p>
              {relation.changes.length > 0 && (
                <small>变化摘要：{relation.changes.join("；")}</small>
              )}
              {relation.change_history.length > 0 && (
                <div className="relation-change-history">
                  {relation.change_history.map((change, changeIndex) => (
                    <div key={`${change.chapter_ordinal}-${changeIndex}`}>
                      <b>第 {change.chapter_ordinal} 章</b>
                      <span>
                        {change.before ? `${change.before} → ` : ""}
                        {change.after}
                      </span>
                      {change.trigger_event_id &&
                        (() => {
                          const event = viewData.events.find(
                            (item) => item.id === change.trigger_event_id,
                          );
                          return event ? (
                            <button
                              type="button"
                              onClick={() => onNavigateItem("events", event.id)}
                            >
                              触发事件：{event.title}
                            </button>
                          ) : null;
                        })()}
                      {renderEvidenceButtons(change.evidence_ids, "查看变化依据")}
                    </div>
                  ))}
                </div>
              )}
              {renderEvidenceButtons(relation.evidence_ids)}
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  onMarkProblem("RELATION", null, `${relation.source_name}与${relation.target_name}的关系`)
                }
              >
                标记问题
              </button>
            </article>
          ))}
        </section>
      )}

      <section className="evidence-ledger-section character-design-ledger">
        <header>
          <div>
            <span>北极星 2.2 · 专项证据</span>
            <h3>主角双层欲望与最小完整集</h3>
          </div>
          <p>学习答案页只显示结论；六项人物要素的逐项依据集中保存在这里。</p>
        </header>
        <div className={`character-design-state ${viewData.character_design_status.toLowerCase()}`}>
          <div>
            <strong>
              {CHARACTER_DESIGN_STATUS_LABELS[viewData.character_design_status] ?? "状态未知"}
            </strong>
            <span>
              {characterDesignReadiness?.gaps.join("；") || "人物专项证据已经通过当前合同检查。"}
            </span>
          </div>
          {!isHistoricalRevision &&
            viewData.deep_status === "READY" &&
            viewData.character_design_status !== "GENERATING" && (
              <button
                type="button"
                disabled={busy === "start-character-design"}
                onClick={() => onStartCharacterDesign(viewData.character_design_status === "READY")}
              >
                {busy === "start-character-design"
                  ? "正在准备"
                  : viewData.character_design_status === "READY"
                  ? "重新分析 2.2"
                  : "生成 2.2 证据表"}
              </button>
            )}
        </div>
        {characterDesign && (
          <>
            <div className="character-design-coverage">
              <div>
                <strong>
                  {characterDesign.coverage.covered_event_count}/
                  {characterDesign.coverage.protagonist_event_count}
                </strong>
                <span>主角事件已纳入</span>
              </div>
              <div>
                <strong>{characterDesign.coverage.source_chapter_count}</strong>
                <span>全书章节</span>
              </div>
              <div>
                <strong>{characterDesign.coverage.first_30_chapter_event_count}</strong>
                <span>前 30 章主角事件</span>
              </div>
              <div>
                <strong>第 {characterDesign.revision} 版</strong>
                <span>{characterDesign.is_current ? "对应当前拆解" : "旧版结果"}</span>
              </div>
            </div>
            <div className="character-design-fields">
              {characterDesign.fields.map((item) => (
                <article
                  className={item.status === "SUPPORTED" ? "supported" : "insufficient"}
                  key={`evidence-${item.field}`}
                >
                  <header>
                    <strong>{CHARACTER_DESIGN_FIELD_LABELS[item.field] ?? item.field}</strong>
                    <span>
                      {item.status === "SUPPORTED"
                        ? `第 ${item.first_display_chapter_ordinal} 章首次展示`
                        : "证据不足"}
                    </span>
                  </header>
                  <h4>{item.value || "当前材料无法形成可靠判断"}</h4>
                  {item.display_event && <p>{item.display_event}</p>}
                  <small>{item.explanation}</small>
                  {renderEvidenceButtons(item.evidence_ids, "查看首次展示原文")}
                </article>
              ))}
            </div>
            <details className="ledger-detail">
              <summary>
                查看人物弧光与 {characterDesign.desire_conflicts.length} 个双层欲望冲突节点
              </summary>
              <p>{characterDesign.arc_summary}</p>
              {characterDesign.desire_conflicts.map((item, index) => (
                <article key={`evidence-conflict-${item.event_id}-${index}`}>
                  <strong>第 {item.chapter_ordinal} 章</strong>
                  <p>表层：{item.surface_desire}</p>
                  <p>深层：{item.deep_desire}</p>
                  <p>动机原文：{item.motive}</p>
                  <p>选择与结果原文：{item.choice}；{item.result}</p>
                  <p>代价与变化：{item.sacrifice}；{item.arc_change}</p>
                  {renderEvidenceButtons(item.motive_evidence_ids, "查看欲望与动机原文")}
                  {renderEvidenceButtons(item.choice_evidence_ids, "查看实际选择原文")}
                  {renderEvidenceButtons(item.result_evidence_ids, "查看现场结果原文")}
                  {renderEvidenceButtons(item.sacrifice_evidence_ids, "查看选择代价原文")}
                </article>
              ))}
            </details>
          </>
        )}
      </section>
    </div>
  );
}
