import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, Project, Task } from "./api";

const STATUS_LABELS: Record<string, string> = {
  PENDING: "待处理",
  RUNNING: "运行中",
  RETRY_WAIT: "等待重试",
  CANCEL_REQUESTED: "正在取消",
  SUCCEEDED: "已完成",
  FAILED: "已失败",
  CANCELLED: "已取消",
};

function formatTime(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function shortId(value: string | null) {
  if (!value) return "-";
  return value.length > 18 ? `${value.slice(0, 10)}...${value.slice(-6)}` : value;
}

function taskMessage(task: Task) {
  const message = task.payload.message;
  return typeof message === "string" && message.trim() ? message : "无任务内容";
}

export default function App() {
  const [health, setHealth] = useState("checking");
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projectName, setProjectName] = useState("测试小说");
  const [message, setMessage] = useState("分析这段测试内容");
  const [selectedProject, setSelectedProject] = useState("");
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [confirmCancelId, setConfirmCancelId] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [healthResult, projectResult, taskResult] = await Promise.all([
        api.health(),
        api.projects(),
        api.tasks(),
      ]);
      setHealth(healthResult.status);
      setProjects(projectResult);
      setTasks(taskResult);
      setSelectedProject((current) => current || projectResult[0]?.id || "");
      setError("");
    } catch (err) {
      setHealth("offline");
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const projectNames = useMemo(
    () => new Map(projects.map((project) => [project.id, project.name])),
    [projects],
  );
  const activeCount = tasks.filter((task) =>
    ["PENDING", "RUNNING", "RETRY_WAIT", "CANCEL_REQUESTED"].includes(task.status),
  ).length;
  const failedCount = tasks.filter((task) => task.status === "FAILED").length;
  const completedCount = tasks.filter((task) => task.status === "SUCCEEDED").length;

  async function runAction(key: string, action: () => Promise<unknown>) {
    try {
      setBusyAction(key);
      setError("");
      await action();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function handleCreateProject(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim()) return;
    await runAction("create-project", async () => {
      const project = await api.createProject(projectName.trim());
      setSelectedProject(project.id);
    });
  }

  async function handleCreateTask(event: FormEvent) {
    event.preventDefault();
    if (!selectedProject || !message.trim()) return;
    await runAction("create-task", () =>
      api.createEchoTask(selectedProject, message.trim()),
    );
  }

  async function handleCancel(task: Task) {
    await runAction(`cancel-${task.id}`, () => api.cancelTask(task.id));
    setConfirmCancelId("");
  }

  async function handleRetry(task: Task) {
    await runAction(`retry-${task.id}`, () => api.retryTask(task.id));
  }

  return (
    <main>
      <header className="app-header">
        <div>
          <p className="eyebrow">P0 工程地基</p>
          <h1>内部任务调试台</h1>
          <p className="header-note">仅用于开发人员验证后台任务，不是小说导入和拆解界面。</p>
        </div>
        <div className={`api-status ${health}`}>
          <span className="status-dot" />
          {health === "ok" ? "服务正常" : health === "offline" ? "服务离线" : "正在连接"}
        </div>
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <section className="summary-strip" aria-label="任务概览">
        <div><span>全部任务</span><strong>{tasks.length}</strong></div>
        <div><span>处理中</span><strong>{activeCount}</strong></div>
        <div><span>已完成</span><strong>{completedCount}</strong></div>
        <div><span>需处理</span><strong>{failedCount}</strong></div>
      </section>

      <section className="workspace-grid">
        <article className="panel project-panel">
          <div className="panel-heading">
            <div><h2>项目</h2><span>{projects.length} 个</span></div>
          </div>
          <form onSubmit={handleCreateProject} className="compact-form">
            <label htmlFor="project-name">项目名称</label>
            <div className="inline-control">
              <input
                id="project-name"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                maxLength={200}
              />
              <button type="submit" disabled={busyAction === "create-project"}>
                {busyAction === "create-project" ? "创建中" : "新建"}
              </button>
            </div>
          </form>
          <div className="project-list">
            {projects.map((project) => (
              <button
                type="button"
                key={project.id}
                className={`project-item ${selectedProject === project.id ? "selected" : ""}`}
                onClick={() => setSelectedProject(project.id)}
              >
                <span>{project.name}</span>
                <small title={project.id}>{shortId(project.id)}</small>
              </button>
            ))}
            {!projects.length && <p className="empty-state">暂无项目</p>}
          </div>
        </article>

        <article className="panel task-creator">
          <div className="panel-heading">
            <div><h2>新建测试任务</h2><span>本地模拟 Provider</span></div>
          </div>
          <form onSubmit={handleCreateTask}>
            <label htmlFor="task-project">所属项目</label>
            <select
              id="task-project"
              value={selectedProject}
              onChange={(event) => setSelectedProject(event.target.value)}
            >
              <option value="">选择项目</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
              <label htmlFor="task-message">测试消息（最多 4000 字符）</label>
            <textarea
              id="task-message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              maxLength={4000}
            />
            <div className="form-footer">
              <span>{message.length} / 4000</span>
              <button
                type="submit"
                disabled={!selectedProject || !message.trim() || busyAction === "create-task"}
              >
                {busyAction === "create-task" ? "提交中" : "提交任务"}
              </button>
            </div>
          </form>
        </article>
      </section>

      <section className="panel run-center">
        <div className="run-center-heading">
          <div>
            <h2>后台任务记录</h2>
          </div>
          <button type="button" className="secondary-button" onClick={() => void refresh()}>
            刷新
          </button>
        </div>

        <div className="task-table-wrap">
          <table>
            <thead>
              <tr>
                <th>状态</th>
                <th>项目与任务</th>
                <th>执行</th>
                <th>时间与结果</th>
                <th><span className="visually-hidden">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => {
                const errorCode = task.last_error_code ?? task.error_code;
                const errorMessage = task.last_error_message ?? task.error_message;
                const canCancel = ["PENDING", "RUNNING", "RETRY_WAIT"].includes(task.status);
                const canRetry = task.status === "FAILED" && task.attempts < task.max_attempts;
                return (
                  <tr key={task.id}>
                    <td data-label="状态">
                      <div className="cell-stack">
                        <span className={`state-badge ${task.status.toLowerCase()}`}>
                          {STATUS_LABELS[task.status] ?? task.status}
                        </span>
                        {task.status === "CANCEL_REQUESTED" && <small>等待 Worker 确认</small>}
                      </div>
                    </td>
                    <td data-label="项目与任务">
                      <div className="cell-stack task-summary">
                        <strong>{projectNames.get(task.project_id) ?? "未知项目"}</strong>
                        <span>{taskMessage(task)}</span>
                        {errorCode && (
                          <div className="task-error">
                            <b>{task.status === "FAILED" ? "错误" : "上次错误"} · {errorCode}</b>
                            {errorMessage && <span>{errorMessage}</span>}
                          </div>
                        )}
                      </div>
                    </td>
                    <td data-label="执行">
                      <div className="cell-stack execution-cell">
                        <strong>{task.attempts} / {task.max_attempts} 次</strong>
                        {task.next_attempt_at && <small>重试 {formatTime(task.next_attempt_at)}</small>}
                      </div>
                    </td>
                    <td data-label="时间与结果">
                      <div className="cell-stack result-cell">
                        <span>创建 {formatTime(task.created_at)}</span>
                        {task.finished_at && <span>结束 {formatTime(task.finished_at)}</span>}
                        {task.result_artifact_id && <span>结果已保存</span>}
                        <details className="technical-details">
                          <summary>技术详情</summary>
                          <code>任务 {shortId(task.id)}</code>
                          <code>执行代次 {task.lease_generation}</code>
                          {task.lease_owner && <code>Worker {shortId(task.lease_owner)}</code>}
                          {task.result_artifact_id && <code>结果 {shortId(task.result_artifact_id)}</code>}
                        </details>
                      </div>
                    </td>
                    <td data-label="操作" className="action-column">
                      <div className="cell-stack action-cell">
                        {canCancel && confirmCancelId !== task.id && (
                          <button
                            type="button"
                            className="danger-button"
                            disabled={busyAction === `cancel-${task.id}`}
                            onClick={() => setConfirmCancelId(task.id)}
                          >
                            取消
                          </button>
                        )}
                        {canCancel && confirmCancelId === task.id && (
                          <div className="confirm-actions">
                            <button
                              type="button"
                              className="danger-button"
                              disabled={busyAction === `cancel-${task.id}`}
                              onClick={() => void handleCancel(task)}
                            >
                              {busyAction === `cancel-${task.id}` ? "取消中" : "确认取消"}
                            </button>
                            <button
                              type="button"
                              className="secondary-button"
                              disabled={busyAction === `cancel-${task.id}`}
                              onClick={() => setConfirmCancelId("")}
                            >
                              返回
                            </button>
                          </div>
                        )}
                        {canRetry && (
                          <button
                            type="button"
                            className="secondary-button"
                            disabled={busyAction === `retry-${task.id}`}
                            onClick={() => void handleRetry(task)}
                          >
                            {busyAction === `retry-${task.id}` ? "重试中" : "重试"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!tasks.length && <p className="empty-state table-empty">暂无任务记录</p>}
        </div>
      </section>
    </main>
  );
}
