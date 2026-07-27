import { useMemo, useState } from "react";
import {
  buildStageZeroDocument,
  buildStageZeroPrompt,
  COCREATION_STAGES,
  COCREATION_STORAGE_KEY,
  parseStageZeroResult,
  SavedStageZero,
  STAGE_ZERO_EXAMPLES,
} from "./cocreation";

function readSavedStageZero(): SavedStageZero | null {
  try {
    const raw = window.localStorage.getItem(COCREATION_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as SavedStageZero;
    return value.version === 1 ? value : null;
  } catch {
    return null;
  }
}

function downloadMarkdown(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function displayTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间无法读取";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export default function NewBookCocreationPage() {
  const initialSaved = useMemo(readSavedStageZero, []);
  const [saved, setSaved] = useState<SavedStageZero | null>(initialSaved);
  const [userIdea, setUserIdea] = useState(initialSaved?.userIdea ?? "");
  const [resultBlock, setResultBlock] = useState(initialSaved?.resultBlock ?? "");
  const [copyNotice, setCopyNotice] = useState("");
  const [saveNotice, setSaveNotice] = useState("");
  const prompt = useMemo(() => buildStageZeroPrompt(userIdea), [userIdea]);
  const parsed = useMemo(() => parseStageZeroResult(resultBlock), [resultBlock]);

  async function copyText(value: string, successMessage: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopyNotice(successMessage);
    } catch {
      setCopyNotice("浏览器没有允许自动复制，请展开下方内容后手动复制。");
    }
  }

  function confirmStage() {
    if (!parsed.canConfirm) return;
    const confirmedAt = new Date().toISOString();
    const nextSaved: SavedStageZero = {
      version: 1,
      userIdea: userIdea.trim(),
      resultBlock: resultBlock.trim(),
      confirmedAt,
      parsed,
      documentMarkdown: buildStageZeroDocument(userIdea.trim(), parsed, confirmedAt),
    };
    window.localStorage.setItem(COCREATION_STORAGE_KEY, JSON.stringify(nextSaved));
    setSaved(nextSaved);
    setSaveNotice("第 0 步已经确认并保存。下一步将讨论“品类、承诺与差异化”。");
  }

  const hasInput = Boolean(resultBlock.trim());
  const statusLabel = parsed.status === "RESULT"
    ? "网页 AI 判断：已形成结果"
    : parsed.status === "NO_RESULT"
      ? "网页 AI 判断：本阶段无正式结果"
      : parsed.status === "INCOMPLETE"
        ? "网页 AI 判断：阶段尚未结束"
        : "等待固定结束块";

  return (
    <div className="cocreation-shell">
      <header className="cocreation-topbar">
        <div>
          <p className="product-kicker">新书共创</p>
          <h1>从一个想法，逐步形成自己的开书包</h1>
        </div>
        <div className="topbar-actions">
          <a className="button-link secondary-button" href="/">← 返回拆书工作台</a>
        </div>
      </header>

      <div className="cocreation-layout">
        <aside className="cocreation-steps" aria-label="新书共创步骤">
          <div className="cocreation-steps-heading">
            <strong>开书顺序</strong>
            <span>先定方向，再逐步收紧</span>
          </div>
          <ol>
            {COCREATION_STAGES.map((stage) => {
              const completed = stage.id === 0 && Boolean(saved);
              const current = stage.id === 0 && !saved;
              const next = stage.id === 1 && Boolean(saved);
              return (
                <li
                  key={stage.id}
                  className={completed ? "completed" : current ? "current" : next ? "next" : "locked"}
                >
                  <span>{completed ? "✓" : stage.id}</span>
                  <div>
                    <b>{stage.name}</b>
                    <small>{completed ? "已保存" : current ? "正在进行" : next ? "下一步" : "后续步骤"}</small>
                  </div>
                </li>
              );
            })}
          </ol>
          <div className="cocreation-boundary-note">
            <strong>这不是拆书结果</strong>
            <p>这里记录的是你与 AI 共同确认的新书决定。参考书内容不会自动写进来。</p>
          </div>
        </aside>

        <main className="cocreation-main">
          <section className="cocreation-stage-intro">
            <div>
              <p>第 0 步 · 先建立创作起点</p>
              <h2>灵感与创作约束</h2>
              <span>先弄清想给读者什么体验、目前偏向什么方向，以及哪些问题还没决定。现在不写完整人物、世界观或大纲。</span>
            </div>
            <div className="stage-destination">
              <span>本阶段写入</span>
              <strong>00_开书总表.md</strong>
              <small>不会调用项目内 AI 接口</small>
            </div>
          </section>

          {saved && (
            <section className="saved-stage-banner" aria-label="已保存结果">
              <div>
                <strong>第 0 步已有确认版本</strong>
                <span>保存于 {displayTime(saved.confirmedAt)}。你仍可以修改下方内容并重新确认，新版本会替换浏览器中的当前版本。</span>
              </div>
              <button
                type="button"
                className="secondary-button"
                onClick={() => downloadMarkdown("00_开书总表.md", saved.documentMarkdown)}
              >
                下载当前开书总表
              </button>
            </section>
          )}

          <section className="cocreation-card">
            <header>
              <span>1</span>
              <div>
                <h3>先写下你现在的想法</h3>
                <p>可以很明确、很模糊，也可以直接说“我没有灵感”。不用先填表。</p>
              </div>
            </header>
            <div className="idea-examples" aria-label="起点示例">
              {STAGE_ZERO_EXAMPLES.map((example) => (
                <button
                  type="button"
                  className="secondary-button"
                  key={example.label}
                  onClick={() => {
                    setUserIdea(example.text);
                    setSaveNotice("");
                  }}
                >
                  {example.label}
                </button>
              ))}
            </div>
            <label className="cocreation-field" htmlFor="cocreation-idea">
              <span>我的当前想法</span>
              <textarea
                id="cocreation-idea"
                value={userIdea}
                onChange={(event) => {
                  setUserIdea(event.target.value);
                  setSaveNotice("");
                }}
                placeholder="例如：我想写一本修仙小说，希望主角一步步成长到世界之巅……"
                maxLength={6000}
              />
              <small>{userIdea.length} / 6000 字符</small>
            </label>
          </section>

          <section className="cocreation-card">
            <header>
              <span>2</span>
              <div>
                <h3>复制提示词，去你常用的网页 AI 讨论</h3>
                <p>提示词会要求 AI 主动追问；你没想好时给至少三个真正不同的方案；只有你确认后才能结束阶段。</p>
              </div>
            </header>
            <div className="portable-prompt-actions">
              <button type="button" onClick={() => void copyText(prompt, "完整提示词已复制，可以直接粘贴到网页 AI。")}>
                复制完整提示词
              </button>
              <span aria-live="polite">{copyNotice || "不限制使用哪一家网页 AI。"}</span>
            </div>
            <details className="prompt-preview">
              <summary>查看将要复制的完整内容</summary>
              <textarea aria-label="完整提示词预览" readOnly value={prompt} />
            </details>
          </section>

          <section className="cocreation-card">
            <header>
              <span>3</span>
              <div>
                <h3>只粘贴最后的“阶段结束块”</h3>
                <p>不需要搬运完整聊天记录。页面会先检查阶段、字段、状态和写入范围，不会直接覆盖正式内容。</p>
              </div>
            </header>
            <label className="cocreation-field" htmlFor="stage-result">
              <span>网页 AI 给出的阶段结论</span>
              <textarea
                id="stage-result"
                className="stage-result-input"
                value={resultBlock}
                onChange={(event) => {
                  setResultBlock(event.target.value);
                  setSaveNotice("");
                }}
                placeholder="从【阶段结束：已形成结果】、【阶段结束：无正式结果】或【阶段尚未结束】开始粘贴……"
                maxLength={30000}
              />
              <small>{resultBlock.length} / 30000 字符</small>
            </label>

            <div className={`stage-result-status ${parsed.status.toLowerCase()}`}>
              <strong>{statusLabel}</strong>
              {!hasInput && <span>粘贴后才会开始检查。</span>}
              {hasInput && parsed.status === "UNKNOWN" && <span>当前内容不能识别为规定的结束格式。</span>}
              {hasInput && parsed.status !== "UNKNOWN" && (
                <span>{parsed.errors.length ? `发现 ${parsed.errors.length} 个必须处理的问题。` : "固定格式和当前步骤一致。"}</span>
              )}
            </div>

            {hasInput && parsed.errors.length > 0 && (
              <section className="result-check-list errors" aria-label="必须处理的问题">
                <h4>返回网页 AI 前需要处理</h4>
                <ul>{parsed.errors.map((item) => <li key={item}>{item}</li>)}</ul>
              </section>
            )}

            {hasInput && parsed.warnings.length > 0 && (
              <section className="result-check-list warnings" aria-label="建议补充的内容">
                <h4>建议让网页 AI 补充</h4>
                <ul>{parsed.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
              </section>
            )}
          </section>

          {hasInput && parsed.status === "INCOMPLETE" && (
            <section className="cocreation-card stage-not-finished">
              <header>
                <span>!</span>
                <div>
                  <h3>这一步还不能确认完成</h3>
                  <p>回到原来的网页 AI 对话继续即可，不需要另开一次聊天。</p>
                </div>
              </header>
              <dl className="stage-summary-grid">
                <div><dt>仍缺少</dt><dd>{parsed.missingDecisions || "网页 AI 没有明确列出"}</dd></div>
                <div><dt>不能结束的原因</dt><dd>{parsed.cannotEndReason || "网页 AI 没有说明"}</dd></div>
                <div><dt>下一轮只讨论</dt><dd>{parsed.nextDiscussion || "网页 AI 没有给出"}</dd></div>
              </dl>
            </section>
          )}

          {hasInput && (parsed.status === "RESULT" || parsed.status === "NO_RESULT") && (
            <section className="cocreation-card writeback-preview">
              <header>
                <span>4</span>
                <div>
                  <h3>确认前预览</h3>
                  <p>先看清会写入什么、不会写入什么，再决定是否完成本阶段。</p>
                </div>
              </header>

              {parsed.status === "RESULT" ? (
                <>
                  <div className="writeback-scope">
                    <div><span>将写入</span><strong>00_开书总表.md</strong></div>
                    <div><span>将改变</span><strong>{parsed.writebacks.length} 个标准字段</strong></div>
                    <div><span>不会写入</span><strong>聊天过程、被否决方案、后续阶段细节</strong></div>
                  </div>
                  <div className="writeback-list">
                    {parsed.writebacks.map((item, index) => (
                      <article key={`${item.field}-${index}`}>
                        <header>
                          <h4>{item.field || "字段名缺失"}</h4>
                          <span>{item.confirmation || "状态缺失"}</span>
                        </header>
                        <p>{item.content || "内容缺失"}</p>
                        <small>{item.necessity || "必要程度缺失"} · {item.lockTiming || "锁定时机缺失"}</small>
                      </article>
                    ))}
                  </div>
                  <dl className="stage-summary-grid">
                    <div><dt>冲突检查</dt><dd>{parsed.conflictCheck || "缺失"}</dd></div>
                    <div><dt>对下一步的影响</dt><dd>{parsed.nextImpact || "缺失"}</dd></div>
                  </dl>
                </>
              ) : (
                <dl className="stage-summary-grid">
                  <div><dt>本阶段状态</dt><dd>无正式结果，不会伪造四个标准字段。</dd></div>
                  <div><dt>结束原因</dt><dd>{parsed.noResultReason || "缺失"}</dd></div>
                  <div><dt>最迟重新处理</dt><dd>{parsed.reopenWhen || "缺失"}</dd></div>
                  <div><dt>对下一步的影响</dt><dd>{parsed.nextImpact || "缺失"}</dd></div>
                </dl>
              )}

              <div className="confirm-stage-actions">
                <div>
                  <strong>{parsed.canConfirm ? "页面检查已通过" : "还有问题，暂时不能确认"}</strong>
                  <span>{parsed.canConfirm ? "点击后保存当前版本；后续仍可回来修改。" : "请按上方问题让网页 AI 修正结束块。"}</span>
                </div>
                <button type="button" disabled={!parsed.canConfirm} onClick={confirmStage}>
                  确认并完成第 0 步
                </button>
              </div>
              {saveNotice && <p className="stage-save-notice" role="status">{saveNotice}</p>}
            </section>
          )}

          <section className="next-stage-card">
            <div>
              <span>完成第 0 步之后</span>
              <h3>第 1 步：品类、承诺与差异化</h3>
              <p>下一步会讨论这类作品必须给读者什么、有哪些雷点不能踩，以及你的差异化应该落在哪一层。不会在这里提前展开。</p>
            </div>
            <span className={saved ? "ready" : ""}>{saved ? "已具备进入条件" : "等待第 0 步确认"}</span>
          </section>
        </main>
      </div>
    </div>
  );
}
