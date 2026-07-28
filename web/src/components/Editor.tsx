import { useEffect, useRef, useState } from "react";

import type { Note, SaveState } from "../types";
import { MenuIcon, TrashIcon } from "./Icons";

interface EditorProps {
  note: Note | null;
  isLoading: boolean;
  onEdit: (field: "concept" | "content", value: string) => void;
  onDelete: () => void;
  onRetry: () => void;
  onLoadServer: () => void;
  onForceSave: () => void;
  onOpenSidebar: () => void;
}

const stateCopy: Record<SaveState, string> = {
  saved: "已保存",
  dirty: "未保存",
  saving: "正在保存",
  offline: "保存失败",
  conflict: "发现版本冲突",
};

function readableTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(date);
}

export function Editor({
  note,
  isLoading,
  onEdit,
  onDelete,
  onRetry,
  onLoadServer,
  onForceSave,
  onOpenSidebar,
}: EditorProps) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (confirmingDelete) cancelRef.current?.focus();
  }, [confirmingDelete]);

  if (isLoading) {
    return (
      <main className="editor-shell" aria-busy="true" aria-label="正在读取笔记">
        <div className="editor-loading"><span /><span /><span /></div>
      </main>
    );
  }

  if (!note) {
    return (
      <main className="editor-shell editor-empty">
        <button className="mobile-menu" onClick={onOpenSidebar} aria-label="打开笔记列表"><MenuIcon /></button>
        <div className="empty-symbol" aria-hidden="true">知</div>
        <h1>留下一点想法</h1>
        <p>从左侧新建一篇笔记，输入后会自动保存。</p>
      </main>
    );
  }

  const saveState = note.saveState ?? "saved";
  return (
    <main className="editor-shell">
      <header className="editor-toolbar">
        <button className="mobile-menu" onClick={onOpenSidebar} aria-label="打开笔记列表"><MenuIcon /></button>
        <div className={`save-indicator state-${saveState}`} role="status" aria-live="polite">
          <span />
          {stateCopy[saveState]}
        </div>
        <button className="icon-button delete-button" onClick={() => setConfirmingDelete(true)} aria-label="删除当前笔记">
          <TrashIcon />
        </button>
      </header>

      {saveState === "offline" && (
        <div className="notice error-notice" role="alert">
          <span>网络或后端暂时不可用，本地文字仍保留在当前页面。</span>
          <button onClick={onRetry}>重试保存</button>
        </div>
      )}
      {saveState === "conflict" && (
        <div className="notice conflict-notice" role="alert">
          <span>服务器上有更新，已暂停自动保存以免覆盖内容。</span>
          <div>
            <button onClick={onLoadServer}>加载服务器版本</button>
            <button className="notice-primary" onClick={onForceSave}>保留本地版本</button>
          </div>
        </div>
      )}

      <article className="editor-page">
        <input
          className="title-input"
          aria-label="笔记标题"
          value={note.concept ?? ""}
          maxLength={100}
          placeholder="无标题"
          onChange={(event) => onEdit("concept", event.target.value)}
        />
        <div className="note-meta">编辑于 {readableTime(note.updated_at)}</div>
        <textarea
          className="content-input"
          aria-label="笔记正文"
          value={note.content}
          maxLength={65535}
          placeholder="从这里开始记录…"
          spellCheck
          onChange={(event) => onEdit("content", event.target.value)}
        />
        <footer className="editor-footer">
          <span>{note.content.length.toLocaleString("zh-CN")} 字符</span>
          <span>Ctrl S 立即保存</span>
        </footer>
      </article>

      {confirmingDelete && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={() => setConfirmingDelete(false)}>
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="dialog-icon"><TrashIcon /></div>
            <h2 id="delete-title">删除这篇笔记？</h2>
            <p>删除后无法从应用内恢复，原笔记会立即从列表移除。</p>
            <div className="dialog-actions">
              <button ref={cancelRef} onClick={() => setConfirmingDelete(false)}>取消</button>
              <button className="danger-action" onClick={() => { setConfirmingDelete(false); onDelete(); }}>确认删除</button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
