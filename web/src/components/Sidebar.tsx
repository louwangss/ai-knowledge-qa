import type { NoteSummary } from "../types";
import { NoteIcon, PlusIcon, SearchIcon } from "./Icons";
import { WorkspaceTabs } from "./WorkspaceTabs";
import type { WorkspaceView } from "./WorkspaceTabs";

interface SidebarProps {
  summaries: NoteSummary[];
  selectedId: number | null;
  search: string;
  isOpen: boolean;
  isCreating: boolean;
  onSearch: (value: string) => void;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onClose: () => void;
  onChangeView: (view: WorkspaceView) => void;
  onLogout: () => void;
}

function noteLabel(concept: string | null) {
  return concept?.trim() || "无标题笔记";
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

export function Sidebar({
  summaries,
  selectedId,
  search,
  isOpen,
  isCreating,
  onSearch,
  onSelect,
  onCreate,
  onClose,
  onChangeView,
  onLogout,
}: SidebarProps) {
  return (
    <>
      <button className={`sidebar-scrim ${isOpen ? "is-visible" : ""}`} aria-label="关闭笔记列表" onClick={onClose} />
      <aside className={`sidebar ${isOpen ? "is-open" : ""}`} aria-label="笔记导航">
        <header className="brand-row">
          <div className="brand-mark" aria-hidden="true">知</div>
          <div>
            <strong>知页</strong>
            <span>Knowledge Notes</span>
          </div>
        </header>

        <WorkspaceTabs active="notes" onChange={onChangeView} />

        <button className="new-note-button" onClick={onCreate} disabled={isCreating}>
          <PlusIcon />
          <span>{isCreating ? "正在创建…" : "新建笔记"}</span>
          <kbd>Ctrl N</kbd>
        </button>

        <label className="search-box">
          <SearchIcon />
          <span className="sr-only">搜索笔记</span>
          <input
            type="search"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="搜索标题"
          />
        </label>

        <div className="list-heading">
          <span>全部笔记</span>
          <span>{summaries.length}</span>
        </div>

        <nav className="note-list" aria-label="笔记列表">
          {summaries.length === 0 ? (
            <div className="sidebar-empty">
              <NoteIcon />
              <p>还没有匹配的笔记</p>
            </div>
          ) : summaries.map((note) => (
            <button
              key={note.id}
              className={`note-list-item ${note.id === selectedId ? "is-active" : ""}`}
              onClick={() => onSelect(note.id)}
              aria-current={note.id === selectedId ? "page" : undefined}
              aria-label={`${noteLabel(note.concept)}，${formatDate(note.updated_at)}`}
            >
              <NoteIcon />
              <span className="note-list-copy">
                <strong>{noteLabel(note.concept)}</strong>
                <small>{formatDate(note.updated_at)}</small>
              </span>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer-actions">
          <button className="logout-button" onClick={onLogout}>退出登录</button>
        </div>
      </aside>
    </>
  );
}
