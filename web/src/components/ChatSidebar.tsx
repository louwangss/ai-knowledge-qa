import type { SessionSummary } from "../types";
import { ExternalIcon, MessageIcon, PlusIcon } from "./Icons";
import { WorkspaceTabs } from "./WorkspaceTabs";

interface ChatSidebarProps {
  sessions: SessionSummary[];
  selectedId: string | null;
  isOpen: boolean;
  isCreating: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onClose: () => void;
  onChangeView: (view: "chat" | "notes") => void;
}

function sessionLabel(session: SessionSummary) {
  return session.title?.trim() || "新会话";
}

function formatActivity(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

export function ChatSidebar({
  sessions,
  selectedId,
  isOpen,
  isCreating,
  onSelect,
  onCreate,
  onClose,
  onChangeView,
}: ChatSidebarProps) {
  return (
    <>
      <button className={`sidebar-scrim ${isOpen ? "is-visible" : ""}`} aria-label="关闭会话列表" onClick={onClose} />
      <aside className={`sidebar ${isOpen ? "is-open" : ""}`} aria-label="会话导航">
        <header className="brand-row">
          <div className="brand-mark" aria-hidden="true">知</div>
          <div><strong>知问</strong><span>Knowledge QA</span></div>
        </header>

        <WorkspaceTabs active="chat" onChange={onChangeView} />

        <button className="new-note-button" onClick={onCreate} disabled={isCreating}>
          <PlusIcon />
          <span>{isCreating ? "正在创建…" : "新建会话"}</span>
        </button>

        <div className="list-heading chat-list-heading">
          <span>最近会话</span><span>{sessions.length}</span>
        </div>
        <nav className="note-list" aria-label="会话列表">
          {sessions.length === 0 ? (
            <div className="sidebar-empty"><MessageIcon /><p>还没有会话</p></div>
          ) : sessions.map((session) => (
            <button
              key={session.id}
              className={`note-list-item ${session.id === selectedId ? "is-active" : ""}`}
              aria-current={session.id === selectedId ? "page" : undefined}
              aria-label={`${sessionLabel(session)}，${formatActivity(session.last_active)}`}
              onClick={() => onSelect(session.id)}
            >
              <MessageIcon />
              <span className="note-list-copy">
                <strong>{sessionLabel(session)}</strong>
                <small>{formatActivity(session.last_active)}</small>
              </span>
            </button>
          ))}
        </nav>

        <a className="gradio-link" href="http://127.0.0.1:7860" target="_blank" rel="noreferrer">
          <span>打开 Gradio 备用入口</span><ExternalIcon />
        </a>
      </aside>
    </>
  );
}
