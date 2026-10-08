import React from "react";
import { Sidebar, type StudioTab } from "./Sidebar";
import { EvidenceDrawer } from "../common/EvidenceDrawer";
import { DiagnosticsModal } from "../DiagnosticsModal";
import type {
  AnalysisCallContent,
  AnalysisRunDiagnostics,
  EvidenceContext,
  Project,
} from "../../api";

interface AppLayoutProps {
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
  // Evidence Drawer
  evidenceContext: EvidenceContext | null;
  onCloseEvidence: () => void;
  onNavigateToChapter?: (chapterId: string) => void;
  // Diagnostics Modal
  showDiagnostics: boolean;
  onCloseDiagnostics: () => void;
  analysisDiagnostics: AnalysisRunDiagnostics | null;
  analysisCallContents: Record<string, AnalysisCallContent>;
  loadingCallContent: string;
  onLoadCallContent: (attemptId: string) => void;
  onRetryComponent?: (component: "overview" | "characters" | "plot" | "relations", label: string) => void;
  children: React.ReactNode;
}

export function AppLayout({
  projects,
  activeProjectId,
  onSelectProject,
  onCreateProject,
  activeTab,
  onTabChange,
  health,
  hasAnalysis,
  learningReadyCount,
  totalLearningQuestions,
  chapterCount,
  busy,
  evidenceContext,
  onCloseEvidence,
  onNavigateToChapter,
  showDiagnostics,
  onCloseDiagnostics,
  analysisDiagnostics,
  analysisCallContents,
  loadingCallContent,
  onLoadCallContent,
  onRetryComponent,
  children,
}: AppLayoutProps) {
  return (
    <div className="studio-root-container">
      {/* 1. Left Fixed Modern Sidebar */}
      <Sidebar
        projects={projects}
        activeProjectId={activeProjectId}
        onSelectProject={onSelectProject}
        onCreateProject={onCreateProject}
        activeTab={activeTab}
        onTabChange={onTabChange}
        health={health}
        hasAnalysis={hasAnalysis}
        learningReadyCount={learningReadyCount}
        totalLearningQuestions={totalLearningQuestions}
        chapterCount={chapterCount}
        busy={busy}
      />

      {/* 2. Main Studio Canvas Area */}
      <main className="studio-canvas">{children}</main>

      {/* 3. Global Slide-Over Evidence Drawer */}
      <EvidenceDrawer
        context={evidenceContext}
        onClose={onCloseEvidence}
        onNavigateToChapter={onNavigateToChapter}
      />

      {/* 4. Global Low-Level Diagnostics Modal */}
      <DiagnosticsModal
        isOpen={showDiagnostics}
        onClose={onCloseDiagnostics}
        diagnostics={analysisDiagnostics}
        callContents={analysisCallContents}
        loadingCallContent={loadingCallContent}
        onLoadCallContent={onLoadCallContent}
        onRetryComponent={onRetryComponent}
        busy={busy}
      />
    </div>
  );
}
