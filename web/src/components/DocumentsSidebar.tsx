import { DocumentIcon } from "./Icons";
import { WorkspaceTabs } from "./WorkspaceTabs";
import type { WorkspaceView } from "./WorkspaceTabs";

interface DocumentsSidebarProps {
  documentCount: number;
  chunkCount: number;
  isOpen: boolean;
  onClose: () => void;
  onChangeView: (view: WorkspaceView) => void;
  onLogout: () => void;
}

export function DocumentsSidebar({
  documentCount,
  chunkCount,
  isOpen,
  onClose,
  onChangeView,
  onLogout,
}: DocumentsSidebarProps) {
  return (
    <>
      <button className={`sidebar-scrim ${isOpen ? "is-visible" : ""}`} aria-label="关闭文档导航" onClick={onClose} />
      <aside className={`sidebar ${isOpen ? "is-open" : ""}`} aria-label="文档导航">
        <header className="brand-row">
          <div className="brand-mark" aria-hidden="true">知</div>
          <div><strong>知库</strong><span>Knowledge Base</span></div>
        </header>

        <WorkspaceTabs active="documents" onChange={onChangeView} />

        <section className="document-summary" aria-label="知识库概览">
          <DocumentIcon />
          <p>已收录</p>
          <strong>{documentCount}</strong>
          <span>份文档 · {chunkCount} 个片段</span>
        </section>

        <div className="document-sidebar-copy">
          <p>文档完成解析后会进入向量知识库，并用于普通问答与深度研究。</p>
        </div>

        <div className="sidebar-footer-actions">
          <button className="logout-button" onClick={onLogout}>退出登录</button>
        </div>
      </aside>
    </>
  );
}
