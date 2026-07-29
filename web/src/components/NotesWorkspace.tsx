import { useEffect, useMemo, useRef, useState } from "react";

import { useNotesWorkspace } from "../useNotesWorkspace";
import { Editor } from "./Editor";
import { Sidebar } from "./Sidebar";
import type { WorkspaceView } from "./WorkspaceTabs";

interface NotesWorkspaceProps {
  userId: string;
  onChangeView: (view: WorkspaceView) => void;
  onLogout: () => void;
}

export function NotesWorkspace({ userId, onChangeView, onLogout }: NotesWorkspaceProps) {
  const [search, setSearch] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const isLeavingRef = useRef(false);
  const pendingLeaveActionRef = useRef<(() => void) | null>(null);
  const workspace = useNotesWorkspace(userId);

  const leaveAfterSaving = (action: () => void) => {
    pendingLeaveActionRef.current = action;
    if (isLeavingRef.current) return;
    isLeavingRef.current = true;
    void workspace.flushPendingSaves()
      .then((saved) => {
        const pendingAction = pendingLeaveActionRef.current;
        pendingLeaveActionRef.current = null;
        if (saved) pendingAction?.();
      })
      .finally(() => {
        isLeavingRef.current = false;
      });
  };

  const handleChangeView = (view: WorkspaceView) => {
    leaveAfterSaving(() => onChangeView(view));
  };

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === "n") {
        event.preventDefault();
        void workspace.createNewNote();
      }
      if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        workspace.saveNow();
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [workspace]);

  const visibleSummaries = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("zh-CN");
    if (!query) return workspace.summaries;
    return workspace.summaries.filter((note) => (note.concept || "无标题笔记")
      .toLocaleLowerCase("zh-CN").includes(query));
  }, [search, workspace.summaries]);

  return (
    <div className="workspace" aria-busy={workspace.isFlushing}>
      <Sidebar
        summaries={visibleSummaries}
        selectedId={workspace.selectedId}
        search={search}
        isOpen={sidebarOpen}
        isCreating={workspace.isCreating || workspace.isFlushing}
        onSearch={setSearch}
        onSelect={(id) => { workspace.selectNote(id); setSidebarOpen(false); }}
        onCreate={() => { void workspace.createNewNote(); setSidebarOpen(false); }}
        onClose={() => setSidebarOpen(false)}
        onChangeView={handleChangeView}
        onLogout={() => leaveAfterSaving(onLogout)}
      />
      <Editor
        note={workspace.activeNote}
        isLoading={workspace.isLoading}
        onEdit={workspace.editSelected}
        onDelete={() => void workspace.removeSelected()}
        onRetry={workspace.retrySave}
        onLoadServer={workspace.loadServerVersion}
        onForceSave={workspace.forceSave}
        onOpenSidebar={() => setSidebarOpen(true)}
      />
      {workspace.error && (
        <div className="toast" role="alert">
          <span>{workspace.error}</span>
          <button onClick={workspace.clearError} aria-label="关闭提示">×</button>
        </div>
      )}
    </div>
  );
}
