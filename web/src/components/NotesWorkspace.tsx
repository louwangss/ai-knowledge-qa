import { useEffect, useMemo, useState } from "react";

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
  const workspace = useNotesWorkspace(userId);

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
    <div className="workspace">
      <Sidebar
        summaries={visibleSummaries}
        selectedId={workspace.selectedId}
        search={search}
        isOpen={sidebarOpen}
        isCreating={workspace.isCreating}
        onSearch={setSearch}
        onSelect={(id) => { workspace.selectNote(id); setSidebarOpen(false); }}
        onCreate={() => { void workspace.createNewNote(); setSidebarOpen(false); }}
        onClose={() => setSidebarOpen(false)}
        onChangeView={onChangeView}
        onLogout={onLogout}
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
