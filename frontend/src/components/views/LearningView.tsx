import React, { useState, useMemo } from "react";
import type {
  Workbench,
  WorkbenchLearningQuestion,
} from "../../api";
import LearningHandbook from "../../LearningHandbook";

interface LearningViewProps {
  workbench: Workbench;
  onOpenEvidence: (evidenceId: string) => void;
  onStartLearningReport?: () => void;
  busy?: string;
}

export function LearningView({
  workbench,
  onOpenEvidence,
  onStartLearningReport,
  busy,
}: LearningViewProps) {
  const [activeQuestionId, setActiveQuestionId] = useState<string>("1.4");
  const [searchQuery, setSearchQuery] = useState("");

  const learningReport = workbench.learning_report;
  const questions = learningReport?.questions || [];

  const answeredCount = questions.filter(
    (q) => q.answer_status === "ANSWERED" || q.has_current_answer
  ).length;

  const filteredQuestions = useMemo(() => {
    if (!searchQuery.trim()) return questions;
    const q = searchQuery.toLowerCase();
    return questions.filter(
      (item) =>
        item.question.toLowerCase().includes(q) ||
        item.stage_name.toLowerCase().includes(q) ||
        item.question_id.includes(q)
    );
  }, [questions, searchQuery]);

  // Ensure activeQuestionId is valid
  const currentActiveId =
    questions.some((q) => q.question_id === activeQuestionId)
      ? activeQuestionId
      : questions[0]?.question_id || "1.4";

  return (
    <div className="learning-view-container">
      {/* Top Banner & Actions */}
      <header className="view-header-strip">
        <div className="view-header-main">
          <h2>创作学习手册</h2>
          <p className="view-header-desc">
            从本书的实际叙事结构中提炼出的 42 项小说创作核心法则与实操研报。
          </p>
        </div>
        <div className="view-header-search">
          <input
            type="search"
            placeholder="搜索 42 问、阶段或关键词…"
            className="learning-search-input"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="搜索创作问题"
          />
        </div>
        <div className="view-header-actions">
          <div className="learning-stat-pill">
            <span>已生成答案</span>
            <strong>{answeredCount} / {questions.length}</strong>
          </div>
          {onStartLearningReport && (
            <button
              type="button"
              className="primary-button"
              disabled={Boolean(busy)}
              onClick={onStartLearningReport}
            >
              {busy === "learning-report" ? "正在提炼研报…" : "重新提炼逐问答案"}
            </button>
          )}
        </div>
      </header>

      {/* Main Two-Column Handbook Reader */}
      <div className="learning-view-body">
        {questions.length > 0 ? (
          <LearningHandbook
            learningReport={learningReport}
            activeLearningQuestionId={currentActiveId}
            onSelectQuestion={setActiveQuestionId}
            searchQuery={searchQuery}
            evidenceButtons={(ids, label) => (
              <div className="evidence-btn-group">
                {ids.map((id) => (
                  <button
                    key={id}
                    type="button"
                    className="evidence-chip-btn"
                    onClick={() => onOpenEvidence(id)}
                    title="在右侧抽屉中查看原文支持证据"
                  >
                    🔍 原文证据
                  </button>
                ))}
              </div>
            )}
          />
        ) : (
          <div className="view-empty-state">
            <span className="empty-icon">📖</span>
            <h3>尚未生成创作学习问答</h3>
            <p>请在“分析控制台”运行小说分析，系统将自动基于本书生成 42 问创作学习手册。</p>
          </div>
        )}
      </div>
    </div>
  );
}
