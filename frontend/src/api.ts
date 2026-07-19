const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:18000";

export type Project = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

export type Task = {
  id: string;
  project_id: string;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  result_artifact_id: string | null;
  attempts: number;
  max_attempts: number;
  lease_owner: string | null;
  lease_expires_at: string | null;
  current_attempt_id: string | null;
  lease_generation: number;
  next_attempt_at: string | null;
  cancel_requested_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
};

export type SourceVersion = {
  id: string;
  document_id: string;
  version_no: number;
  content_hash: string;
  parser_version: number;
  total_chars: number;
  chapter_count: number;
  detected_encoding: string | null;
  status: "REVIEW" | "CONFIRMED";
  created_at: string;
  confirmed_at: string | null;
};

export type SourceUnit = {
  id: string;
  source_version_id: string;
  ordinal: number;
  unit_type: string;
  title: string;
  start_char: number;
  end_char: number;
  content_hash: string;
  char_count: number;
};

export type SourceIssue = {
  id: string;
  source_version_id: string;
  source_unit_id: string | null;
  code: string;
  severity: "BLOCKING" | "WARNING" | "REVIEW";
  message: string;
  details: Record<string, unknown>;
  status: "OPEN" | "RESOLVED";
  created_at: string;
  resolved_at: string | null;
};

export type SourceImport = {
  document: {
    id: string;
    project_id: string;
    original_filename: string;
    source_format: string;
    created_at: string;
  };
  version: SourceVersion;
  units: SourceUnit[];
  issues: SourceIssue[];
  reused_existing: boolean;
};

export type SourceUnitContent = {
  id: string;
  source_version_id: string;
  ordinal: number;
  title: string;
  start_char: number;
  end_char: number;
  content: string;
};

export type OpenAIConfig = {
  configured: boolean;
  base_url: string;
  model: string;
};

export type ModelService = {
  id: string;
  name: string;
  service_type: "OPENAI" | "OPENAI_COMPATIBLE";
  base_url: string;
  configured: boolean;
  last_tested_at: string | null;
  last_test_status: "NOT_TESTED" | "CONNECTED" | "FAILED";
  last_test_message: string | null;
  capabilities: {
    tested_model: string | null;
    tested_at: string | null;
    ordinary_request: "UNTESTED" | "SUPPORTED" | "FAILED";
    structured_output: "UNTESTED" | "STRICT_JSON_SCHEMA" | "JSON_ONLY" | "UNSUPPORTED";
    temperature: "UNTESTED" | "SUPPORTED" | "FAILED" | "UNSUPPORTED";
    reasoning_effort: "UNTESTED" | "SUPPORTED" | "FAILED" | "UNSUPPORTED";
    model_catalog: "UNTESTED" | "SUPPORTED" | "FAILED" | "UNSUPPORTED";
  };
};

export type AnalysisProfile = {
  id: string;
  name: string;
  task_type: string;
  service_id: string;
  model: string;
  temperature: number | null;
  max_output_tokens: number;
  reasoning_effort: "auto" | "none" | "low" | "medium" | "high";
  timeout_seconds: number;
  max_retries: number;
};

export type ModelSettings = {
  services: ModelService[];
  analysis_profiles: AnalysisProfile[];
};

export type ModelServiceInput = {
  name: string;
  service_type: ModelService["service_type"];
  base_url: string;
  api_key?: string;
};

export type AnalysisProfileInput = Omit<AnalysisProfile, "id" | "task_type">;

export type AnalysisRun = {
  id: string;
  source_version_id: string;
  stage: string;
  status: "PENDING" | "RUNNING" | "REVIEW" | "CONFIRMED" | "FAILED" | "CANCELLED";
  total_batches: number;
  completed_batches: number;
  failed_batches: number;
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
  finished_at: string | null;
  confirmed_at: string | null;
};

export type EntityCandidate = {
  id: string;
  run_id: string;
  source_version_id: string;
  name: string;
  entity_type: "PERSON" | "ORGANIZATION" | "PLACE" | "OBJECT" | "OTHER";
  aliases: string[];
  description: string;
  evidence_ids: string[];
  status: "VALID" | "UNCERTAIN";
  confidence: number;
};

