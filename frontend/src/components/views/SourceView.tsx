import React, { useState, useEffect, useMemo, useRef } from "react";
import type { EvidenceContext, SourceUnit, SourceUnitContent, SourceVersion } from "../../api";

interface SourceViewProps {
  chapters: SourceUnit[];
  selectedChapterId: string;
  onSelectChapter: (chapterId: string) => void;
  chapterContent: SourceUnitContent | null;
  activeVersion: SourceVersion | null;
  chapterDisplayNumbers: Map<string, number>;
  chapterIssueMap: Map<string, number>;
  // Chapter editing actions (optional)
  chapterTitleDraft: string;
  setChapterTitleDraft: (val: string) => void;
  chapterTypeDraft: string;
  setChapterTypeDraft: (val: string) => void;
  onSaveSourceUnit: () => Promise<void>;
  onMergeSourceUnit: (direction: "PREVIOUS" | "NEXT") => Promise<void>;
  splitTitleDraft: string;
  setSplitTitleDraft: (val: string) => void;
  splitTypeDraft: "VOLUME" | "CHAPTER";
  setSplitTypeDraft: (val: "VOLUME" | "CHAPTER") => void;
  splitOffset: number;
  setSplitOffset: (val: number) => void;
  splitLineOptions: Array<{ offset: number; line: number; preview: string }>;
  onSplitSourceUnit: () => Promise<void>;
  busy?: string;
  targetEvidence?: EvidenceContext["evidence"] | null;
  onClearTargetEvidence?: () => void;
}

function renderHighlightedText(text: string, snapshot: string) {
  if (!snapshot) return <mark className="evidence-highlight-mark">{text}</mark>;
  const cleanSnapshot = snapshot.trim();
  if (!cleanSnapshot) return <mark className="evidence-highlight-mark">{text}</mark>;
  const index = text.indexOf(cleanSnapshot);
  if (index !== -1) {
    const before = text.slice(0, index);
    const match = text.slice(index, index + cleanSnapshot.length);
    const after = text.slice(index + cleanSnapshot.length);
    return (
      <>
        {before}
        <mark className="evidence-highlight-mark">{match}</mark>
        {after}
      </>
    );
  }
  // Try substring prefix match (at least 8 chars)
  for (let len = Math.min(cleanSnapshot.length, 24); len >= 8; len -= 4) {
    const sub = cleanSnapshot.slice(0, len);
    const subIdx = text.indexOf(sub);
    if (subIdx !== -1) {
      const matchLen = Math.min(text.length - subIdx, cleanSnapshot.length);
      const before = text.slice(0, subIdx);
      const match = text.slice(subIdx, subIdx + matchLen);
      const after = text.slice(subIdx + matchLen);
      return (
        <>
          {before}
          <mark className="evidence-highlight-mark">{match}</mark>
          {after}
        </>
      );
    }
  }
  // Fallback: highlight the full paragraph
  return <mark className="evidence-highlight-mark">{text}</mark>;
}

