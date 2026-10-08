import { useState } from "react";
import type {
  WorkbenchLearningReport,
  WorkbenchLearningQuestion,
} from "./api";

// ── Types ──────────────────────────────────────────────────────────────────

interface HandbookData {
  why_important: string;
  universal_methods: string[];
  checklist: string[];
  common_errors: Array<{ mistake: string; fix: string }>;
  templates: Array<{ name: string; structure: string; checkpoints: string[] }>;
  genre_note: string;
}

interface LearningHandbookProps {
  learningReport: WorkbenchLearningReport;
  activeLearningQuestionId: string;
  onSelectQuestion: (id: string) => void;
  evidenceButtons: (ids: string[], label?: string) => React.ReactNode;
  searchQuery?: string;
}

// ── Constants ──────────────────────────────────────────────────────────────

const ANSWER_STATUS_LABEL: Record<string, string> = {
  ANSWERED: "完整回答",
  PARTIAL: "部分回答",
  INSUFFICIENT_EVIDENCE: "原料不足",
  READY_TO_GENERATE: "可生成",
  OUTDATED: "已过期",
  NOT_GENERATED: "尚无答案",
};

const ANSWER_DOT_CLASS: Record<string, string> = {
  ANSWERED: "hb-dot-green",
  PARTIAL: "hb-dot-green",
  INSUFFICIENT_EVIDENCE: "hb-dot-gray",
  READY_TO_GENERATE: "hb-dot-yellow",
  OUTDATED: "hb-dot-yellow",
  NOT_GENERATED: "hb-dot-gray",
};

const BADGE_CLASS: Record<string, string> = {
  ANSWERED: "hb-badge-green",
  PARTIAL: "hb-badge-green",
  INSUFFICIENT_EVIDENCE: "hb-badge-gray",
  READY_TO_GENERATE: "hb-badge-yellow",
  OUTDATED: "hb-badge-yellow",
  NOT_GENERATED: "hb-badge-gray",
};

// ── QuestionContent ────────────────────────────────────────────────────────

