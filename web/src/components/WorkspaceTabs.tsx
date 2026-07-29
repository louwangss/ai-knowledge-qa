import { DocumentIcon, MessageIcon, NoteIcon } from "./Icons";

export type WorkspaceView = "chat" | "notes" | "documents";

interface WorkspaceTabsProps {
  active: WorkspaceView;
  onChange: (view: WorkspaceView) => void;
}

export function WorkspaceTabs({ active, onChange }: WorkspaceTabsProps) {
  return (
    <nav className="workspace-tabs" aria-label="工作区">
      <button
        className={active === "chat" ? "is-active" : ""}
        aria-current={active === "chat" ? "page" : undefined}
        onClick={() => onChange("chat")}
      >
        <MessageIcon />
        问答
      </button>
      <button
        className={active === "notes" ? "is-active" : ""}
        aria-current={active === "notes" ? "page" : undefined}
        onClick={() => onChange("notes")}
      >
        <NoteIcon />
        笔记
      </button>
      <button
        className={active === "documents" ? "is-active" : ""}
        aria-current={active === "documents" ? "page" : undefined}
        onClick={() => onChange("documents")}
      >
        <DocumentIcon />
        文档
      </button>
    </nav>
  );
}
