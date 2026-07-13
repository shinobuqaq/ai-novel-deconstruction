import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Project, Task } from "./api";

export default function App() {
  const [health, setHealth] = useState("checking");
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projectName, setProjectName] = useState("测试小说");
  const [message, setMessage] = useState("M0 fake provider test");
  const [selectedProject, setSelectedProject] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setError("");
      const [healthResult, projectResult, taskResult] = await Promise.all([
        api.health(),
        api.projects(),
        api.tasks(),
      ]);
      setHealth(healthResult.status);
      setProjects(projectResult);
      setTasks(taskResult);
      if (!selectedProject && projectResult[0]) {
        setSelectedProject(projectResult[0].id);
      }
    } catch (err) {
      setHealth("offline");
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [selectedProject]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function handleCreateProject(event: FormEvent) {
    event.preventDefault();
    const project = await api.createProject(projectName);
    setSelectedProject(project.id);
    await refresh();
  }

  async function handleCreateTask(event: FormEvent) {
    event.preventDefault();
    if (!selectedProject) return;
    await api.createEchoTask(selectedProject, message);
    await refresh();
  }

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">M0 ENGINEERING SCAFFOLD</p>
          <h1>AI 自动小说拆书分析器</h1>
          <p>最小闭环：Project → Task → Worker → Artifact</p>
        </div>
        <div className={`status ${health}`}>API: {health}</div>
      </header>

      {error && <div className="error">{error}</div>}

      <section className="grid">
        <article className="card">
          <h2>创建项目</h2>
          <form onSubmit={handleCreateProject}>
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
            <button type="submit">创建</button>
          </form>
          <ul>
            {projects.map((project) => (
              <li key={project.id}>
                <button className="link" onClick={() => setSelectedProject(project.id)}>
                  {project.name}
                </button>
                <small>{project.id}</small>
              </li>
            ))}
          </ul>
        </article>

        <article className="card">
          <h2>创建 Fake Task</h2>
          <form onSubmit={handleCreateTask}>
            <select value={selectedProject} onChange={(event) => setSelectedProject(event.target.value)}>
              <option value="">选择项目</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
            <textarea value={message} onChange={(event) => setMessage(event.target.value)} />
            <button type="submit" disabled={!selectedProject}>提交任务</button>
          </form>
        </article>
      </section>

      <section className="card tasks">
        <div className="section-title">
          <h2>Run Center</h2>
          <button onClick={() => void refresh()}>刷新</button>
        </div>
        <table>
          <thead>
            <tr><th>状态</th><th>类型</th><th>尝试</th><th>任务</th><th>Artifact</th></tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.id}>
                <td><span className={`pill ${task.status.toLowerCase()}`}>{task.status}</span></td>
                <td>{task.kind}</td>
                <td>{task.attempts}</td>
                <td><code>{task.id}</code>{task.error_message && <div className="error-text">{task.error_message}</div>}</td>
                <td><code>{task.result_artifact_id ?? "—"}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
