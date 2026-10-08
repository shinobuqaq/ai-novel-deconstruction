import React from "react";
import type { EvidenceContext } from "../../api";

interface EvidenceDrawerProps {
  context: EvidenceContext | null;
  onClose: () => void;
  onNavigateToChapter?: (chapterId: string) => void;
}

export function EvidenceDrawer({
  context,
  onClose,
  onNavigateToChapter,
}: EvidenceDrawerProps) {
  if (!context) return null;

  const { evidence, chapter_title, context_text } = context;

  return (
    <aside className="evidence-drawer-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="evidence-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="evidence-drawer-header">
          <div className="evidence-drawer-title">
            <span className="evidence-badge">原文证据</span>
            <h3>{chapter_title || "对应章节"}</h3>
          </div>
          <button
            type="button"
            className="evidence-drawer-close"
            onClick={onClose}
            aria-label="关闭证据面板"
          >
            ✕
          </button>
        </header>

        <div className="evidence-drawer-body">
          <section className="evidence-section">
            <h4>关键依据原文</h4>
            <div className="evidence-quote-box">
              <blockquote>{evidence.text_snapshot}</blockquote>
            </div>
          </section>

          {context_text && (
            <section className="evidence-section">
              <h4>上下文环境</h4>
              <div className="evidence-context-box">
                <p>{context_text}</p>
              </div>
            </section>
          )}

          <section className="evidence-meta">
            <div className="evidence-meta-item">
              <span>证据编号</span>
              <code>{evidence.id}</code>
            </div>
            <div className="evidence-meta-item">
              <span>字符位置</span>
              <span>
                {evidence.start_char} - {evidence.end_char} (第 {evidence.paragraph_index + 1} 段)
              </span>
            </div>
          </section>
        </div>

        {onNavigateToChapter && evidence.source_unit_id && (
          <footer className="evidence-drawer-footer">
            <button
              type="button"
              className="primary-button full-width"
              onClick={() => {
                onNavigateToChapter(evidence.source_unit_id);
                onClose();
              }}
            >
              在原文阅读器中查看此章
            </button>
          </footer>
        )}
      </div>
    </aside>
  );
}