export type EventCandidate = {
  id: string;
  run_id: string;
  source_version_id: string;
  title: string;
  event_type: string;
  summary: string;
  participants: string[];
  evidence_ids: string[];
  start_char: number;
  end_char: number;
  status: "VALID" | "UNCERTAIN";
  confidence: number;
};

export type WorkbenchCharacter = {
  id: string;
  name: string;
  aliases: string[];
  description: string;
  evidence_ids: string[];
  event_ids: string[];
  first_chapter_ordinal: number | null;
  first_chapter_title: string | null;
  last_chapter_ordinal: number | null;
  last_chapter_title: string | null;
  appearance_count: number;
  activity_level: string;
  status: "VALID" | "UNCERTAIN";
  confidence: number;
  role: "PROTAGONIST" | "CORE_SUPPORTING" | "IMPORTANT_SUPPORTING" | "MINOR" | "UNCLASSIFIED";
  role_reason: string;
  goals: string[];
  motivations: string[];
  current_state: string;
};

export type WorkbenchEvent = {
  id: string;
  title: string;
  event_type: string;
  summary: string;
  people: string[];
  related_entities: string[];
  evidence_ids: string[];
  chapter_ordinals: number[];
  chapter_titles: string[];
  start_char: number;
  end_char: number;
  mention_count: number;
  status: "VALID" | "UNCERTAIN";
  confidence: number;
};

export type WorkbenchPhase = {
  id: string;
  title: string;
  summary: string;
  event_ids: string[];
  evidence_ids: string[];
  chapter_ordinals: number[];
  chapter_titles: string[];
  people: string[];
  situation: string;
  goal: string;
  obstacle: string;
  key_actions: string[];
  outcome: string;
  change: string;
  next_hook: string;
};

export type WorkbenchStoryOverview = {
  premise: string;
  synopsis: string;
  protagonist: string;
  protagonist_goal: string;
  central_conflict: string;
  current_situation: string;
  unresolved_questions: string[];
  evidence_ids: string[];
};

export type WorkbenchCharacterRelation = {
  source_name: string;
  target_name: string;
  relation: string;
  current_state: string;
  changes: string[];
  evidence_ids: string[];
};

export type WorkbenchEventRelation = {
  source_event_id: string;
  target_event_id: string;
  relation: string;
  explanation: string;
  evidence_ids: string[];
  source_title: string;
  target_title: string;
};

export type WorkbenchFactVersion = {
  id: string;
  subject: string;
  predicate: string;
  value: string;
  fact_type: string;
  status: string;
  valid_from_chapter: number;
  valid_to_chapter: number | null;
  evidence_ids: string[];
  counter_evidence_ids: string[];
};

export type WorkbenchStateChange = {
  id: string;
  subject: string;
  aspect: string;
  before: string;
  after: string;
  chapter_ordinal: number;
  event_id: string | null;
  evidence_ids: string[];
};

export type WorkbenchActorKnowledge = {
  id: string;
  actor: string;
  proposition: string;
  state: string;
  chapter_ordinal: number;
  evidence_ids: string[];
};

export type WorkbenchWorldRule = {
  id: string;
  title: string;
  description: string;
  limitations: string[];
  costs: string[];
  exceptions: string[];
  evidence_ids: string[];
  discovered_chapter: number;
};

export type WorkbenchForeshadowing = {
  id: string;
  title: string;
  setup: string;
  lifecycle: string;
  setup_chapter: number;
  payoff_chapter: number | null;
  event_ids: string[];
  evidence_ids: string[];
};

export type WorkbenchConflict = {
  id: string;
  title: string;
  conflict_type: string;
  participants: string[];
  goals: string;
  obstacles: string;
  stakes: string;
  escalation: string[];
  resolution: string;
  status: string;
  event_ids: string[];
  evidence_ids: string[];
};

export type WorkbenchSceneAnalysis = {
  id: string;
  chapter_ordinal: number;
  function: string;
  summary: string;
  information_released: string[];
  action_dialogue_balance: string;
  pace: string;
  evidence_ids: string[];
};

