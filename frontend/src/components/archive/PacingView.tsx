import React from "react";
import type { Workbench } from "../../api";
import {
  ACTION_DIALOGUE_LABELS,
  CHAPTER_END_HOOK_STRENGTH_LABELS,
  CHAPTER_END_HOOK_TYPE_LABELS,
  CHARACTER_DESIGN_STATUS_LABELS,
} from "./constants";

interface PacingViewProps {
  viewData: Workbench;
  isHistoricalRevision: boolean;
  busy: string;
  focusTarget: { view: string; id: string } | null;
  onOpenEvidence: (evidenceId: string) => void;
  onMarkProblem: (kind: string, id: string | null, label: string) => void;
  onStartChapterEndHooks: (force: boolean) => void;
}

export function PacingView({
  viewData,
  isHistoricalRevision,
  busy,
  focusTarget,
  onOpenEvidence,
  onMarkProblem,
  onStartChapterEndHooks,
}: PacingViewProps) {
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

  const chapterEndHooks = viewData.chapter_end_hooks_evidence;
  const chapterEndHooksReadiness = viewData.learning_report.readiness.checks.find(
    (item) => item.question_id === "4.9",
  );

  return (
    <div className="deep-analysis-view">
      {!viewData.deep_analysis ? (
        <div className="workbench-callout">
          <strong>节奏分析仍在整理</strong>
          <span>系统会按章节说明场景作用、信息释放和节奏变化。</span>
        </div>
      ) : (
        <section className="insight-group">
          <header>
            <div>
              <span>章节如何发挥作用</span>
              <h3>场景与节奏</h3>
            </div>
            <b>{viewData.deep_analysis.scene_analysis.length}</b>
          </header>
          <div className="scene-analysis-list">
            {viewData.deep_analysis.scene_analysis.map((scene) => (
              <article key={scene.id}>
                <div>
                  <span>第 {scene.chapter_ordinal} 章</span>
                  <b>
                    {scene.function === "SETUP"
                      ? "铺垫"
                      : scene.function === "TRANSITION"
                      ? "过渡"
                      : scene.function === "REVELATION"
                      ? "揭示"
                      : scene.function === "CONFLICT"
                      ? "冲突"
                      : scene.function === "DECISION"
                      ? "决定"
                      : scene.function === "AFTERMATH"
                      ? "余波"
                      : "其他功能"}
                  </b>
                </div>
                <h4>{scene.summary}</h4>
                <p>
                  节奏：
                  {scene.pace === "SLOW"
                    ? "较慢"
                    : scene.pace === "STEADY"
                    ? "平稳"
                    : scene.pace === "FAST"
                    ? "较快"
                    : scene.pace === "ACCELERATING"
                    ? "正在加速"
                    : scene.pace === "BRAKING"
                    ? "明显放缓"
                    : "尚不确定"}
                </p>
                {scene.information_released.length > 0 && (
                  <small>释放信息：{scene.information_released.join("；")}</small>
                )}
                {scene.action_dialogue_balance && (
                  <small>
                    动作与对话：
                    {ACTION_DIALOGUE_LABELS[scene.action_dialogue_balance] ?? "暂时无法判断"}
                  </small>
                )}
                {renderEvidenceButtons(scene.evidence_ids)}
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    onMarkProblem("SCENE", scene.id, `第 ${scene.chapter_ordinal} 章：${scene.summary}`)
                  }
                >
                  标记问题
                </button>
              </article>
            ))}
            {!viewData.deep_analysis.scene_analysis.length && (
              <p className="result-empty">当前没有完成场景与节奏分析。</p>
            )}
          </div>
        </section>
      )}

      <section className="evidence-ledger-section chapter-end-hooks-ledger">
        <header>
          <div>
            <span>北极星 4.9 · 专项证据</span>
            <h3>全书章末钩类型与节律账本</h3>
          </div>
          <p>
            模型按连续章节窗口逐章分类，程序在全书窗口完成后精确计算类型比例、相邻轮换与连续记录；这里不混入
            3.4 的前三章兑现或 4.10 的悬念回收。
          </p>
        </header>
        <div className={`character-design-state ${viewData.chapter_end_hooks_status.toLowerCase()}`}>
          <div>
            <strong>
              {CHARACTER_DESIGN_STATUS_LABELS[viewData.chapter_end_hooks_status] ?? "状态未知"}
            </strong>
            <span>
              {viewData.chapter_end_hooks_status === "GENERATING"
                ? "后台正在按连续窗口覆盖全书，全部窗口完成后才形成精确全书账本。"
                : chapterEndHooksReadiness?.gaps.join("；") ||
                  "全书连续覆盖与顺序指标已经通过程序校验。"}
            </span>
          </div>
          {!isHistoricalRevision &&
            viewData.deep_status === "READY" &&
            viewData.chapter_end_hooks_status !== "GENERATING" && (
              <button
                type="button"
                disabled={busy === "start-chapter-end-hooks"}
                onClick={() =>
                  onStartChapterEndHooks(viewData.chapter_end_hooks_status === "READY")
                }
              >
                {busy === "start-chapter-end-hooks"
                  ? "正在准备"
                  : viewData.chapter_end_hooks_status === "READY"
                  ? "重新分析 4.9"
                  : "生成 4.9 全书账本"}
              </button>
            )}
        </div>
        {chapterEndHooks && (
          <>
            {!chapterEndHooks.is_current && (
              <div className="character-design-warning">
                当前显示的是旧版结果，只供回看；最新正文、拆解合同或深层分析已经变化。
              </div>
            )}
            <div className="character-design-coverage">
              <div>
                <strong>
                  {chapterEndHooks.coverage.sampled_chapter_count}/
                  {chapterEndHooks.coverage.required_sample_count}
                </strong>
                <span>真实章末已覆盖</span>
              </div>
              <div>
                <strong>{chapterEndHooks.coverage.window_count ?? 1}</strong>
                <span>连续分析窗口</span>
              </div>
              <div>
                <strong>{chapterEndHooks.summary.max_consecutive_strong} 章</strong>
                <span>最长连续强钩</span>
              </div>
              <div>
                <strong>{chapterEndHooks.summary.no_hook_count} 章</strong>
                <span>无钩章节</span>
              </div>
            </div>
            <div className="hook-type-summary">
              {chapterEndHooks.summary.type_distribution
                .filter((item) => item.count > 0)
                .map((item) => (
                  <div key={`pacing-${item.hook_type}`}>
                    <strong>{item.count}</strong>
                    <span>{CHAPTER_END_HOOK_TYPE_LABELS[item.hook_type]}</span>
                    <small>{Math.round(item.ratio * 100)}%</small>
                  </div>
                ))}
            </div>
            <details className="ledger-detail chapter-hook-details">
              <summary>查看 {chapterEndHooks.chapters.length} 章逐章分类与原文依据</summary>
              <div className="chapter-hook-list">
                {chapterEndHooks.chapters.map((item) => (
                  <article
                    key={`pacing-hook-${item.chapter_ordinal}`}
                    className={item.hook_type === "NONE" ? "none" : item.strength.toLowerCase()}
                  >
                    <header>
                      <div>
                        <strong>
                          第 {item.chapter_ordinal} 章 · {item.chapter_title}
                        </strong>
                        <span>{item.phase_title || "未归入剧情阶段"}</span>
                      </div>
                      <div>
                        <b>{CHAPTER_END_HOOK_TYPE_LABELS[item.hook_type]}</b>
                        <i>{CHAPTER_END_HOOK_STRENGTH_LABELS[item.strength]}</i>
                      </div>
                    </header>
                    <h4>{item.hook_question || "本章没有形成具体未闭合问题"}</h4>
                    <p>{item.rationale}</p>
                    {item.retention_basis && <small>章末依据：{item.retention_basis}</small>}
                    <div className="chapter-hook-evidence-actions">
                      {renderEvidenceButtons(item.ending_evidence_ids, "查看真实章末")}
                    </div>
                  </article>
                ))}
              </div>
            </details>
          </>
        )}
      </section>
    </div>
  );
}
