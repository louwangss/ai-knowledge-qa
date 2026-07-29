import { useMemo, useState } from "react";

import { useDocumentsWorkspace } from "../useDocumentsWorkspace";
import { DocumentsPanel } from "./DocumentsPanel";
import { DocumentsSidebar } from "./DocumentsSidebar";
import type { WorkspaceView } from "./WorkspaceTabs";

interface DocumentsWorkspaceProps {
  userId: string;
  onChangeView: (view: WorkspaceView) => void;
  onLogout: () => void;
}

export function DocumentsWorkspace({ userId, onChangeView, onLogout }: DocumentsWorkspaceProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const workspace = useDocumentsWorkspace(userId);
  const chunkCount = useMemo(
    () => workspace.documents.reduce((total, document) => total + (document.chunk_count ?? 0), 0),
    [workspace.documents],
  );

  return (
    <div className="workspace document-workspace">
      <DocumentsSidebar
        documentCount={workspace.documents.length}
        chunkCount={chunkCount}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onChangeView={onChangeView}
        onLogout={onLogout}
      />
      <DocumentsPanel
        documents={workspace.documents}
        isLoading={workspace.isLoading}
        isUploading={workspace.isUploading}
        onUpload={workspace.uploadDocument}
        onDelete={workspace.removeDocument}
        onOpenSidebar={() => setSidebarOpen(true)}
      />
      {workspace.error && (
        <div className="toast" role="alert"><span>{workspace.error}</span><button onClick={workspace.clearError} aria-label="关闭提示">×</button></div>
      )}
    </div>
  );
}
