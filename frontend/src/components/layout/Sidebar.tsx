import React, { useState, type FormEvent } from "react";
import type { Project } from "../../api";

export type StudioTab = "learn" | "archive" | "source" | "pipeline";

interface SidebarProps {
  projects: Project[];
  activeProjectId: string;
  onSelectProject: (projectId: string) => void;
  onCreateProject: (name: string) => Promise<void>;
  activeTab: StudioTab;
  onTabChange: (tab: StudioTab) => void;
  health: string;
  hasAnalysis: boolean;
  learningReadyCount?: number;
  totalLearningQuestions?: number;
  chapterCount?: number;
  busy?: string;
}

export function Sidebar({
  projects,
  activeProjectId,
  onSelectProject,
  onCreateProject,
  activeTab,
  onTabChange,
  health,
  hasAnalysis,
  learningReadyCount = 0,
  totalLearningQuestions = 42,
  chapterCount = 0,
  busy,
}: SidebarProps) {
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");

  const activeProject = projects.find((p) => p.id === activeProjectId);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!newProjectName.trim()) return;
    await onCreateProject(newProjectName.trim());
    setNewProjectName("");
    setShowProjectModal(false);
  }

  return (
    <aside className="studio-sidebar">
      {/* Brand Header */}
      <div className="sidebar-brand">
        <div className="brand-logo">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          </svg>
        </div>
        <div className="brand-text">
          <span className="brand-name">AI 小说拆书器</span>
          <span className="brand-subtitle">创作方法与叙事分析</span>
        </div>
      </div>

      {/* Novel Project Selector Card */}
      <div className="project-switcher-wrap">
        <button
          type="button"
          className="project-switcher-btn"
          onClick={() => setShowProjectModal((prev) => !prev)}
        >
          <div className="project-switcher-info">
            <span className="project-switcher-label">当前小说</span>
            <strong className="project-switcher-name">
              {activeProject?.name || "选择或创建小说"}
            </strong>
          </div>
          <span className="project-switcher-arrow">{showProjectModal ? "▲" : "▼"}</span>
        </button>

        {showProjectModal && (
          <div className="project-switcher-dropdown">
            <div className="project-dropdown-header">
              <span>切换小说项目 ({projects.length})</span>
            </div>
            <div className="project-dropdown-list">
              {projects.map((p) => (
                <button
                  type="button"
                  key={p.id}
                  className={`project-dropdown-item ${p.id === activeProjectId ? "selected" : ""}`}
                  onClick={() => {
                    onSelectProject(p.id);
                    setShowProjectModal(false);
                  }}
                >
                  <span className="project-item-name">{p.name}</span>
                  {p.id === activeProjectId && <span className="project-item-active">当前</span>}
                </button>
              ))}
              {!projects.length && <p className="dropdown-empty">暂无项目，请新建</p>}
            </div>
            <form onSubmit={handleCreate} className="project-dropdown-create">
              <input
                type="text"
                placeholder="输入新书名，按回车新建"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                maxLength={200}
              />
              <button
                type="submit"
                className="primary-button small-btn"
                disabled={!newProjectName.trim() || busy === "create-project"}
              >
                新建
              </button>
            </form>
          </div>
        )}
      </div>

      {/* Main Navigation Menu */}
      <nav className="sidebar-nav" aria-label="工作台主导航">
        <button
          type="button"
          className={`nav-item ${activeTab === "learn" ? "active" : ""}`}
          onClick={() => onTabChange("learn")}
        >
          <span className="nav-icon">📖</span>
          <span className="nav-label">学习手册</span>
          {hasAnalysis && (
            <span className="nav-badge highlight">
              {learningReadyCount > 0 ? `${learningReadyCount}/${totalLearningQuestions}` : "可生成"}
            </span>
          )}
        </button>

        <button
          type="button"
          className={`nav-item ${activeTab === "archive" ? "active" : ""}`}
          onClick={() => onTabChange("archive")}
        >
          <span className="nav-icon">📊</span>
          <span className="nav-label">故事档案</span>
          {hasAnalysis && <span className="nav-badge">角色/剧情/设定</span>}
        </button>

        <button
          type="button"
          className={`nav-item ${activeTab === "source" ? "active" : ""}`}
          onClick={() => onTabChange("source")}
        >
          <span className="nav-icon">📑</span>
          <span className="nav-label">原文与章节</span>
          {chapterCount > 0 && <span className="nav-badge">{chapterCount} 章</span>}
        </button>

        <button
          type="button"
          className={`nav-item ${activeTab === "pipeline" ? "active" : ""}`}
          onClick={() => onTabChange("pipeline")}
        >
          <span className="nav-icon">⚡</span>
          <span className="nav-label">分析控制台</span>
          <span className="nav-badge">{hasAnalysis ? "已就绪" : "待分析"}</span>
        </button>

        <div className="nav-divider" />

        <a href="/new-book" className="nav-item external-nav">
          <span className="nav-icon">✍️</span>
          <span className="nav-label">新书共创策划</span>
          <span className="nav-arrow">↗</span>
        </a>
      </nav>

      {/* Sidebar Footer */}
      <footer className="sidebar-footer">
        <div className="system-health-indicator">
          <span className={`health-dot ${health}`} />
          <span className="health-text">
            {health === "ok" ? "服务正常" : health === "offline" ? "服务离线" : "连接中…"}
          </span>
        </div>
        <div className="sidebar-footer-links">
          <a href="/settings" className="footer-link">AI 设置</a>
          <span className="footer-sep">·</span>
          <a href="/debug" className="footer-link">任务调试</a>
        </div>
      </footer>
    </aside>
  );
}
