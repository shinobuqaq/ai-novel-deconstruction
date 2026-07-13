const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

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
  error_message: string | null;
  created_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${detail}`);
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
};
