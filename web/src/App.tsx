import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, createWebSession, getWebConfig, getWebSessionStatus } from "./api";
import { Editor } from "./components/Editor";
import { Sidebar } from "./components/Sidebar";
import { useNotesWorkspace } from "./useNotesWorkspace";
import "./styles.css";

type AuthState = "checking" | "required" | "ready";

function takeBootstrapToken() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get("bootstrap");
  if (token) window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  return token;
}

export default function App() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [userId, setUserId] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const authStartedRef = useRef(false);
  const workspace = useNotesWorkspace(userId);

  async function finishAuthentication(token?: string | null) {
    try {
      if (token) {
        await createWebSession(token);
      } else if (!(await getWebSessionStatus()).authenticated) {
        setAuthState("required");
        return;
      }
      const config = await getWebConfig();
      setUserId(config.user_id);
      setAuthState("ready");
      setAuthError(null);
    } catch (cause) {
      setAuthState("required");
      setAuthError(cause instanceof ApiError && cause.status !== 401
        ? "后端暂时不可用，请确认服务已经启动。"
        : null);
    }
  }

  useEffect(() => {
    if (authStartedRef.current) return;
    authStartedRef.current = true;
    void finishAuthentication(takeBootstrapToken());
  }, []);

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

  if (authState === "checking") {
    return <div className="app-loading" role="status"><span className="brand-mark">知</span><p>正在打开笔记工作区…</p></div>;
  }

  if (authState === "required") {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <div className="brand-mark">知</div>
          <p className="eyebrow">LOCAL WORKSPACE</p>
          <h1>连接你的知识库</h1>
          <p>自动启动凭证已失效。输入本机 `.env` 中的 `APP_ACCESS_TOKEN`，它只用于换取 HttpOnly 会话，不会写入浏览器存储。</p>
          <form onSubmit={(event) => { event.preventDefault(); void finishAuthentication(tokenInput); }}>
            <label htmlFor="access-token">访问令牌</label>
            <input
              id="access-token"
              type="password"
              autoComplete="off"
              value={tokenInput}
              onChange={(event) => setTokenInput(event.target.value)}
              required
            />
            {authError && <div className="auth-error" role="alert">{authError}</div>}
            <button type="submit">进入笔记</button>
          </form>
        </section>
      </main>
    );
  }

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