export function SourceView({
  chapters,
  selectedChapterId,
  onSelectChapter,
  chapterContent,
  activeVersion,
  chapterDisplayNumbers,
  chapterIssueMap,
  chapterTitleDraft,
  setChapterTitleDraft,
  chapterTypeDraft,
  setChapterTypeDraft,
  onSaveSourceUnit,
  onMergeSourceUnit,
  splitTitleDraft,
  setSplitTitleDraft,
  splitTypeDraft,
  setSplitTypeDraft,
  splitOffset,
  setSplitOffset,
  splitLineOptions,
  onSplitSourceUnit,
  busy,
  targetEvidence,
  onClearTargetEvidence,
}: SourceViewProps) {
  const [showManagePanel, setShowManagePanel] = useState(false);
  const [searchChapter, setSearchChapter] = useState("");

  const filteredChapters = chapters.filter((c) =>
    searchChapter.trim() ? c.title.toLowerCase().includes(searchChapter.toLowerCase()) : true
  );

  const selectedIndex = chapters.findIndex((c) => c.id === selectedChapterId);

  // Virtual scrolling state for long chapter reading
  const paragraphs = useMemo(() => {
    return chapterContent ? chapterContent.content.split("\n") : [];
  }, [chapterContent]);

  const [readerScrollTop, setReaderScrollTop] = useState(0);
  const [readerHeight, setReaderHeight] = useState(800);
  const readerMainRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = readerMainRef.current;
    if (!el) return;
    let ticking = false;
    const handleScroll = () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          setReaderScrollTop(el.scrollTop);
          setReaderHeight(el.clientHeight || 800);
          ticking = false;
        });
        ticking = true;
      }
    };
    handleScroll();
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, [chapterContent]);

  const ESTIMATED_PARA_HEIGHT = 44;
  const OVERSCAN = 25;
  const isVirtual = paragraphs.length > 80;

  // Resolve the exact target paragraph index in paragraphs array
  const targetParagraphIndex = useMemo(() => {
    if (!targetEvidence || targetEvidence.source_unit_id !== selectedChapterId || !chapterContent) {
      return -1;
    }
    const cleanSnapshot = targetEvidence.text_snapshot?.trim();

    // Strategy 1: Content match against paragraphs
    if (cleanSnapshot) {
      // 1a: Exact or containment match
      const exactIdx = paragraphs.findIndex((p) => {
        const t = p.trim();
        return t && (t.includes(cleanSnapshot) || cleanSnapshot.includes(t));
      });
      if (exactIdx !== -1) return exactIdx;

      // 1b: Prefix match (first 15 chars)
      const prefix = cleanSnapshot.slice(0, 15);
      if (prefix) {
        const prefixIdx = paragraphs.findIndex((p) => p.includes(prefix));
        if (prefixIdx !== -1) return prefixIdx;
      }
    }

    // Strategy 2: Relative character offset match inside chapter content
    if (chapterContent.start_char != null && targetEvidence.start_char >= chapterContent.start_char) {
      const relStart = targetEvidence.start_char - chapterContent.start_char;
      let offset = 0;
      for (let i = 0; i < paragraphs.length; i++) {
        const lineLen = paragraphs[i].length + 1; // +1 for \n
        if (offset <= relStart && relStart < offset + lineLen) {
          return i;
        }
        offset += lineLen;
      }
    }

    // Strategy 3: Index offset heuristic (when chapter heading was stripped by source_unit_display_content)
    if (targetEvidence.paragraph_index > 0 && targetEvidence.paragraph_index - 1 < paragraphs.length) {
      return targetEvidence.paragraph_index - 1;
    }
    if (targetEvidence.paragraph_index >= 0 && targetEvidence.paragraph_index < paragraphs.length) {
      return targetEvidence.paragraph_index;
    }

    return -1;
  }, [targetEvidence, selectedChapterId, chapterContent, paragraphs]);

  const { visibleStart, visibleEnd, paddingTop, paddingBottom } = useMemo(() => {
    if (!isVirtual) {
      return { visibleStart: 0, visibleEnd: paragraphs.length, paddingTop: 0, paddingBottom: 0 };
    }
    const rawStart = Math.max(0, Math.floor(readerScrollTop / ESTIMATED_PARA_HEIGHT) - OVERSCAN);
    const rawEnd = Math.min(
      paragraphs.length,
      Math.ceil((readerScrollTop + readerHeight) / ESTIMATED_PARA_HEIGHT) + OVERSCAN
    );

    let start = rawStart;
    let end = rawEnd;

    // Guarantee target evidence paragraph is always mounted in virtual window
    if (targetEvidence && targetEvidence.source_unit_id === selectedChapterId) {
      const targetIdx = targetParagraphIndex >= 0 ? targetParagraphIndex : targetEvidence.paragraph_index;
      if (targetIdx >= 0) {
        if (targetIdx < start) {
          start = Math.max(0, targetIdx - 10);
        } else if (targetIdx >= end) {
          end = Math.min(paragraphs.length, targetIdx + 15);
        }
      }
    }

    const padTop = start * ESTIMATED_PARA_HEIGHT;
    const padBottom = (paragraphs.length - end) * ESTIMATED_PARA_HEIGHT;

    return { visibleStart: start, visibleEnd: end, paddingTop: padTop, paddingBottom: padBottom };
  }, [isVirtual, paragraphs.length, readerScrollTop, readerHeight, targetEvidence, selectedChapterId, targetParagraphIndex]);

  // Auto-scroll to target paragraph when targetEvidence is set
  useEffect(() => {
    if (!targetEvidence || targetEvidence.source_unit_id !== selectedChapterId) return;
    const targetIdx = targetParagraphIndex >= 0 ? targetParagraphIndex : targetEvidence.paragraph_index;
    const timer = window.setTimeout(() => {
      const el = document.getElementById(`source-para-${targetIdx}`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 120);
    return () => window.clearTimeout(timer);
  }, [targetEvidence, selectedChapterId, targetParagraphIndex]);

  return (
    <div className="source-view-container">
      {/* Chapter Sidebar */}
      <aside className="source-chapter-sidebar">
        <div className="source-chapter-header">
          <div className="source-chapter-title">
            <h3>章节目录</h3>
            <span className="source-chapter-count">{chapters.length} 单元</span>
          </div>
          <input
            type="search"
            placeholder="搜索章节名…"
            className="source-search-input"
            value={searchChapter}
            onChange={(e) => setSearchChapter(e.target.value)}
          />
        </div>

        <div className="source-chapter-list">
          {filteredChapters.map((chapter) => {
            const isSelected = chapter.id === selectedChapterId;
            const issueCount = chapterIssueMap.get(chapter.id) ?? 0;
            const num =
              chapter.unit_type === "TITLE"
                ? "作品"
                : chapter.unit_type === "PREFACE"
                ? "前置"
                : chapter.unit_type === "VOLUME"
                ? "分卷"
                : chapterDisplayNumbers.get(chapter.id) ?? chapter.ordinal;

            return (
              <button
                key={chapter.id}
                type="button"
                className={`source-chapter-item ${isSelected ? "active" : ""}`}
                onClick={() => onSelectChapter(chapter.id)}
              >
                <span className="chapter-ordinal-badge">{num}</span>
                <span className="chapter-item-title" title={chapter.title}>
                  {chapter.title}
                </span>
                <span className="chapter-item-chars">
                  {(chapter.char_count / 1000).toFixed(1)}k
                </span>
                {issueCount > 0 && <span className="chapter-issue-badge">{issueCount}</span>}
              </button>
            );
          })}
          {!filteredChapters.length && <p className="source-empty-hint">未找到匹配章节</p>}
        </div>
      </aside>

      {/* Main Chapter Reader */}
      <main className="source-reader-main" ref={readerMainRef}>
        {chapterContent ? (
          <article className="source-reader-article">
            <header className="source-reader-header">
              <div className="source-reader-heading">
                <h2>{chapterContent.title}</h2>
                <div className="source-reader-meta">
                  <span>总计 {chapterContent.content.length.toLocaleString()} 字符</span>
                  {activeVersion?.status !== "CONFIRMED" && (
                    <span className="source-unconfirmed-tag">未确认边界</span>
                  )}
                </div>
              </div>

              {activeVersion?.status !== "CONFIRMED" && (
                <button
                  type="button"
                  className="secondary-button small-btn"
                  onClick={() => setShowManagePanel((prev) => !prev)}
                >
                  {showManagePanel ? "收起管理面板" : "⚙️ 拆分/合并/重命名"}
                </button>
              )}
            </header>

            {/* Collapsible Chapter Correction Panel */}
            {showManagePanel && activeVersion?.status !== "CONFIRMED" && (
              <div className="chapter-correction-card">
                <h4>卷章边界纠偏</h4>
                <div className="correction-grid">
                  <div className="correction-field">
                    <label>单元标题</label>
                    <input
                      value={chapterTitleDraft}
                      maxLength={500}
                      onChange={(e) => setChapterTitleDraft(e.target.value)}
                    />
                  </div>
                  <div className="correction-field">
                    <label>单元类型</label>
                    <select
                      value={chapterTypeDraft}
                      onChange={(e) => setChapterTypeDraft(e.target.value)}
                    >
                      <option value="CHAPTER">正文章节</option>
                      <option value="VOLUME">分卷卷首</option>
                      <option value="PREFACE">正文前内容</option>
                      <option value="TITLE">作品信息</option>
                    </select>
                  </div>
                  <button
                    type="button"
                    className="primary-button small-btn"
                    disabled={!chapterTitleDraft.trim() || Boolean(busy)}
                    onClick={() => void onSaveSourceUnit()}
                  >
                    {busy === "save-source-unit" ? "保存中…" : "保存修改"}
                  </button>
                </div>

                <div className="correction-actions-row">
                  <button
                    type="button"
                    className="secondary-button small-btn"
                    disabled={selectedIndex <= 0 || Boolean(busy)}
                    onClick={() => void onMergeSourceUnit("PREVIOUS")}
                  >
                    并入上一单元
                  </button>
                  <button
                    type="button"
                    className="secondary-button small-btn"
                    disabled={selectedIndex < 0 || selectedIndex >= chapters.length - 1 || Boolean(busy)}
                    onClick={() => void onMergeSourceUnit("NEXT")}
                  >
                    与下一单元合并
                  </button>
                  <small className="correction-hint">合并只消除错误分割线，不丢失任何原文正文。</small>
                </div>

                <div className="split-action-row">
                  <div className="correction-field">
                    <label>在此单元中拆出新章节</label>
                    <input
                      placeholder="新章节标题"
                      value={splitTitleDraft}
                      onChange={(e) => setSplitTitleDraft(e.target.value)}
                    />
                  </div>
                  <div className="correction-field">
                    <label>从哪一行起切分</label>
                    <select
                      value={splitOffset || ""}
                      onChange={(e) => setSplitOffset(Number(e.target.value))}
                    >
                      <option value="">选择行作为新章起点</option>
                      {splitLineOptions.map((opt) => (
                        <option key={opt.offset} value={opt.offset}>
                          第 {opt.line} 行：{opt.preview}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="button"
                    className="secondary-button small-btn"
                    disabled={!splitOffset || !splitTitleDraft.trim() || Boolean(busy)}
                    onClick={() => void onSplitSourceUnit()}
                  >
                    {busy === "split-source-unit" ? "切分中…" : "执行拆分"}
                  </button>
                </div>
              </div>
            )}

            {/* Target Evidence Focus Banner */}
            {targetEvidence && targetEvidence.source_unit_id === selectedChapterId && (
              <div className="evidence-focus-banner" role="status">
                <div className="evidence-focus-info">
                  <span className="focus-badge">📍 正在查看原文证据</span>
                  <span>
                    第 <strong>{targetParagraphIndex >= 0 ? targetParagraphIndex + 1 : targetEvidence.paragraph_index + 1}</strong> 段（字符偏移量：{targetEvidence.start_char} ~ {targetEvidence.end_char}）
                  </span>
                </div>
                {onClearTargetEvidence && (
                  <button
                    type="button"
                    className="evidence-clear-btn"
                    onClick={onClearTargetEvidence}
                    title="取消高亮定位"
                  >
                    ✕ 取消高亮
                  </button>
                )}
              </div>
            )}

            {/* Prose Content (Virtual Windowed) */}
            <div
              className="source-reader-prose"
              style={
                isVirtual
                  ? { paddingTop: `${paddingTop}px`, paddingBottom: `${paddingBottom}px` }
                  : undefined
              }
            >
              {paragraphs.slice(visibleStart, visibleEnd).map((para, localIdx) => {
                const idx = visibleStart + localIdx;
                const trimmed = para.trim();
                if (!trimmed) return <div key={idx} className="prose-empty-line" />;
                const isTargetParagraph =
                  Boolean(targetEvidence) &&
                  targetEvidence?.source_unit_id === selectedChapterId &&
                  (targetParagraphIndex >= 0 ? idx === targetParagraphIndex : idx === targetEvidence?.paragraph_index);

                return (
                  <p
                    key={idx}
                    id={`source-para-${idx}`}
                    className={`prose-paragraph ${isTargetParagraph ? "highlighted-paragraph" : ""}`}
                  >
                    {isTargetParagraph && targetEvidence?.text_snapshot
                      ? renderHighlightedText(trimmed, targetEvidence.text_snapshot)
                      : trimmed}
                  </p>
                );
              })}
            </div>
          </article>
        ) : (
          <div className="source-empty-reader">
            <span className="empty-icon">📑</span>
            <h3>请选择左侧章节开始阅读</h3>
            <p>点击章节目录可快速加载原文正文。</p>
          </div>
        )}
      </main>
    </div>
  );
}
