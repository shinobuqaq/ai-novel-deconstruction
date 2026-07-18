import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  AnalysisProfile,
  api,
  ModelService,
  ModelServiceInput,
  ModelSettings,
} from "./api";

type SettingsSection = "services" | "profiles" | "privacy";

type ServiceDraft = ModelServiceInput & {
  id: string;
  configured: boolean;
};

const EMPTY_SERVICE: ServiceDraft = {
  id: "new",
  name: "",
  service_type: "OPENAI_COMPATIBLE",
  base_url: "",
  api_key: "",
  configured: false,
};

function serviceDraft(service: ModelService): ServiceDraft {
  return {
    id: service.id,
    name: service.name,
    service_type: service.service_type,
    base_url: service.base_url,
    api_key: "",
    configured: service.configured,
  };
}

function connectionLabel(service: ModelService) {
  if (service.last_test_status === "CONNECTED") return "连接正常";
  if (service.last_test_status === "FAILED") return "连接失败";
  if (service.configured) return "已保存，尚未测试";
  return "尚未连接";
}

export default function SettingsPage() {
  const [section, setSection] = useState<SettingsSection>("services");
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [selectedServiceId, setSelectedServiceId] = useState("");
  const [draft, setDraft] = useState<ServiceDraft>(EMPTY_SERVICE);
  const [profileDraft, setProfileDraft] = useState<AnalysisProfile | null>(null);
  const [catalogs, setCatalogs] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [manualModelEntry, setManualModelEntry] = useState(false);

  async function loadSettings(preferredServiceId?: string) {
    const loaded = await api.modelSettings();
    setSettings(loaded);
    const selected = loaded.services.find((item) => item.id === preferredServiceId)
      ?? loaded.services.find((item) => item.id === selectedServiceId)
      ?? loaded.services[0];
    if (selected) {
      setSelectedServiceId(selected.id);
      setDraft(serviceDraft(selected));
    }
    setProfileDraft(loaded.analysis_profiles[0] ?? null);
  }

  useEffect(() => {
    void loadSettings().catch((reason) => {
      setError(reason instanceof Error ? reason.message : String(reason));
    });
  }, []);

  const selectedService = useMemo(
    () => settings?.services.find((item) => item.id === selectedServiceId) ?? null,
    [settings, selectedServiceId],
  );

  function selectService(service: ModelService) {
    setSelectedServiceId(service.id);
    setDraft(serviceDraft(service));
    setNotice("");
    setError("");
    setConfirmDelete(false);
  }

  function beginNewService() {
    setSelectedServiceId("new");
    setDraft({ ...EMPTY_SERVICE });
    setNotice("");
    setError("");
    setConfirmDelete(false);
  }

  async function persistService() {
    const payload: ModelServiceInput = {
      name: draft.name,
      service_type: draft.service_type,
      base_url: draft.base_url,
      api_key: draft.api_key || undefined,
    };
    const saved = draft.id === "new"
      ? await api.createModelService(payload)
      : await api.saveModelService(draft.id, payload);
    await loadSettings(saved.id);
    return saved;
  }

  async function handleSaveService(event: FormEvent) {
    event.preventDefault();
    try {
      setBusy("save-service");
      setError("");
      const saved = await persistService();
      setNotice(`${saved.name} 已保存。建议继续测试连接。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleSaveAndTest() {
    try {
      setBusy("test-service");
      setError("");
      const saved = await persistService();
      const result = await api.testModelService(saved.id);
      setNotice(result.message);
      await loadSettings(saved.id);
      if (result.model_count > 0) {
        const catalog = await api.modelCatalog(saved.id);
        setCatalogs((current) => ({ ...current, [saved.id]: catalog.models }));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      await loadSettings(draft.id === "new" ? undefined : draft.id).catch(() => undefined);
    } finally {
      setBusy("");
    }
  }

  async function loadCatalog(serviceId: string) {
    if (!serviceId) return;
    try {
      setBusy("load-models");
      setError("");
      const result = await api.modelCatalog(serviceId);
      setCatalogs((current) => ({ ...current, [serviceId]: result.models }));
      setManualModelEntry(false);
      setNotice(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleSaveProfile(event: FormEvent) {
    event.preventDefault();
    if (!profileDraft) return;
    try {
      setBusy("save-profile");
      setError("");
      const saved = await api.saveAnalysisProfile(profileDraft.id, {
        name: profileDraft.name,
        service_id: profileDraft.service_id,
        model: profileDraft.model,
        temperature: profileDraft.temperature,
        max_output_tokens: profileDraft.max_output_tokens,
        reasoning_effort: profileDraft.reasoning_effort,
        timeout_seconds: profileDraft.timeout_seconds,
        max_retries: profileDraft.max_retries,
      });
      setProfileDraft(saved);
      setNotice("人物与事件分析方案已保存，下一次分析会使用这套设置。");
      await loadSettings(saved.service_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  async function handleDeleteService() {
    if (draft.id === "new") return;
    try {
      setBusy("delete-service");
      setError("");
      await api.deleteModelService(draft.id);
      setConfirmDelete(false);
      setNotice("模型服务已删除。");
      await loadSettings();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  const currentCatalog = profileDraft ? catalogs[profileDraft.service_id] ?? [] : [];
  const currentModelIsListed = Boolean(
    profileDraft?.model && currentCatalog.includes(profileDraft.model),
  );

  return (
    <div className="settings-shell">
      <header className="settings-topbar">
        <div>
          <p className="product-kicker">AI 小说拆解工作台</p>
          <h1>设置中心</h1>
        </div>
        <a className="button-link secondary-button" href="/">← 返回工作台</a>
      </header>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置分类">
          <button className={section === "services" ? "active" : ""} onClick={() => setSection("services")}>
            <span aria-hidden="true">●</span><b>模型服务</b><small>地址、密钥和连接状态</small>
          </button>
          <button className={section === "profiles" ? "active" : ""} onClick={() => setSection("profiles")}>
            <span aria-hidden="true">◆</span><b>分析方案</b><small>模型选择和分析参数</small>
          </button>
          <button className={section === "privacy" ? "active" : ""} onClick={() => setSection("privacy")}>
            <span aria-hidden="true">■</span><b>数据与隐私</b><small>本机保存和外发边界</small>
          </button>
        </nav>

        <main className="settings-main">
          {error && <div className="settings-message error" role="alert">{error}</div>}
          {notice && !error && <div className="settings-message success" role="status">{notice}</div>}

          {section === "services" && (
            <section className="settings-section">
              <header className="settings-section-heading">
                <div><p>模型服务</p><h2>连接在线 AI</h2></div>
                <button type="button" className="secondary-button" onClick={beginNewService}>＋ 添加服务</button>
              </header>
              <div className="service-workspace">
                <aside className="service-list" aria-label="已保存的模型服务">
                  {settings?.services.map((service) => (
                    <button
                      type="button"
                      key={service.id}
                      className={selectedServiceId === service.id ? "active" : ""}
                      onClick={() => selectService(service)}
                    >
                      <strong>{service.name}</strong>
                      <span className={`connection-state ${service.last_test_status.toLowerCase()}`}>
                        {connectionLabel(service)}
                      </span>
                    </button>
                  ))}
                  {selectedServiceId === "new" && <button type="button" className="active"><strong>新模型服务</strong><span>尚未保存</span></button>}
                </aside>

                <form className="service-editor" onSubmit={handleSaveService}>
                  <div className="field-grid two-columns">
                    <label>服务名称<input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：主要分析服务" /></label>
                    <label>服务类型
                      <select value={draft.service_type} onChange={(event) => setDraft({ ...draft, service_type: event.target.value as ModelService["service_type"] })}>
                        <option value="OPENAI">OpenAI 官方</option>
                        <option value="OPENAI_COMPATIBLE">兼容 OpenAI 的服务</option>
                      </select>
                    </label>
                  </div>
                  <label>接口地址<input value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://api.example.com/v1" /></label>
                  <label>API Key（访问密钥）
                    <input type="password" autoComplete="off" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} placeholder={draft.configured ? "已保存；留空表示不更换" : "输入服务提供的 API Key"} />
                  </label>
                  {selectedService && (
                    <div className="connection-summary">
                      <div><span>当前状态</span><strong>{connectionLabel(selectedService)}</strong></div>
                      <p>{selectedService.last_test_message || "保存后执行连接测试，系统会检查密钥并尝试读取模型列表。"}</p>
                    </div>
                  )}
                  <footer className="settings-actions">
                    {draft.id !== "new" && (settings?.services.length ?? 0) > 1 && !confirmDelete && (
                      <button type="button" className="danger-button settings-delete" disabled={Boolean(busy)} onClick={() => setConfirmDelete(true)}>删除服务</button>
                    )}
                    {confirmDelete && (
                      <div className="delete-confirmation">
                        <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => setConfirmDelete(false)}>取消</button>
                        <button type="button" className="danger-button" disabled={Boolean(busy)} onClick={() => void handleDeleteService()}>{busy === "delete-service" ? "正在删除" : "确认删除"}</button>
                      </div>
                    )}
                    <button type="submit" className="secondary-button" disabled={Boolean(busy)}>{busy === "save-service" ? "正在保存" : "保存"}</button>
                    <button type="button" onClick={() => void handleSaveAndTest()} disabled={Boolean(busy)}>{busy === "test-service" ? "正在连接" : "保存并测试连接"}</button>
                  </footer>
                </form>
              </div>
            </section>
          )}

          {section === "profiles" && profileDraft && (
            <section className="settings-section">
              <header className="settings-section-heading">
                <div><p>分析方案</p><h2>人物与事件精确提取</h2></div>
                <span className="section-state">当前已接入分析流程</span>
              </header>
              <form className="profile-editor" onSubmit={handleSaveProfile}>
                <div className="profile-summary-band">
                  <label>使用的模型服务
                    <select
                      value={profileDraft.service_id}
                      onChange={(event) => {
                        setProfileDraft({ ...profileDraft, service_id: event.target.value, model: "" });
                        setManualModelEntry(false);
                      }}
                    >
                      {settings?.services.map((service) => <option key={service.id} value={service.id}>{service.name}{service.configured ? "" : "（未连接）"}</option>)}
                    </select>
                  </label>
                  <div className="profile-field">
                    <label htmlFor="analysis-model">分析模型 {currentCatalog.length > 0 && <span>已加载 {currentCatalog.length} 个</span>}</label>
                    <div className="model-picker">
                      {manualModelEntry ? (
                        <input
                          id="analysis-model"
                          value={profileDraft.model}
                          onChange={(event) => setProfileDraft({ ...profileDraft, model: event.target.value })}
                          placeholder="填写服务商提供的模型名称"
                        />
                      ) : (
                        <select
                          id="analysis-model"
                          value={profileDraft.model}
                          onChange={(event) => setProfileDraft({ ...profileDraft, model: event.target.value })}
                        >
                          {!profileDraft.model && <option value="">请先获取模型</option>}
                          {profileDraft.model && !currentModelIsListed && (
                            <option value={profileDraft.model}>{profileDraft.model}（当前设置，列表中未找到）</option>
                          )}
                          {currentCatalog.map((model) => <option value={model} key={model}>{model}</option>)}
                        </select>
                      )}
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={busy === "load-models"}
                        onClick={() => void loadCatalog(profileDraft.service_id)}
                      >
                        {busy === "load-models" ? "读取中" : currentCatalog.length ? "刷新模型" : "获取模型"}
                      </button>
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => setManualModelEntry((current) => !current)}
                      >
                        {manualModelEntry ? "使用模型列表" : "手工填写"}
                      </button>
                    </div>
                    {!currentCatalog.length && <small>点击“获取模型”后，可从服务返回的模型中直接选择。</small>}
                  </div>
                </div>

                <div className="recommended-settings">
                  <div><span>结果稳定性</span><strong>质量优先</strong><small>适合人物、事件和证据抽取</small></div>
                  <label>推理强度
                    <div className="segmented-control">
                      {(["none", "low", "medium", "high"] as const).map((effort) => (
                        <button
                          type="button"
                          key={effort}
                          aria-pressed={profileDraft.reasoning_effort === effort}
                          className={profileDraft.reasoning_effort === effort ? "active" : ""}
                          onClick={() => setProfileDraft({ ...profileDraft, reasoning_effort: effort })}
                        >
                          {{ none: "关闭", low: "较低", medium: "中等", high: "较高" }[effort]}
                        </button>
                      ))}
                    </div>
                    <small>部分不支持推理参数的兼容服务会拒绝该设置；遇到参数错误时请选择“关闭”。</small>
                  </label>
                </div>

                <button type="button" className="advanced-toggle" onClick={() => setShowAdvanced((current) => !current)}>{showAdvanced ? "收起高级参数" : "展开高级参数"}</button>
                {showAdvanced && (
                  <div className="parameter-grid">
                    <label>温度 <span>{profileDraft.temperature.toFixed(2)}</span>
                      <input type="range" min="0" max="2" step="0.05" value={profileDraft.temperature} onChange={(event) => setProfileDraft({ ...profileDraft, temperature: Number(event.target.value) })} />
                      <small>越低越稳定，越高越发散。信息抽取建议保持较低。</small>
                    </label>
                    <label>最大输出长度
                      <input type="number" min="256" max="128000" step="256" value={profileDraft.max_output_tokens} onChange={(event) => setProfileDraft({ ...profileDraft, max_output_tokens: Number(event.target.value) })} />
                      <small>限制单次模型返回的最大内容量。</small>
                    </label>
                    <label>单次超时（秒）
                      <input type="number" min="10" max="1800" value={profileDraft.timeout_seconds} onChange={(event) => setProfileDraft({ ...profileDraft, timeout_seconds: Number(event.target.value) })} />
                      <small>长篇批次和强推理模型需要更长等待时间。</small>
                    </label>
                    <label>失败后重试次数
                      <input type="number" min="0" max="10" value={profileDraft.max_retries} onChange={(event) => setProfileDraft({ ...profileDraft, max_retries: Number(event.target.value) })} />
                      <small>只对超时、限流和临时故障自动重试。</small>
                    </label>
                  </div>
                )}
                <footer className="settings-actions"><button type="submit" disabled={Boolean(busy)}>{busy === "save-profile" ? "正在保存" : "保存分析方案"}</button></footer>
              </form>
            </section>
          )}

          {section === "privacy" && (
            <section className="settings-section privacy-section">
              <header className="settings-section-heading"><div><p>数据与隐私</p><h2>当前保存与外发边界</h2></div></header>
              <dl className="privacy-list">
                <div><dt>小说与项目数据</dt><dd>保存在本机工作区，不上传到代码仓库。</dd></div>
                <div><dt>API Key（访问密钥）</dt><dd>只保存在本机密钥目录，页面和接口不会返回完整内容。</dd></div>
                <div><dt>发送给在线 AI 的内容</dt><dd>当前人物与事件分析会按章节批次发送原文片段，不会一次发送整本小说。</dd></div>
                <div><dt>诊断信息</dt><dd>只记录服务、模型、参数、用量和错误类型，不记录访问密钥。</dd></div>
              </dl>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
