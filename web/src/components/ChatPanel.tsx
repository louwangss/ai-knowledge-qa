import { useEffect, useRef, useState } from "react";

import type { ChatDisplayMessage, ChatMode } from "../types";
import { MenuIcon, SendIcon, SparkIcon, StopIcon, TrashIcon } from "./Icons";
import { MarkdownText } from "./MarkdownText";

interface ChatPanelProps {
  messages: ChatDisplayMessage[];
  sessionId: string | null;
  isLoading: boolean;
  canRetryHistory?: boolean;
  isStreaming: boolean;
  isDeleting: boolean;
  onSend: (message: string, mode: ChatMode) => Promise<boolean>;
  onStop: () => void;
  onDelete: (sessionId: string) => Promise<boolean>;
  onCreate: () => void;
  onRetryHistory?: () => void;
  onOpenSidebar: () => void;
}

function Message({ message }: { message: ChatDisplayMessage }) {
  return (
    <article className={`chat-message ${message.role}`} aria-label={message.role === "user" ? "你的问题" : "AI 回答"}>
      <div className="message-avatar" aria-hidden="true">{message.role === "user" ? "你" : "知"}</div>
      <div className="message-body">
        <div className="message-meta">{message.role === "user" ? "你" : message.mode === "deep" ? "深度研究" : "知识助手"}</div>
        {message.stage && <div className="message-stage" role="status"><span />{message.stage}</div>}
        {message.content && <div className="message-content"><MarkdownText content={message.content} /></div>}
        {message.state === "streaming" && !message.content && !message.stage && (
          <div className="message-stage" role="status"><span />正在生成…</div>
        )}
        {message.error && <div className="message-error" role="alert">{message.error}</div>}
        {message.sources && message.sources.length > 0 && (
          <section className="message-sources" aria-label="回答来源">
            <h3>参考来源</h3>
            <ul>
              {message.sources.map((source, index) => (
                <li key={`${source.source}-${index}`}><span>{index + 1}</span>{source.source}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </article>
  );
}

export function ChatPanel({
  messages,
  sessionId,
  isLoading,
  canRetryHistory = false,
  isStreaming,
  isDeleting,
  onSend,
  onStop,
  onDelete,
  onCreate,
  onRetryHistory,
  onOpenSidebar,
}: ChatPanelProps) {
  const hasSession = Boolean(sessionId);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<ChatMode>("normal");
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const deleteTriggerRef = useRef<HTMLButtonElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const restoreDeleteFocusRef = useRef(false);
  const wasStreamingRef = useRef(isStreaming);

  useEffect(() => {
    const element = endRef.current;
    if (element && typeof element.scrollIntoView === "function") element.scrollIntoView({ block: "end" });
  }, [messages]);

  useEffect(() => {
    if (deleteTargetId) {
      cancelRef.current?.focus();
      return;
    }
    if (restoreDeleteFocusRef.current) {
      restoreDeleteFocusRef.current = false;
      deleteTriggerRef.current?.focus({ preventScroll: true });
    }
  }, [deleteTargetId]);

  useEffect(() => {
    const answerFinished = wasStreamingRef.current && !isStreaming;
    wasStreamingRef.current = isStreaming;
    if (!answerFinished || isLoading || canRetryHistory || deleteTargetId) return;

    const activeElement = document.activeElement;
    const focusIsUnclaimed = !activeElement
      || activeElement === document.body
      || activeElement === document.documentElement;
    if (focusIsUnclaimed || composerRef.current?.contains(activeElement)) {
      composerInputRef.current?.focus({ preventScroll: true });
    }
  }, [canRetryHistory, deleteTargetId, isLoading, isStreaming]);

  function closeDeleteDialog() {
    restoreDeleteFocusRef.current = true;
    setDeleteTargetId(null);
  }

  function submit() {
    const content = draft.trim();
    if (!content || isLoading || canRetryHistory || isStreaming) return;
    setDraft("");
    void onSend(content, mode).then((accepted) => {
      if (!accepted) setDraft((current) => current || content);
    });
  }

  return (
    <main className="chat-shell">
      <header className="chat-toolbar">
        <button className="mobile-menu" onClick={onOpenSidebar} aria-label="打开会话列表"><MenuIcon /></button>
        <div className="mode-switch" role="group" aria-label="问答模式">
          <button aria-pressed={mode === "normal"} onClick={() => setMode("normal")}>普通问答</button>
          <button aria-pressed={mode === "deep"} onClick={() => setMode("deep")}><SparkIcon />深度研究</button>
        </div>
        {hasSession && (
          <button ref={deleteTriggerRef} className="icon-button delete-button" onClick={() => setDeleteTargetId(sessionId)} disabled={isStreaming || isDeleting} aria-label="删除当前会话">
            <TrashIcon />
          </button>
        )}
      </header>

      <section className="chat-scroll" aria-label="对话内容">
        {canRetryHistory ? (
          <div className="chat-empty" role="alert">
            <div className="empty-symbol" aria-hidden="true">!</div>
            <h1>会话记录暂时无法读取</h1>
            <p>重新读取成功前不会发送新问题，避免覆盖服务端记录。</p>
            <button className="empty-action" onClick={onRetryHistory}>重试读取</button>
          </div>
        ) : isLoading ? (
          <div className="chat-loading" aria-busy="true" aria-label="正在读取会话"><span /><span /><span /></div>
        ) : messages.length > 0 ? (
          <div className="message-list" role="log" aria-live="polite">
            {messages.map((message) => <Message key={message.id} message={message} />)}
            <div ref={endRef} />
          </div>
        ) : (
          <div className="chat-empty">
            <div className="empty-symbol" aria-hidden="true">问</div>
            <p className="eyebrow">YOUR KNOWLEDGE, GROUNDED</p>
            <h1>{hasSession ? "从你的资料里找到答案" : "创建一段新的对话"}</h1>
            <p>{hasSession ? "普通问答适合快速查询；深度研究会拆解问题、检索并整合答案。" : "会话会保存到本机数据库，稍后可以继续。"}</p>
            {!hasSession && <button className="empty-action" onClick={onCreate}>新建会话</button>}
          </div>
        )}
      </section>

      <footer className="composer-wrap">
        <div ref={composerRef} className="composer">
          <textarea
            ref={composerInputRef}
            aria-label="输入问题"
            value={draft}
            rows={1}
            placeholder={mode === "deep" ? "输入一个需要深入研究的问题…" : "询问你的知识库…"}
            disabled={isLoading || canRetryHistory || isStreaming}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
          />
          {isStreaming ? (
            <button className="send-button stop" onClick={onStop} aria-label="停止回答"><StopIcon /></button>
          ) : (
            <button className="send-button" onClick={submit} disabled={isLoading || canRetryHistory || !draft.trim()} aria-label="发送问题"><SendIcon /></button>
          )}
        </div>
        <div className="composer-hint">Enter 发送 · Shift Enter 换行 · AI 回答可能有误，请核对来源</div>
      </footer>

      {deleteTargetId && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={closeDeleteDialog}>
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-session-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="dialog-icon"><TrashIcon /></div>
            <h2 id="delete-session-title">删除这个会话？</h2>
            <p>该会话中的消息、摘要和短期记忆都会被删除，操作无法恢复。</p>
            <div className="dialog-actions">
              <button ref={cancelRef} onClick={closeDeleteDialog}>取消</button>
              <button className="danger-action" disabled={isDeleting} onClick={() => void onDelete(deleteTargetId).then((deleted) => {
                if (deleted) {
                  restoreDeleteFocusRef.current = false;
                  setDeleteTargetId(null);
                }
              })}>
                {isDeleting ? "正在删除…" : "确认删除"}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
