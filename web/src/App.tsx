import { useEffect, useRef, useState } from "react";

import { ApiError, createWebSession, getWebConfig, getWebSessionStatus } from "./api";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { NotesWorkspace } from "./components/NotesWorkspace";
import "./styles.css";

type AuthState = "checking" | "required" | "ready";
type ActiveView = "chat" | "notes";

function initialView(): ActiveView {
  return new URLSearchParams(window.location.search).get("view") === "chat" ? "chat" : "notes";
}

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
  const [activeView, setActiveView] = useState<ActiveView>(initialView);
  const authStartedRef = useRef(false);

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

  function changeView(view: ActiveView) {
    const url = new URL(window.location.href);
    if (view === "chat") url.searchParams.set("view", "chat");
    else url.searchParams.delete("view");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
    setActiveView(view);
  }

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

  if (activeView === "chat" && userId) {
    return <ChatWorkspace userId={userId} onChangeView={changeView} />;
  }

  return userId ? <NotesWorkspace userId={userId} onChangeView={changeView} /> : null;
}