export type WorkbenchClaim = {
  id: string;
  claim_kind: string;
  claim_text: string;
  scope: string;
  evidence_ids: string[];
  counter_evidence_ids: string[];
  verification_status: string;
  confidence: number;
};

export type WorkbenchDeepAnalysis = {
  fact_versions: WorkbenchFactVersion[];
  state_changes: WorkbenchStateChange[];
  actor_knowledge: WorkbenchActorKnowledge[];
  world_rules: WorkbenchWorldRule[];
  foreshadowing: WorkbenchForeshadowing[];
  conflicts: WorkbenchConflict[];
  scene_analysis: WorkbenchSceneAnalysis[];
  claims: WorkbenchClaim[];
};

export type WorkbenchChapterRef = {
  ordinal: number;
  title: string;
};

export type AnalysisIssue = {
  id: string;
  run_id: string;
  target_kind: string;
  target_id: string | null;
  target_label: string;
  category: string;
  note: string;
  status: "OPEN" | "RESOLVED";
  created_at: string;
  resolved_at: string | null;
};

export type DeepAnalysisRevision = {
  revision_no: number;
  created_at: string;
  prompt_version: string;
};

export type DeepAnalysisDiff = {
  from_revision: number;
  to_revision: number;
  added: Record<string, string[]>;
  removed: Record<string, string[]>;
  changed_counts: Record<string, number>;
};

export type Workbench = {
  run_id: string;
  source_version_id: string;
  status: string;
  characters: WorkbenchCharacter[];
  related_entities: EntityCandidate[];
  events: WorkbenchEvent[];
  phases: WorkbenchPhase[];
  narrative_status: "READY" | "NOT_GENERATED";
  story_overview: WorkbenchStoryOverview | null;
  character_relations: WorkbenchCharacterRelation[];
  event_relations: WorkbenchEventRelation[];
  deep_status: "READY" | "NOT_GENERATED";
  deep_analysis: WorkbenchDeepAnalysis | null;
  deep_revision: number | null;
  chapters: WorkbenchChapterRef[];
};

export type EvidenceContext = {
  evidence: {
    id: string;
    source_version_id: string;
    source_unit_id: string;
    paragraph_index: number;
    start_char: number;
    end_char: number;
    text_snapshot: string;
    context_hash: string;
  };
  chapter_title: string;
  context_start: number;
  context_end: number;
  context_text: string;
};

