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
