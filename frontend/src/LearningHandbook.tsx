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
  const [toolkitOpen, setToolkitOpen] = useState(false);
  const [refsOpen, setRefsOpen] = useState(false);

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
      {/* ── Header ── */}
      <header className="hb-question-header">
        <div className="hb-question-meta">
          <span className="hb-qid">{question.question_id}</span>
          <span className="hb-stage-name">{question.stage_name}</span>
          <span className={`hb-badge ${BADGE_CLASS[question.answer_status] ?? "hb-badge-gray"}`}>
            {ANSWER_STATUS_LABEL[question.answer_status] ?? question.answer_status}
          </span>
        </div>
        <h2 className="hb-question-title">{question.question}</h2>
      </header>

      {/* ── 为什么重要 ── */}
      {hb?.why_important && (
        <section className="hb-section hb-why">
          <div className="hb-section-label">为什么重要</div>
          <p>{hb.why_important}</p>
        </section>
      )}

      {/* ── 本书怎么做 ── */}
      <section className="hb-section hb-answer">
        <div className="hb-section-label">本书怎么做</div>
        {hasAnswer ? (
          <>
            <p className="hb-conclusion">{question.conclusion}</p>
            {question.metrics.length > 0 && (
              <div className="hb-metrics">
                {question.metrics.map((m, i) => (
                  <div key={i} className="hb-metric-card" title={m.method}>
                    <strong>{m.value}{m.unit ? ` ${m.unit}` : ""}</strong>
                    <span>{m.label}</span>
                  </div>
                ))}
              </div>
            )}
            {question.reusable_lessons.length > 0 && (
              <div className="hb-lessons">
                <div className="hb-sublabel">可参考做法</div>
                <ul>{question.reusable_lessons.map((l, i) => <li key={i}>{l}</li>)}</ul>
              </div>
            )}
            {question.do_not_copy.length > 0 && (
              <div className="hb-do-not-copy">
                <div className="hb-sublabel">不可照搬</div>
                <ul>{question.do_not_copy.map((d, i) => <li key={i}>{d}</li>)}</ul>
              </div>
            )}
          </>
        ) : (
          <p className="hb-no-answer">这问的学习答案尚未生成。</p>
        )}
      </section>

      {/* ── 通用方法 ── */}
      {(hb?.universal_methods?.length ?? 0) > 0 && (
        <section className="hb-section hb-methods">
          <div className="hb-section-label">通用方法</div>
          <ul>{hb!.universal_methods.map((m, i) => <li key={i}>{m}</li>)}</ul>
        </section>
      )}

      {/* ── 实操工具包（默认折叠）── */}
      {hasToolkit && (
        <section className="hb-section hb-toolkit">
          <button type="button" className="hb-toggle-btn"
            onClick={() => setToolkitOpen(v => !v)} aria-expanded={toolkitOpen}>
            <span className="hb-section-label">实操工具包</span>
            <span className="hb-toggle-hint">
              {[hb!.checklist?.length ? `${hb!.checklist.length} 条自检` : null,
                hb!.common_errors?.length ? `${hb!.common_errors.length} 个错误示范` : null,
                hb!.templates?.length ? `${hb!.templates.length} 个模板` : null,
              ].filter(Boolean).join(" · ")}
              <span className="hb-chevron">{toolkitOpen ? " ▲" : " ▼"}</span>
            </span>
          </button>
          {toolkitOpen && (
            <div className="hb-toolkit-body">
              {(hb!.checklist?.length ?? 0) > 0 && (
                <div className="hb-checklist">
                  <div className="hb-sublabel">自检清单</div>
                  <ul>{hb!.checklist.map((item, i) => (
                    <li key={i}><label><input type="checkbox" /><span>{item}</span></label></li>
                  ))}</ul>
                </div>
              )}
              {(hb!.common_errors?.length ?? 0) > 0 && (
                <div className="hb-errors">
                  <div className="hb-sublabel">常见错误</div>
                  {hb!.common_errors.map((err, i) => (
                    <div key={i} className="hb-error-item">
                      <span className="hb-mistake">❌ {err.mistake}</span>
                      <span className="hb-fix">✅ {err.fix}</span>
                    </div>
                  ))}
                </div>
              )}
              {(hb!.templates?.length ?? 0) > 0 && (
                <div className="hb-templates">
                  <div className="hb-sublabel">可套模板</div>
                  {hb!.templates.map((tmpl, i) => (
                    <div key={i} className="hb-template-item">
                      <strong>{tmpl.name}</strong>
                      <pre>{tmpl.structure}</pre>
                      {(tmpl.checkpoints?.length ?? 0) > 0 && (
                        <ul>{tmpl.checkpoints.map((cp, j) => <li key={j}>{cp}</li>)}</ul>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* ── 题材差异 ── */}
      <section className="hb-section hb-genre">
        <div className="hb-section-label">题材差异</div>
        {hb?.genre_note
          ? <p>{hb.genre_note}</p>
          : <p className="hb-placeholder">待补充（当前样本为龙族1+2，网文爽文题材差异数据尚未收集）</p>
        }
      </section>

      {/* ── 参考依据（默认折叠）── */}
      {(question.contract_items.length > 0 || question.limitations.length > 0) && (
        <section className="hb-section hb-refs">
          <button type="button" className="hb-toggle-btn"
            onClick={() => setRefsOpen(v => !v)} aria-expanded={refsOpen}>
            <span className="hb-section-label">参考依据</span>
            <span className="hb-toggle-hint">
              {supportedCount}/{question.contract_items.length} 项已有原文依据
              <span className="hb-chevron">{refsOpen ? " ▲" : " ▼"}</span>
            </span>
          </button>
          {refsOpen && (
            <div className="hb-refs-body">
              {question.limitations.length > 0 && (
                <div className="hb-limitations">
                  <div className="hb-sublabel">当前限制</div>
                  <ul>{question.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
                </div>
              )}
              {question.contract_items.map((item) => (
                <div key={item.item_id}
                  className={`hb-contract-item ${item.status === "SUPPORTED" ? "hb-ci-ok" : "hb-ci-gap"}`}>
                  <div className="hb-ci-header">
                    <strong>{item.label}</strong>
                    <span>{item.status === "SUPPORTED" ? "已有依据" : "仍缺数据"}</span>
                  </div>
                  <p>{item.finding}</p>
                  {item.limitations.length > 0 && <small>限制：{item.limitations.join("；")}</small>}
                  {evidenceButtons(item.evidence_ids.slice(0, 3), "查看原文")}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ── 关键原文快速入口 ── */}
      {(question.evidence_ids.length > 0 || question.counter_evidence_ids.length > 0) && (
        <div className="hb-evidence-row">
          {evidenceButtons(question.evidence_ids.slice(0, 4), "查看关键原文")}
          {question.counter_evidence_ids.length > 0 &&
            evidenceButtons(question.counter_evidence_ids.slice(0, 2), "查看反证")}
        </div>
      )}
    </article>
  );
}

// ── LearningHandbook (主组件) ───────────────────────────────────────────────

export default function LearningHandbook({
  learningReport,
  activeLearningQuestionId,
  onSelectQuestion,
  evidenceButtons,
}: LearningHandbookProps) {
  // 侧边栏只展示已在系统里跟踪的题目（有答案、或材料就绪、或已过期需更新）
  const routeQuestions = learningReport.questions.filter(
    (q) =>
      q.has_current_answer ||
      q.answer_status !== "NOT_GENERATED" ||
      q.material_status === "READY" ||
      q.material_status === "OUTDATED",
  );

  const activeQuestion =
    learningReport.questions.find((q) => q.question_id === activeLearningQuestionId) ??
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
          <strong>按写书顺序</strong>
          <small>{answeredCount}/{routeQuestions.length} 有效答案</small>
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
                className={`hb-sidebar-q ${q.question_id === activeLearningQuestionId ? "hb-sq-active" : ""}`}
                onClick={() => onSelectQuestion(q.question_id)}
              >
                <span className={`hb-dot ${ANSWER_DOT_CLASS[q.answer_status] ?? "hb-dot-gray"}`} />
                <span className="hb-sq-id">{q.question_id}</span>
                <span className="hb-sq-title">
                  {q.question.length > 22 ? q.question.slice(0, 22) + "…" : q.question}
                </span>
              </button>
            ))}
          </div>
        ))}
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
            <p>选择左侧的问题开始阅读。</p>
          </div>
        )}
      </main>
    </div>
  );
}