function QuestionContent({
  question,
  evidenceButtons,
}: {
  question: WorkbenchLearningQuestion;
  evidenceButtons: LearningHandbookProps["evidenceButtons"];
}) {
  const hb = question.handbook as HandbookData | null;
  const hasAnswer =
    question.has_current_answer ||
    question.answer_status === "ANSWERED" ||
    question.answer_status === "PARTIAL";

  const hasToolkit =
    (hb?.checklist?.length ?? 0) > 0 ||
    (hb?.common_errors?.length ?? 0) > 0 ||
    (hb?.templates?.length ?? 0) > 0;

  const supportedCount = question.contract_items.filter(
    (i) => i.status === "SUPPORTED",
  ).length;

  return (
    <article className="hb-question">
      {/* ── 顶部题头 ── */}
      <header className="hb-question-header">
        <div className="hb-question-meta">
          <span className="hb-qid-pill">{question.question_id}</span>
          <span className="hb-stage-pill">{question.stage_name}</span>
          <span className={`hb-badge ${BADGE_CLASS[question.answer_status] ?? "hb-badge-gray"}`}>
            {ANSWER_STATUS_LABEL[question.answer_status] ?? question.answer_status}
          </span>
        </div>
        <h1 className="hb-question-title">{question.question}</h1>
      </header>

      {/* ── Studio 双栏协同工作区：左侧核心研读 + 右侧实操检视 ── */}
      <div className="hb-content-grid">
        {/* 左侧/中栏：核心拆解与节律研报 */}
        <div className="hb-column-core">
          {/* 创作心法 / 为什么重要 */}
          {hb?.why_important && (
            <section className="hb-callout-card hb-callout-why">
              <div className="hb-callout-header">
                <span className="hb-callout-icon">💡</span>
                <span className="hb-callout-title">创作心法 · 为什么这个问题至关重要</span>
              </div>
              <p className="hb-callout-text">{hb.why_important}</p>
            </section>
          )}

          {/* 本书叙事提炼与核心答案 */}
          <section className="hb-card hb-card-main">
            <div className="hb-card-header">
              <span className="hb-card-tag">实战拆解</span>
              <h3 className="hb-card-title">本书叙事做法与核心结论</h3>
            </div>

            {hasAnswer ? (
              <>
                <p className="hb-conclusion-lead">{question.conclusion}</p>

                {/* Linear 风格质感节律指标舱 */}
                {question.metrics.length > 0 && (
                  <div className="hb-metrics-grid">
                    {question.metrics.map((m, i) => (
                      <div key={i} className="hb-metric-pod" title={m.method}>
                        <div className="hb-metric-value-wrap">
                          <span className="hb-metric-num">{m.value}</span>
                          {m.unit && <span className="hb-metric-unit">{m.unit}</span>}
                        </div>
                        <div className="hb-metric-lbl">{m.label}</div>
                      </div>
                    ))}
                  </div>
                )}

                {/* 可参考做法 (Emerald Callout) */}
                {question.reusable_lessons.length > 0 && (
                  <div className="hb-callout-card hb-callout-success">
                    <div className="hb-callout-header">
                      <span className="hb-callout-icon">✨</span>
                      <span className="hb-callout-title">可迁移写法建议</span>
                    </div>
                    <ul className="hb-callout-list">
                      {question.reusable_lessons.map((l, i) => (
                        <li key={i}>{l}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 不可照搬 (Rose Callout) */}
                {question.do_not_copy.length > 0 && (
                  <div className="hb-callout-card hb-callout-danger">
                    <div className="hb-callout-header">
                      <span className="hb-callout-icon">⚠️</span>
                      <span className="hb-callout-title">创作者避坑红线（不可照搬）</span>
                    </div>
                    <ul className="hb-callout-list">
                      {question.do_not_copy.map((d, i) => (
                        <li key={i}>{d}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            ) : (
              <div className="hb-no-answer-card">
                <span className="hb-empty-hint-icon">⏳</span>
                <p>该问题的专项方法论研报尚未提炼，点击右上角即可生成。</p>
              </div>
            )}
          </section>

          {/* 通用方法 */}
          {(hb?.universal_methods?.length ?? 0) > 0 && (
            <section className="hb-card hb-card-methods">
              <div className="hb-card-header">
                <span className="hb-card-tag">方法沉淀</span>
                <h3 className="hb-card-title">通用创作规律</h3>
              </div>
              <ul className="hb-methods-list">
                {hb!.universal_methods.map((m, i) => <li key={i}>{m}</li>)}
              </ul>
            </section>
          )}

          {/* 题材差异 */}
          <section className="hb-card hb-card-genre">
            <div className="hb-card-header">
              <span className="hb-card-tag">跨题材考量</span>
              <h3 className="hb-card-title">题材差异与边界</h3>
            </div>
            {hb?.genre_note ? (
              <p className="hb-genre-text">{hb.genre_note}</p>
            ) : (
              <p className="hb-genre-placeholder">
                当前拆解样本为《龙族》，在其他男频爽文或特定流派中，可根据主线升级与金手指节奏适度调整。
              </p>
            )}
          </section>
        </div>

        {/* 右侧栏：实操工具箱与原文证据常驻检视区 */}
        <div className="hb-column-inspector">
          {/* 实操工具包 */}
          {hasToolkit && (
            <section className="hb-inspector-panel hb-toolkit-panel">
              <div className="hb-panel-heading">
                <div className="hb-panel-title-wrap">
                  <span className="hb-panel-icon">🛠️</span>
                  <h4>实操工具包 (Writer's Kit)</h4>
                </div>
                <span className="hb-panel-badge">即学即用</span>
              </div>

              <div className="hb-toolkit-content">
                {/* 自检清单 */}
                {(hb!.checklist?.length ?? 0) > 0 && (
                  <div className="hb-toolkit-section">
                    <h5 className="hb-toolkit-subtitle">✅ 开书自检清单</h5>
                    <ul className="hb-checklist">
                      {hb!.checklist.map((item, i) => (
                        <li key={i}>
                          <label className="hb-check-label">
                            <input type="checkbox" className="hb-checkbox" />
                            <span>{item}</span>
                          </label>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 常见错误 */}
                {(hb!.common_errors?.length ?? 0) > 0 && (
                  <div className="hb-toolkit-section">
                    <h5 className="hb-toolkit-subtitle">❌ 常见新手雷区对照</h5>
                    <div className="hb-error-grid">
                      {hb!.common_errors.map((err, i) => (
                        <div key={i} className="hb-error-card">
                          <div className="hb-err-line hb-err-mistake">
                            <span className="hb-err-tag">雷区</span>
                            <span className="hb-err-text">{err.mistake}</span>
                          </div>
                          <div className="hb-err-line hb-err-fix">
                            <span className="hb-fix-tag">纠偏</span>
                            <span className="hb-fix-text">{err.fix}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 可套模板 */}
                {(hb!.templates?.length ?? 0) > 0 && (
                  <div className="hb-toolkit-section">
                    <h5 className="hb-toolkit-subtitle">📋 结构填空模板</h5>
                    {hb!.templates.map((tmpl, i) => (
                      <div key={i} className="hb-template-card">
                        <div className="hb-template-name">{tmpl.name}</div>
                        <pre className="hb-template-code">{tmpl.structure}</pre>
                        {(tmpl.checkpoints?.length ?? 0) > 0 && (
                          <ul className="hb-template-checkpoints">
                            {tmpl.checkpoints.map((cp, j) => <li key={j}>{cp}</li>)}
                          </ul>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* 原文依据与佐证账本 */}
          <section className="hb-inspector-panel hb-evidence-panel">
            <div className="hb-panel-heading">
              <div className="hb-panel-title-wrap">
                <span className="hb-panel-icon">🔍</span>
                <h4>原文佐证与溯源</h4>
              </div>
              <span className="hb-panel-badge">{supportedCount}/{question.contract_items.length} 支撑</span>
            </div>

            <div className="hb-evidence-content">
              {(question.evidence_ids.length > 0 || question.counter_evidence_ids.length > 0) && (
                <div className="hb-quick-evidence-box">
                  <span className="hb-quick-lbl">关键切片即时对照:</span>
                  <div className="hb-evidence-chips">
                    {evidenceButtons(question.evidence_ids.slice(0, 4), "查看关键原文")}
                    {question.counter_evidence_ids.length > 0 &&
                      evidenceButtons(question.counter_evidence_ids.slice(0, 2), "查看反证")}
                  </div>
                </div>
              )}

              {question.contract_items.length > 0 && (
                <div className="hb-contracts-list">
                  {question.contract_items.map((item) => (
                    <div
                      key={item.item_id}
                      className={`hb-grounding-card ${item.status === "SUPPORTED" ? "is-supported" : "is-gap"}`}
                    >
                      <div className="hb-grounding-header">
                        <strong className="hb-grounding-label">{item.label}</strong>
                        <span className="hb-grounding-status">
                          {item.status === "SUPPORTED" ? "✓ 依据确凿" : "○ 待补数据"}
                        </span>
                      </div>
                      <p className="hb-grounding-finding">{item.finding}</p>
                      {item.evidence_ids.length > 0 && (
                        <div className="hb-grounding-action">
                          {evidenceButtons(item.evidence_ids.slice(0, 2), "定位原文")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </article>
  );
}

export default function LearningHandbook({
  learningReport,
  activeLearningQuestionId,
  onSelectQuestion,
  evidenceButtons,
  searchQuery = "",
}: LearningHandbookProps) {
  const normalizedQuery = searchQuery.trim().toLowerCase();

  // 侧边栏展示已在系统里跟踪的题目，并在有搜索关键词时过滤
  const routeQuestions = learningReport.questions.filter((q) => {
    const isTracked =
      q.has_current_answer ||
      q.answer_status !== "NOT_GENERATED" ||
      q.material_status === "READY" ||
      q.material_status === "OUTDATED";
    if (!isTracked) return false;
    if (!normalizedQuery) return true;
    return (
      q.question.toLowerCase().includes(normalizedQuery) ||
      q.stage_name.toLowerCase().includes(normalizedQuery) ||
      q.question_id.includes(normalizedQuery)
    );
  });

  const activeQuestion =
    routeQuestions.find((q) => q.question_id === activeLearningQuestionId) ??
    routeQuestions[0] ??
    null;

  // 按 stage 分组，只保留有题目的阶段
  const stagesWithQuestions = learningReport.stages
    .map((stage) => ({
      ...stage,
      questions: routeQuestions.filter((q) => q.stage_id === stage.stage_id),
    }))
    .filter((s) => s.questions.length > 0);

  const answeredCount = routeQuestions.filter((q) => q.has_current_answer).length;

  return (
    <div className="hb-layout">
      {/* ── 侧边栏 ── */}
      <nav className="hb-sidebar" aria-label="学习问题目录">
        <div className="hb-sidebar-header">
          <strong>{normalizedQuery ? "搜索结果" : "按写书顺序"}</strong>
          <small>{answeredCount}/{routeQuestions.length} 项</small>
        </div>
        {stagesWithQuestions.map((stage) => (
          <div key={stage.stage_id} className="hb-sidebar-stage">
            <div className="hb-stage-label">
              <span className="hb-stage-num">{stage.stage_id}</span>
              <span className="hb-stage-name">{stage.stage_name}</span>
            </div>
            {stage.questions.map((q) => (
              <button
                key={q.question_id}
                type="button"
                className={`hb-sidebar-q ${q.question_id === activeQuestion?.question_id ? "hb-sq-active" : ""}`}
                onClick={() => onSelectQuestion(q.question_id)}
              >
                <span className={`hb-dot ${ANSWER_DOT_CLASS[q.answer_status] ?? "hb-dot-gray"}`} />
                <span className="hb-sq-id">{q.question_id}</span>
                <span className="hb-sq-title">{q.question}</span>
              </button>
            ))}
          </div>
        ))}
        {stagesWithQuestions.length === 0 && (
          <div className="hb-no-results">
            <p>未找到匹配的创作问题</p>
          </div>
        )}
      </nav>

      {/* ── 主内容区 ── */}
      <main className="hb-main">
        {activeQuestion ? (
          <QuestionContent
            question={activeQuestion}
            evidenceButtons={evidenceButtons}
          />
        ) : (
          <div className="hb-empty">
            <p>{normalizedQuery ? `未找到与 “${searchQuery}” 相关的问题。` : "选择左侧的问题开始阅读。"}</p>
          </div>
        )}
      </main>
    </div>
  );
}
