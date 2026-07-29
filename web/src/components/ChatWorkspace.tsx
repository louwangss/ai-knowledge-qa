import { useState } from "react";

import { useChatWorkspace } from "../useChatWorkspace";
import { ChatPanel } from "./ChatPanel";
import { ChatSidebar } from "./ChatSidebar";
import type { WorkspaceView } from "./WorkspaceTabs";

interface ChatWorkspaceProps {
  userId: string;
  onChangeView: (view: WorkspaceView) => void;
  onLogout: () => void;
}

export function ChatWorkspace({ userId, onChangeView, onLogout }: ChatWorkspaceProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [leaveError, setLeaveError] = useState<string | null>(null);
  const workspace = useChatWorkspace(userId);

  function leaveWhenIdle(action: () => void) {
    if (workspace.hasActiveStreams) {
      setLeaveError("仍有回答正在生成。请回到对应会话停止回答，或等待完成后再离开。");
      return;
    }
    setLeaveError(null);
    action();
  }

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
        onChangeView={(view) => leaveWhenIdle(() => onChangeView(view))}
        onLogout={() => leaveWhenIdle(onLogout)}
      />
      <ChatPanel
        messages={workspace.activeMessages}
        sessionId={workspace.selectedId}
        isLoading={workspace.isLoading}
        canRetryHistory={workspace.canRetryHistory}
        isStreaming={workspace.isStreaming}
        isDeleting={workspace.isDeleting}
        onSend={workspace.sendMessage}
        onStop={workspace.stopSelected}
        onDelete={workspace.removeSelected}
        onCreate={() => void workspace.createNewSession()}
        onRetryHistory={workspace.retryHistory}
        onOpenSidebar={() => setSidebarOpen(true)}
      />
      {(leaveError || workspace.error) && (
        <div className="toast" role="alert">
          <span>{leaveError || workspace.error}</span>
          {!leaveError && workspace.canRetryHistory && <button onClick={workspace.retryHistory}>重试</button>}
          <button onClick={() => { setLeaveError(null); workspace.clearError(); }} aria-label="关闭提示">×</button>
        </div>
      )}
    </div>
  );
}
