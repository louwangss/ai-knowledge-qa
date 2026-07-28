import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createSession,
  deleteSession,
  getChatHistory,
  listSessions,
  streamChat,
} from "./api";
import type {
  ChatDisplayMessage,
  ChatHistoryMessage,
  ChatMode,
  SessionSummary,
} from "./types";

function toDisplayMessage(message: ChatHistoryMessage): ChatDisplayMessage {
  return {
    id: `history-${message.id}`,
    role: message.role,
    content: message.content,
    mode: message.mode,
    created_at: message.created_at,
    state: "complete",
  };
}

function readableError(cause: unknown) {
  if (cause instanceof DOMException && cause.name === "AbortError") return "回答已停止";
  return "连接中断，已收到的内容仍保留。请检查后端后重试。";
}

export function useChatWorkspace(userId: string) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, ChatDisplayMessage[]>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [streamingIds, setStreamingIds] = useState<Set<string>>(() => new Set());
  const [isCreating, setIsCreating] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllersRef = useRef(new Map<string, AbortController>());
  const sequenceRef = useRef(0);

  const refreshSessions = useCallback(async (preferredId?: string | null) => {
    const items = await listSessions(userId);
    setSessions(items);
    setSelectedId((current) => {
      const preferred = preferredId ?? current;
      return preferred && items.some((item) => item.id === preferred)
        ? preferred
        : items[0]?.id ?? null;
    });
    return items;
  }, [userId]);

  useEffect(() => {
    let active = true;
    setError(null);
    void refreshSessions().catch(() => {
      if (active) setError("会话列表加载失败，请确认后端服务可用。");
    });
    return () => { active = false; };
  }, [refreshSessions]);

  useEffect(() => {
    if (!selectedId || Object.hasOwn(messagesBySession, selectedId)) return;
    let active = true;
    const targetId = selectedId;
    setLoadingId(targetId);
    void getChatHistory(userId, targetId)
      .then((history) => {
        if (!active) return;
        setMessagesBySession((current) => Object.hasOwn(current, targetId) ? current : ({
          ...current,
          [targetId]: history.map(toDisplayMessage),
        }));
      })
      .catch(() => {
        if (active) setError("会话记录加载失败，请稍后重试。");
      })
      .finally(() => {
        setLoadingId((current) => current === targetId ? null : current);
      });
    return () => { active = false; };
  }, [messagesBySession, selectedId, userId]);

  useEffect(() => () => {
    controllersRef.current.forEach((controller) => controller.abort());
    controllersRef.current.clear();
  }, []);

  const createNewSession = useCallback(async () => {
    if (isCreating) return null;
    setIsCreating(true);
    setError(null);
    try {
      const session = await createSession(userId);
      setSessions((current) => [session, ...current]);
      setMessagesBySession((current) => ({ ...current, [session.id]: [] }));
      setSelectedId(session.id);
      return session.id;
    } catch {
      setError("新建会话失败，请稍后重试。");
      return null;
    } finally {
      setIsCreating(false);
    }
  }, [isCreating, userId]);

  const removeSelected = useCallback(async () => {
    if (!selectedId || isDeleting) return false;
    if (controllersRef.current.has(selectedId)) {
      setError("请先停止当前回答，再删除这个会话。");
      return false;
    }
    const targetId = selectedId;
    const previousSessions = sessions;
    const remaining = sessions.filter((session) => session.id !== targetId);
    setIsDeleting(true);
    setSessions(remaining);
    setSelectedId(remaining[0]?.id ?? null);
    try {
      await deleteSession(userId, targetId);
      setMessagesBySession((current) => {
        const next = { ...current };
        delete next[targetId];
        return next;
      });
      return true;
    } catch {
      setSessions(previousSessions);
      setSelectedId(targetId);
      setError("删除会话失败，列表已恢复。");
      return false;
    } finally {
      setIsDeleting(false);
    }
  }, [isDeleting, selectedId, sessions, userId]);

  const updateMessage = useCallback((sessionId: string, messageId: string, update: (message: ChatDisplayMessage) => ChatDisplayMessage) => {
    setMessagesBySession((current) => ({
      ...current,
      [sessionId]: (current[sessionId] ?? []).map((message) => message.id === messageId ? update(message) : message),
    }));
  }, []);

  const sendMessage = useCallback(async (rawMessage: string, mode: ChatMode) => {
    const content = rawMessage.trim();
    if (!content) return false;
    let sessionId = selectedId;
    if (!sessionId) sessionId = await createNewSession();
    if (!sessionId || controllersRef.current.has(sessionId)) return false;

    const sequence = ++sequenceRef.current;
    const createdAt = new Date().toISOString();
    const userMessage: ChatDisplayMessage = {
      id: `local-user-${sequence}`,
      role: "user",
      content,
      mode,
      created_at: createdAt,
      state: "complete",
    };
    const assistantId = `local-assistant-${sequence}`;
    const assistantMessage: ChatDisplayMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      mode,
      created_at: createdAt,
      state: "streaming",
      stage: mode === "deep" ? "正在准备深度研究…" : "正在检索知识库…",
    };
    setMessagesBySession((current) => ({
      ...current,
      [sessionId]: [...(current[sessionId] ?? []), userMessage, assistantMessage],
    }));
    setError(null);

    const controller = new AbortController();
    controllersRef.current.set(sessionId, controller);
    setStreamingIds((current) => new Set(current).add(sessionId));
    let terminalEventReceived = false;

    try {
      for await (const event of streamChat(userId, sessionId, content, mode, controller.signal)) {
        if (event.type === "token") {
          updateMessage(sessionId, assistantId, (message) => ({
            ...message,
            content: message.content + event.content,
            stage: undefined,
          }));
        } else if (event.type === "status") {
          updateMessage(sessionId, assistantId, (message) => ({ ...message, stage: event.content }));
        } else if (event.type === "sources") {
          updateMessage(sessionId, assistantId, (message) => ({ ...message, sources: event.content }));
        } else if (event.type === "error") {
          terminalEventReceived = true;
          updateMessage(sessionId, assistantId, (message) => ({
            ...message,
            state: "error",
            stage: undefined,
            error: event.content,
          }));
        } else if (event.type === "done") {
          terminalEventReceived = true;
          updateMessage(sessionId, assistantId, (message) => ({
            ...message,
            state: "complete",
            stage: undefined,
          }));
        }
      }
      if (!terminalEventReceived) throw new Error("流式响应未正常结束");
      void refreshSessions(sessionId).catch(() => undefined);
      return true;
    } catch (cause) {
      updateMessage(sessionId, assistantId, (message) => ({
        ...message,
        state: "error",
        stage: undefined,
        error: readableError(cause),
      }));
      return false;
    } finally {
      controllersRef.current.delete(sessionId);
      setStreamingIds((current) => {
        const next = new Set(current);
        next.delete(sessionId);
        return next;
      });
    }
  }, [createNewSession, refreshSessions, selectedId, updateMessage, userId]);

  const stopSelected = useCallback(() => {
    if (selectedId) controllersRef.current.get(selectedId)?.abort();
  }, [selectedId]);

  const activeMessages = useMemo(
    () => selectedId ? messagesBySession[selectedId] ?? [] : [],
    [messagesBySession, selectedId],
  );

  return {
    sessions,
    selectedId,
    activeMessages,
    isLoading: Boolean(selectedId && loadingId === selectedId),
    isCreating,
    isDeleting,
    isStreaming: Boolean(selectedId && streamingIds.has(selectedId)),
    error,
    selectSession: setSelectedId,
    createNewSession,
    removeSelected,
    sendMessage,
    stopSelected,
    clearError: () => setError(null),
  };
}
