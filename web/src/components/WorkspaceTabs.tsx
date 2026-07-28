import { MessageIcon, NoteIcon } from "./Icons";

interface WorkspaceTabsProps {
  active: "chat" | "notes";
  onChange: (view: "chat" | "notes") => void;
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
    </nav>
  );
}
