import { useMemo, useState } from "react";
import {
  buildOpeningPackageDocuments,
  buildStagePrompt,
  canOpenStage,
  COCREATION_STAGES,
  COCREATION_STORAGE_KEY,
  CocreationWorkspace,
  createEmptyWorkspace,
  isPackageInternallyComplete,
  LEGACY_COCREATION_STORAGE_KEY,
  migrateStageZeroV1,
  nextStageNeedingWork,
  OpeningPackageDocument,
  parseStageResult,
  SavedStageZeroV1,
  stageAllowsProgress,
  StageDraft,
} from "./cocreation";

function readWorkspace(): CocreationWorkspace {
  try {
    const current = window.localStorage.getItem(COCREATION_STORAGE_KEY);
    if (current) {
      const value = JSON.parse(current) as CocreationWorkspace;
      if (value.version === 2 && value.stages && value.drafts) return value;
    }
    const legacy = window.localStorage.getItem(LEGACY_COCREATION_STORAGE_KEY);
    if (legacy) {
      const migrated = migrateStageZeroV1(JSON.parse(legacy) as SavedStageZeroV1);
      window.localStorage.setItem(COCREATION_STORAGE_KEY, JSON.stringify(migrated));
      return migrated;
    }
  } catch {
    // 损坏的浏览器草稿不应阻止页面打开；从一个新的本地工作区开始。
  }
  return createEmptyWorkspace();
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

function stageStatus(
  workspace: CocreationWorkspace,
  stageId: number,
  activeStageId: number,
) {
  const record = workspace.stages[String(stageId)];
  if (record?.needsReview) return { className: "review", label: "需要复核" };
  if (stageAllowsProgress(record)) return { className: "completed", label: "已保存" };
  if (record?.parsed.blocksNext) return { className: "blocked", label: "结果阻断后续" };
  if (stageId === activeStageId) return { className: "current", label: "正在进行" };
  if (canOpenStage(workspace, stageId)) return { className: "available", label: "可以开始" };
  return { className: "locked", label: "等待前序步骤" };
}

function uniqueDestinationFiles(stageId: number) {
  return [...new Set(COCREATION_STAGES[stageId].fields.map((item) => item.file))];
}

function combinedPackage(documents: OpeningPackageDocument[]) {
  return documents.map((document) => document.markdown.trim()).join("\n\n---\n\n");
}

export default function NewBookCocreationPage() {
  const initialWorkspace = useMemo(readWorkspace, []);
  const [workspace, setWorkspace] = useState<CocreationWorkspace>(initialWorkspace);
  const [copyNotice, setCopyNotice] = useState("");
  const [saveNotice, setSaveNotice] = useState("");
  const activeStageId = Math.min(
    Math.max(workspace.activeStageId, 0),
    COCREATION_STAGES.length - 1,
  );
  const stage = COCREATION_STAGES[activeStageId];
  const draft: StageDraft = workspace.drafts[String(activeStageId)]
    ?? { userInput: "", resultBlock: "" };
  const record = workspace.stages[String(activeStageId)];
  const prompt = useMemo(
    () => buildStagePrompt(stage, draft.userInput, workspace),
    [stage, draft.userInput, workspace],
  );
  const parsed = useMemo(
    () => parseStageResult(draft.resultBlock, stage),
    [draft.resultBlock, stage],
  );
  const documents = useMemo(
    () => buildOpeningPackageDocuments(workspace),
    [workspace],
  );
  const packageComplete = isPackageInternallyComplete(workspace);
  const completedCount = COCREATION_STAGES.filter((item) =>
    stageAllowsProgress(workspace.stages[String(item.id)]),
  ).length;
  const laterRecords = Object.values(workspace.stages)
    .filter((item) => item.stageId > activeStageId);
  const hasInput = Boolean(draft.resultBlock.trim());
  const destinations = uniqueDestinationFiles(activeStageId);

  function persist(next: CocreationWorkspace) {
    window.localStorage.setItem(COCREATION_STORAGE_KEY, JSON.stringify(next));
    setWorkspace(next);
  }

  function updateDraft(patch: Partial<StageDraft>) {
    const nextDraft = { ...draft, ...patch };
    persist({
      ...workspace,
      drafts: { ...workspace.drafts, [String(activeStageId)]: nextDraft },
      updatedAt: new Date().toISOString(),
    });
    setSaveNotice("");
  }

  function openStage(stageId: number) {
    if (!canOpenStage(workspace, stageId)) return;
    persist({ ...workspace, activeStageId: stageId, updatedAt: new Date().toISOString() });
    setCopyNotice("");
    setSaveNotice("");
  }

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
    const stages = { ...workspace.stages };
    stages[String(activeStageId)] = {
      stageId: activeStageId,
      userInput: draft.userInput.trim(),
      resultBlock: draft.resultBlock.trim(),
      confirmedAt,
      parsed,
      needsReview: false,
    };
    Object.values(stages).forEach((item) => {
      if (item.stageId > activeStageId) {
        stages[String(item.stageId)] = { ...item, needsReview: true };
      }
    });
    const provisional: CocreationWorkspace = {
      ...workspace,
      stages,
      updatedAt: confirmedAt,
    };
    const nextId = parsed.blocksNext
      ? activeStageId
      : nextStageNeedingWork(provisional, activeStageId);
    const next = { ...provisional, activeStageId: nextId };
    persist(next);
    setCopyNotice("");
    setSaveNotice(
      parsed.blocksNext
        ? `第 ${activeStageId} 步已记录，但结束块说明它会阻断后续；请按重开条件回来处理。`
        : activeStageId === COCREATION_STAGES.length - 1 && isPackageInternallyComplete(next)
          ? "十个步骤的内部结果已经齐备，可以下载五份通用开书材料。"
          : `第 ${activeStageId} 步已保存，已进入第 ${nextId} 步“${COCREATION_STAGES[nextId].name}”。`,
    );
  }

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
          <p className="product-kicker">新书共创 · 内部功能</p>
          <h1>从一个想法，逐步形成自己的开书包</h1>
        </div>
        <div className="topbar-actions">
          <span className={`package-progress-badge ${packageComplete ? "complete" : ""}`}>
            {packageComplete ? "十步结果已齐备" : `已完成 ${completedCount} / ${COCREATION_STAGES.length} 步`}
          </span>
          <a className="button-link secondary-button" href="/">← 返回拆书工作台</a>
        </div>
      </header>

      <div className="cocreation-layout">
        <aside className="cocreation-steps" aria-label="新书共创步骤">
          <div className="cocreation-steps-heading">
            <strong>开书顺序</strong>
            <span>按写书时间推进，发现冲突可以返回修订</span>
          </div>
          <ol>
            {COCREATION_STAGES.map((item) => {
              const status = stageStatus(workspace, item.id, activeStageId);
              const accessible = canOpenStage(workspace, item.id);
              return (
                <li key={item.id} className={`${status.className}${item.id === activeStageId ? " active" : ""}`}>
                  <button
                    type="button"
                    disabled={!accessible}
                    aria-current={item.id === activeStageId ? "step" : undefined}
                    onClick={() => openStage(item.id)}
                  >
                    <span>{status.className === "completed" ? "✓" : status.className === "review" ? "!" : item.id}</span>
                    <div>
                      <b>{item.name}</b>
                      <small>{status.label}</small>
                    </div>
                  </button>
                </li>
              );
            })}
          </ol>
          <div className="cocreation-boundary-note">
            <strong>与拆书结果分开</strong>
            <p>这里只记录用户确认的新书决定。参考书的方法可以作为讨论材料，但不会自动写入。</p>
          </div>
        </aside>

        <main className="cocreation-main">
          {saveNotice && <p className="stage-save-notice global" role="status">{saveNotice}</p>}

          <section className="cocreation-stage-intro">
            <div>
              <p>第 {stage.id} 步 · 依据 {stage.references}</p>
              <h2>{stage.name}</h2>
              <span>{stage.goal}</span>
              <small>{stage.whyNow}</small>
            </div>
            <div className="stage-destination">
              <span>本阶段写入</span>
              <strong>{destinations.join("、")}</strong>
              <small>不调用项目内 AI 接口</small>
            </div>
          </section>

          {record && (
            <section className={`saved-stage-banner${record.needsReview ? " needs-review" : ""}`} aria-label="已保存结果">
              <div>
                <strong>{record.needsReview ? `第 ${stage.id} 步需要重新复核` : `第 ${stage.id} 步已有确认版本`}</strong>
                <span>
                  保存于 {displayTime(record.confirmedAt)}。
                  {record.needsReview
                    ? "前序决定已经修改，当前结果仍保留但不再属于权威版本。"
                    : "可以返回修改；重新确认后，已有的后续步骤会被标记为需要复核。"}
                </span>
              </div>
              <span className={record.parsed.blocksNext ? "blocked" : ""}>
                {record.parsed.blocksNext ? "阻断后续" : record.needsReview ? "待复核" : "当前有效"}
              </span>
            </section>
          )}

          {laterRecords.length > 0 && (
            <section className="revision-impact-note">
              <strong>这是前序步骤</strong>
              <span>如果重新确认本步骤，第 {laterRecords.map((item) => item.stageId).sort((a, b) => a - b).join("、")} 步会保留原结果并标记为“需要复核”，不会静默覆盖或删除。</span>
            </section>
          )}

          <section className="cocreation-card">
            <header>
              <span>1</span>
              <div>
                <h3>写下你现在的想法</h3>
                <p>可以明确、模糊，也可以直接让 AI 帮你设计。不需要先填复杂表格。</p>
              </div>
            </header>
            <div className="idea-examples" aria-label="起点示例">
              {stage.examples.map((example) => (
                <button
                  type="button"
                  className="secondary-button"
                  key={example.label}
                  onClick={() => updateDraft({ userInput: example.text })}
                >
                  {example.label}
                </button>
              ))}
            </div>
            <label className="cocreation-field" htmlFor="cocreation-idea">
              <span>我对第 {stage.id} 步的当前想法</span>
              <textarea
                id="cocreation-idea"
                value={draft.userInput}
                onChange={(event) => updateDraft({ userInput: event.target.value })}
                placeholder={`写下你对“${stage.name}”已经确定、拿不准或希望 AI 帮助设计的内容……`}
              />
              <small>已输入 {draft.userInput.length} 个字符，草稿自动保存在当前浏览器。</small>
            </label>
          </section>

          <section className="cocreation-card">
            <header>
              <span>2</span>
              <div>
                <h3>复制提示词，去常用的网页 AI 讨论</h3>
                <p>提示词已经带上本阶段真正需要的既有确认内容、字段合同、完成标准和三种结束格式。</p>
              </div>
            </header>
            <div className="portable-prompt-actions">
              <button type="button" onClick={() => void copyText(prompt, `第 ${stage.id} 步完整提示词已复制。`)}>
                复制第 {stage.id} 步完整提示词
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
                <p>页面会检查步骤编号、标准字段、目标文件、确认状态和依赖影响，不保存完整聊天记录。</p>
              </div>
            </header>
            <label className="cocreation-field" htmlFor="stage-result">
              <span>网页 AI 给出的第 {stage.id} 步结论</span>
              <textarea
                id="stage-result"
                className="stage-result-input"
                value={draft.resultBlock}
                onChange={(event) => updateDraft({ resultBlock: event.target.value })}
                placeholder="从【阶段结束：已形成结果】、【阶段结束：无正式结果】或【阶段尚未结束】开始粘贴……"
              />
              <small>已输入 {draft.resultBlock.length} 个字符。</small>
            </label>

            <div className={`stage-result-status ${parsed.status.toLowerCase()}`}>
              <strong>{statusLabel}</strong>
              {!hasInput && <span>粘贴后才会开始检查。</span>}
              {hasInput && parsed.status === "UNKNOWN" && <span>当前内容不能识别为唯一的规定结束格式。</span>}
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
                  <p>回到原来的网页 AI 对话继续即可，不需要另开聊天或进入下一步。</p>
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
                  <p>先看清会写入什么、会使哪些后续结果需要复核，再保存本阶段。</p>
                </div>
              </header>
              {parsed.status === "RESULT" ? (
                <>
                  <div className="writeback-scope">
                    <div><span>将写入</span><strong>{[...new Set(parsed.writebacks.map((item) => item.file))].join("、") || "尚未识别"}</strong></div>
                    <div><span>将改变</span><strong>{parsed.writebacks.length} 个标准字段</strong></div>
                    <div><span>不会写入</span><strong>聊天过程、被否决方案、其他步骤的自由扩写</strong></div>
                  </div>
                  <div className="writeback-list">
                    {parsed.writebacks.map((item, index) => (
                      <article key={`${item.file}-${item.field}-${index}`}>
                        <header>
                          <h4>{item.field || "字段名缺失"}</h4>
                          <span>{item.confirmation || "状态缺失"}</span>
                        </header>
                        <small>{item.file || "文件名缺失"}</small>
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
                  <div><dt>本阶段状态</dt><dd>无正式结果，不会伪造标准字段。</dd></div>
                  <div><dt>结束原因</dt><dd>{parsed.noResultReason || "缺失"}</dd></div>
                  <div><dt>最迟重新处理</dt><dd>{parsed.reopenWhen || "缺失"}</dd></div>
                  <div><dt>对下一步的影响</dt><dd>{parsed.nextImpact || "缺失"}</dd></div>
                </dl>
              )}
              <div className="confirm-stage-actions">
                <div>
                  <strong>{parsed.canConfirm ? "页面检查已通过" : "还有问题，暂时不能确认"}</strong>
                  <span>
                    {parsed.canConfirm
                      ? parsed.blocksNext
                        ? "可以保存无结果记录，但当前说明会阻断后续步骤。"
                        : laterRecords.length
                          ? `保存后，第 ${laterRecords.map((item) => item.stageId).sort((a, b) => a - b).join("、")} 步会标记为需要复核。`
                          : "保存后进入下一个需要处理的步骤。"
                      : "请按上方问题让网页 AI 修正结束块。"}
                  </span>
                </div>
                <button type="button" disabled={!parsed.canConfirm} onClick={confirmStage}>
                  确认并保存第 {stage.id} 步
                </button>
              </div>
            </section>
          )}

          <section className="stage-navigation-card">
            <button
              type="button"
              className="secondary-button"
              disabled={activeStageId === 0 || !canOpenStage(workspace, activeStageId - 1)}
              onClick={() => openStage(activeStageId - 1)}
            >
              ← 返回上一步
            </button>
            <div>
              <strong>{packageComplete ? "十步内部结果已齐备" : `当前第 ${activeStageId} 步：${stage.name}`}</strong>
              <span>{packageComplete ? "仍可返回任何一步修订；修改前序内容后，后续结果会自动进入复核状态。" : stage.summary}</span>
            </div>
            <button
              type="button"
              className="secondary-button"
              disabled={activeStageId >= COCREATION_STAGES.length - 1 || !canOpenStage(workspace, activeStageId + 1)}
              onClick={() => openStage(activeStageId + 1)}
            >
              打开下一步 →
            </button>
          </section>

          <section className="package-documents-card">
            <header>
              <div>
                <span>当前通用源材料</span>
                <h3>五份稳定开书文档</h3>
                <p>讨论次数不会增加正式文件数量。后续适配具体写作 Agent 时另行转换，不污染这里的源材料。</p>
              </div>
              <button
                type="button"
                className="secondary-button"
                disabled={!documents.some((item) => item.fieldCount > 0 || item.noResultCount > 0)}
                onClick={() => downloadMarkdown("新书开书包_合并查看版.md", combinedPackage(documents))}
              >
                下载合并查看版
              </button>
            </header>
            <div className="package-document-list">
              {documents.map((document) => (
                <article key={document.filename} className={document.needsReview ? "needs-review" : ""}>
                  <div>
                    <strong>{document.title}</strong>
                    <span>{document.filename}</span>
                    <small>
                      {document.fieldCount
                        ? `${document.fieldCount} 个当前字段${document.noResultCount ? ` · ${document.noResultCount} 条无正式结果记录` : ""}${document.needsReview ? " · 含需复核结果" : ""}`
                        : document.noResultCount
                          ? `${document.noResultCount} 条无正式结果记录${document.needsReview ? " · 需要复核" : ""}`
                          : "尚未写入字段"}
                    </small>
                  </div>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={!document.fieldCount && !document.noResultCount}
                    onClick={() => downloadMarkdown(document.filename, document.markdown)}
                  >
                    下载
                  </button>
                </article>
              ))}
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