function errorMessage(payload: unknown, status: number) {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "object" && detail !== null && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
    if (typeof detail === "string") {
      const known: Record<string, string> = {
        PROJECT_NOT_FOUND: "没有找到这个小说项目。",
        SOURCE_VERSION_NOT_FOUND: "没有找到这次导入记录。",
        SOURCE_UNIT_NOT_FOUND: "没有找到这个章节。",
        SOURCE_ISSUE_NOT_FOUND: "这个问题已经不存在。",
        ANALYSIS_RUN_NOT_FOUND: "没有找到这次分析记录。",
      };
      return known[detail] ?? detail;
    }
  }
  return `请求失败（${status}）`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (typeof init?.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as unknown;
    throw new Error(errorMessage(payload, response.status));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; app: string }>("/health"),
  projects: () => request<Project[]>("/api/projects"),
  createProject: (name: string) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  sourceVersions: (projectId: string) =>
    request<SourceVersion[]>(`/api/projects/${projectId}/source-versions`),
  importSource: (projectId: string, file: File) =>
    request<SourceImport>(
      `/api/projects/${projectId}/sources/import?filename=${encodeURIComponent(file.name)}`,
      { method: "POST", body: file },
    ),
  sourceChapters: (versionId: string) =>
    request<SourceUnit[]>(`/api/source-versions/${versionId}/chapters`),
  sourceIssues: (versionId: string) =>
    request<SourceIssue[]>(`/api/source-versions/${versionId}/issues`),
  chapterContent: (unitId: string) =>
    request<SourceUnitContent>(`/api/chapters/${unitId}/content`),
  resolveSourceIssue: (issueId: string) =>
    request<SourceIssue>(`/api/source-issues/${issueId}/resolve`, { method: "POST" }),
  confirmSourceVersion: (versionId: string) =>
    request<SourceVersion>(`/api/source-versions/${versionId}/confirm`, { method: "POST" }),
  openAIConfig: () => request<OpenAIConfig>("/api/settings/openai"),
  saveOpenAIConfig: (payload: { api_key?: string; base_url?: string; model?: string }) =>
    request<OpenAIConfig>("/api/settings/openai", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  modelSettings: () => request<ModelSettings>("/api/settings/models"),
  createModelService: (payload: ModelServiceInput) =>
    request<ModelService>("/api/settings/model-services", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveModelService: (serviceId: string, payload: ModelServiceInput) =>
    request<ModelService>(`/api/settings/model-services/${serviceId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteModelService: (serviceId: string) =>
    request<void>(`/api/settings/model-services/${serviceId}`, { method: "DELETE" }),
  testModelService: (serviceId: string) =>
    request<{ service: ModelService; model_count: number; message: string }>(
      `/api/settings/model-services/${serviceId}/test`,
      { method: "POST" },
    ),
  modelCatalog: (serviceId: string) =>
    request<{ service_id: string; models: string[]; message: string }>(
      `/api/settings/model-services/${serviceId}/models`,
    ),
  testAnalysisProfile: (profileId: string) =>
    request<{ service: ModelService; message: string }>(
      `/api/settings/analysis-profiles/${profileId}/test`,
      { method: "POST" },
    ),
  saveAnalysisProfile: (profileId: string, payload: AnalysisProfileInput) =>
    request<AnalysisProfile>(`/api/settings/analysis-profiles/${profileId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  latestAnalysis: (versionId: string) =>
    request<AnalysisRun | null>(`/api/source-versions/${versionId}/analysis/entities-events`),
  startAnalysis: (versionId: string) =>
    request<AnalysisRun>(`/api/source-versions/${versionId}/analysis/entities-events/start`, {
      method: "POST",
    }),
  analysisEntities: (runId: string) =>
    request<EntityCandidate[]>(`/api/analysis-runs/${runId}/entities`),
  analysisEvents: (runId: string) =>
    request<EventCandidate[]>(`/api/analysis-runs/${runId}/events`),
  analysisWorkbench: (runId: string) =>
    request<Workbench>(`/api/analysis-runs/${runId}/workbench`),
  startDeepAnalysis: (runId: string) =>
    request<AnalysisRun>(`/api/analysis-runs/${runId}/deep/start`, { method: "POST" }),
  analysisIssues: (runId: string) =>
    request<AnalysisIssue[]>(`/api/analysis-runs/${runId}/issues`),
  createAnalysisIssue: (runId: string, payload: { target_kind: string; target_id: string | null; target_label: string; category: string; note: string }) =>
    request<AnalysisIssue>(`/api/analysis-runs/${runId}/issues`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  resolveAnalysisIssue: (issueId: string) =>
    request<AnalysisIssue>(`/api/analysis-issues/${issueId}/resolve`, { method: "POST" }),
  recomputeDeepAnalysis: (runId: string) =>
    request<AnalysisRun>(`/api/analysis-runs/${runId}/deep/recompute`, { method: "POST" }),
  deepAnalysisRevisions: (runId: string) =>
    request<DeepAnalysisRevision[]>(`/api/analysis-runs/${runId}/deep/revisions`),
  deepAnalysisDiff: (runId: string) =>
    request<DeepAnalysisDiff>(`/api/analysis-runs/${runId}/deep/diff`),
  confirmAnalysis: (runId: string) =>
    request<AnalysisRun>(`/api/analysis-runs/${runId}/confirm`, { method: "POST" }),
  evidenceContext: (evidenceId: string) =>
    request<EvidenceContext>(`/api/evidence/${evidenceId}`),
  tasks: () => request<Task[]>("/api/tasks"),
  createEchoTask: (projectId: string, message: string) =>
    request<Task>("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        kind: "fake.echo",
        payload: { message },
      }),
    }),
  cancelTask: (taskId: string) =>
    request<Task>(`/api/tasks/${taskId}/cancel`, { method: "POST" }),
  retryTask: (taskId: string) =>
    request<Task>(`/api/tasks/${taskId}/retry`, { method: "POST" }),
};
