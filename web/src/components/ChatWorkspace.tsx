import { useState } from "react";

import { useChatWorkspace } from "../useChatWorkspace";
import { ChatPanel } from "./ChatPanel";
import { ChatSidebar } from "./ChatSidebar";

interface ChatWorkspaceProps {
  userId: string;
  onChangeView: (view: "chat" | "notes") => void;
  onLogout: () => void;
}

export function ChatWorkspace({ userId, onChangeView, onLogout }: ChatWorkspaceProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const workspace = useChatWorkspace(userId);

  return (
    <div className="workspace chat-workspace">
      <ChatSidebar
        sessions={workspace.sessions}
        selectedId={workspace.selectedId}
        isOpen={sidebarOpen}
        isCreating={workspace.isCreating}
        onSelect={(id) => { workspace.selectSession(id); setSidebarOpen(false); }}
        onCreate={() => { void workspace.createNewSession(); setSidebarOpen(false); }}
        onClose={() => setSidebarOpen(false)}
        onChangeView={onChangeView}
        onLogout={onLogout}
      />
      <ChatPanel
        messages={workspace.activeMessages}
        hasSession={Boolean(workspace.selectedId)}
        isLoading={workspace.isLoading}
        isStreaming={workspace.isStreaming}
        isDeleting={workspace.isDeleting}
        onSend={workspace.sendMessage}
        onStop={workspace.stopSelected}
        onDelete={workspace.removeSelected}
        onCreate={() => void workspace.createNewSession()}
        onOpenSidebar={() => setSidebarOpen(true)}
      />
      {workspace.error && (
        <div className="toast" role="alert"><span>{workspace.error}</span><button onClick={workspace.clearError} aria-label="关闭提示">×</button></div>
      )}
    </div>
  );
}
