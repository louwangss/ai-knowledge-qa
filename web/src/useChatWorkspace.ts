import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  createSession,
  deleteSession,
  getChatHistory,
  getChatTurnStatus,
  listSessions,
  streamChat,
} from "./api";
import type {
  ChatDisplayMessage,
  ChatHistoryMessage,
  ChatMode,
  SessionSummary,
} from "./types";

interface PendingTurn {
  fingerprint: string;
  sessionId: string;
  mode: ChatMode;
  clientTurnId: string;
}

const PENDING_TURNS_STORAGE_KEY = "aiqa.pending-chat-turns.v1";
const NEW_SESSION_TURN_SLOT = "__new-session-turn__";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function readPendingTurns(): Map<string, PendingTurn> {
  try {
    const raw = window.sessionStorage.getItem(PENDING_TURNS_STORAGE_KEY);
    if (!raw) return new Map();
    const values = JSON.parse(raw) as unknown;
    if (!Array.isArray(values)) return new Map();
    return new Map(values.flatMap((item): [string, PendingTurn][] => {
      if (
        !item
        || typeof item !== "object"
        || !("fingerprint" in item)
        || !("sessionId" in item)
        || !("mode" in item)
        || !("clientTurnId" in item)
        || typeof item.fingerprint !== "string"
        || typeof item.sessionId !== "string"
        || (item.mode !== "normal" && item.mode !== "deep")
        || typeof item.clientTurnId !== "string"
        || !UUID_PATTERN.test(item.clientTurnId)
      ) return [];
      const pending: PendingTurn = {
        fingerprint: item.fingerprint,
        sessionId: item.sessionId,
        mode: item.mode,
        clientTurnId: item.clientTurnId,
      };
      return [[pending.fingerprint, pending]];
    }));
  } catch {
    return new Map();
  }
}

function writePendingTurns(turns: Map<string, PendingTurn>) {
  try {
    if (turns.size === 0) {
      window.sessionStorage.removeItem(PENDING_TURNS_STORAGE_KEY);
      return;
    }
    window.sessionStorage.setItem(PENDING_TURNS_STORAGE_KEY, JSON.stringify(
      [...turns.values()].map(({ fingerprint, sessionId, mode, clientTurnId }) => ({
        fingerprint,
        sessionId,
        mode,
        clientTurnId,
      })),
    ));
  } catch {
    // sessionStorage 不可用时，当前 Hook 生命周期内的内存映射仍可保证重试。
  }
}

function removePendingTurn(
  turns: Map<string, PendingTurn>,
  fingerprint: string,
  clientTurnId: string,
) {
  if (turns.get(fingerprint)?.clientTurnId !== clientTurnId) return;
  turns.delete(fingerprint);
  writePendingTurns(turns);
}

function fallbackFingerprint(value: string): string {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return `fallback-${(first >>> 0).toString(16)}-${(second >>> 0).toString(16)}-${value.length}`;
}

async function turnFingerprint(sessionId: string, mode: ChatMode, content: string): Promise<string> {
  const value = `${sessionId}\u0000${mode}\u0000${content}`;
  try {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    return fallbackFingerprint(value);
  }
}

function toDisplayMessage(message: ChatHistoryMessage): ChatDisplayMessage {
  return {
    id: `history-${message.id}`,
    role: message.role,
    content: message.content,
    mode: message.mode,
    created_at: message.created_at,
    state: "complete",
    sources: message.sources ?? undefined,
  };
}

function readableError(cause: unknown) {
  if (cause instanceof ApiError && cause.code === "TURN_IN_PROGRESS") {
    return "这次问答仍在服务端处理中，请稍后用同一草稿重试。";
  }
  if (cause instanceof ApiError && cause.code === "TURN_ID_REUSED") {
    return "重试标识与原问题不一致，已为下次发送重置。";
  }
  if (cause instanceof DOMException && cause.name === "AbortError") return "回答已停止";
  return "连接中断，已收到的内容仍保留。请检查后端后重试。";
}

export function useChatWorkspace(userId: string) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, ChatDisplayMessage[]>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [historyFailedId, setHistoryFailedId] = useState<string | null>(null);
  const [historyRetryVersion, setHistoryRetryVersion] = useState(0);
  const [isSessionListLoading, setIsSessionListLoading] = useState(true);
  const [streamingIds, setStreamingIds] = useState<Set<string>>(() => new Set());
  const [isCreating, setIsCreating] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllersRef = useRef(new Map<string, AbortController>());
  const preparingSessionIdsRef = useRef(new Set<string>());
  const loadedSessionIdsRef = useRef(new Set<string>());
  const pendingTurnsRef = useRef(new Map<string, PendingTurn>());
  const pendingTurnsLoadedRef = useRef(false);
  const sessionMutationVersionRef = useRef(0);
  const latestRefreshRequestRef = useRef(0);
  const deletingSessionIdRef = useRef<string | null>(null);
  if (!pendingTurnsLoadedRef.current) {
    pendingTurnsRef.current = readPendingTurns();
    pendingTurnsLoadedRef.current = true;
  }
  const sequenceRef = useRef(0);

  const refreshSessions = useCallback(async (preferredId?: string | null) => {
    const requestVersion = ++latestRefreshRequestRef.current;
    const mutationVersion = sessionMutationVersionRef.current;
    const items = await listSessions(userId);
    if (
      requestVersion !== latestRefreshRequestRef.current
      || mutationVersion !== sessionMutationVersionRef.current
    ) return items;
    const deletingSessionId = deletingSessionIdRef.current;
    const visibleItems = deletingSessionId
      ? items.filter((item) => item.id !== deletingSessionId)
      : items;
    const existingIds = new Set(items.map((item) => item.id));
    let pendingChanged = false;
    for (const [fingerprint, turn] of pendingTurnsRef.current) {
      if (!existingIds.has(turn.sessionId)) {
        pendingTurnsRef.current.delete(fingerprint);
        pendingChanged = true;
      }
    }
    if (pendingChanged) writePendingTurns(pendingTurnsRef.current);
    setSessions(visibleItems);
    setSelectedId((current) => {
      const preferred = preferredId ?? current;
      return preferred && visibleItems.some((item) => item.id === preferred)
        ? preferred
        : visibleItems[0]?.id ?? null;
    });
    return visibleItems;
  }, [userId]);

  useEffect(() => {
    let active = true;
    setError(null);
    setIsSessionListLoading(true);
    void refreshSessions().catch(() => {
      if (active) setError("会话列表加载失败，请确认后端服务可用。");
    }).finally(() => {
      if (active) setIsSessionListLoading(false);
    });
    return () => { active = false; };
  }, [refreshSessions]);

  useEffect(() => {
    if (!selectedId || loadedSessionIdsRef.current.has(selectedId)) return;
    let active = true;
    const targetId = selectedId;
    setLoadingId(targetId);
    setHistoryFailedId((current) => current === targetId ? null : current);
    void getChatHistory(userId, targetId)
      .then((history) => {
        if (!active) return;
        loadedSessionIdsRef.current.add(targetId);
        setHistoryFailedId((current) => current === targetId ? null : current);
        setMessagesBySession((current) => ({
          ...current,
          [targetId]: history.map(toDisplayMessage),
        }));
      })
      .catch(() => {
        if (active) {
          setHistoryFailedId(targetId);
          setError("会话记录加载失败，请稍后重试。");
        }
      })
      .finally(() => {
        setLoadingId((current) => current === targetId ? null : current);
      });
    return () => { active = false; };
  }, [historyRetryVersion, selectedId, userId]);

  useEffect(() => () => {
    controllersRef.current.forEach((controller) => controller.abort());
    controllersRef.current.clear();
  }, []);

  const createNewSession = useCallback(async (signal?: AbortSignal) => {
    if (isCreating || isSessionListLoading || deletingSessionIdRef.current) return null;
    setIsCreating(true);
    setError(null);
    try {
      const session = await createSession(userId, signal);
      if (signal?.aborted) return null;
      sessionMutationVersionRef.current += 1;
      loadedSessionIdsRef.current.add(session.id);
      setSessions((current) => [session, ...current]);
      setMessagesBySession((current) => ({ ...current, [session.id]: [] }));
      setSelectedId(session.id);
      return session.id;
    } catch (cause) {
      if (signal?.aborted || (cause instanceof DOMException && cause.name === "AbortError")) {
        return null;
      }
      setError("新建会话失败，请稍后重试。");
      return null;
    } finally {
      setIsCreating(false);
    }
  }, [isCreating, isSessionListLoading, userId]);

  const removeSelected = useCallback(async (requestedId?: string) => {
    const targetId = requestedId ?? selectedId;
    if (!targetId || deletingSessionIdRef.current) return false;
    if (controllersRef.current.has(targetId)) {
      setError("请先停止当前回答，再删除这个会话。");
      return false;
    }
    const removedSession = sessions.find((session) => session.id === targetId);
    const remaining = sessions.filter((session) => session.id !== targetId);
    const replacementId = remaining[0]?.id ?? null;
    const shouldRestoreSelection = selectedId === targetId;
    deletingSessionIdRef.current = targetId;
    sessionMutationVersionRef.current += 1;
    setIsDeleting(true);
    setSessions((current) => current.filter((session) => session.id !== targetId));
    setSelectedId((current) => current === targetId ? replacementId : current);
    try {
      await deleteSession(userId, targetId);
      // 覆盖删除期间已落地的旧列表响应，并让尚未返回的响应因版本变化而失效。
      sessionMutationVersionRef.current += 1;
      setSessions((current) => current.filter((session) => session.id !== targetId));
      setSelectedId((current) => current === targetId ? remaining[0]?.id ?? null : current);
      loadedSessionIdsRef.current.delete(targetId);
      for (const [fingerprint, turn] of pendingTurnsRef.current) {
        if (turn.sessionId === targetId) pendingTurnsRef.current.delete(fingerprint);
      }
      writePendingTurns(pendingTurnsRef.current);
      setMessagesBySession((current) => {
        const next = { ...current };
        delete next[targetId];
        return next;
      });
      return true;
    } catch {
      sessionMutationVersionRef.current += 1;
      if (removedSession) {
        setSessions((current) => current.some((session) => session.id === targetId)
          ? current
          : [removedSession, ...current]);
      }
      setSelectedId((current) => (
        shouldRestoreSelection && current === replacementId ? targetId : current
      ));
      setError("删除会话失败，列表已恢复。");
      return false;
    } finally {
      if (deletingSessionIdRef.current === targetId) deletingSessionIdRef.current = null;
      setIsDeleting(false);
    }
  }, [selectedId, sessions, userId]);

  const updateMessage = useCallback((sessionId: string, messageId: string, update: (message: ChatDisplayMessage) => ChatDisplayMessage) => {
    setMessagesBySession((current) => ({
      ...current,
      [sessionId]: (current[sessionId] ?? []).map((message) => message.id === messageId ? update(message) : message),
    }));
  }, []);

  const reconcileHistory = useCallback(async (sessionId: string, signal?: AbortSignal) => {
    try {
      const history = await getChatHistory(userId, sessionId, signal);
      if (signal?.aborted) return false;
      loadedSessionIdsRef.current.add(sessionId);
      setHistoryFailedId((current) => current === sessionId ? null : current);
      setMessagesBySession((current) => ({
        ...current,
        [sessionId]: history.map(toDisplayMessage),
      }));
      return true;
    } catch {
      if (signal?.aborted) return false;
      loadedSessionIdsRef.current.delete(sessionId);
      setHistoryFailedId(sessionId);
      setMessagesBySession((current) => {
        const next = { ...current };
        delete next[sessionId];
        return next;
      });
      setError("会话记录同步失败，请重试读取服务端记录。");
      return false;
    }
  }, [userId]);

  const sendMessage = useCallback(async (rawMessage: string, mode: ChatMode) => {
    const content = rawMessage.trim();
    if (!content) return false;
    if (isSessionListLoading) return false;
    let sessionId = selectedId;
    let slotId = sessionId ?? NEW_SESSION_TURN_SLOT;
    if (
      controllersRef.current.has(slotId)
      || preparingSessionIdsRef.current.has(slotId)
      || (sessionId !== null && !loadedSessionIdsRef.current.has(sessionId))
    ) return false;

    const controller = new AbortController();
    preparingSessionIdsRef.current.add(slotId);
    controllersRef.current.set(slotId, controller);
    setStreamingIds((current) => new Set(current).add(slotId));
    const releaseTurnSlot = () => {
      preparingSessionIdsRef.current.delete(slotId);
      controllersRef.current.delete(slotId);
      setStreamingIds((current) => {
        const next = new Set(current);
        next.delete(slotId);
        return next;
      });
    };
    const moveTurnSlot = (nextSessionId: string) => {
      const previousSlotId = slotId;
      slotId = nextSessionId;
      preparingSessionIdsRef.current.delete(previousSlotId);
      controllersRef.current.delete(previousSlotId);
      preparingSessionIdsRef.current.add(nextSessionId);
      controllersRef.current.set(nextSessionId, controller);
      setStreamingIds((current) => {
        const next = new Set(current);
        next.delete(previousSlotId);
        next.add(nextSessionId);
        return next;
      });
    };

    if (!sessionId) {
      sessionId = await createNewSession(controller.signal);
      if (!sessionId || controller.signal.aborted) {
        releaseTurnSlot();
        return false;
      }
      moveTurnSlot(sessionId);
    }
    const fingerprint = await turnFingerprint(sessionId, mode, content);
    if (controller.signal.aborted) {
      releaseTurnSlot();
      return false;
    }
    const pendingTurn = pendingTurnsRef.current.get(fingerprint);
    const isPendingRetry = Boolean(pendingTurn);
    const clientTurnId = isPendingRetry
      ? pendingTurn!.clientTurnId
      : crypto.randomUUID();
    pendingTurnsRef.current.set(fingerprint, {
      fingerprint,
      sessionId,
      mode,
      clientTurnId,
    });
    writePendingTurns(pendingTurnsRef.current);

    if (isPendingRetry) {
      try {
        const turn = await getChatTurnStatus(userId, sessionId, clientTurnId, controller.signal);
        if (controller.signal.aborted) {
          releaseTurnSlot();
          return false;
        }
        if (turn.status === "completed") {
          const reconciled = await reconcileHistory(sessionId, controller.signal);
          const wasAborted = controller.signal.aborted;
          if (reconciled && !wasAborted) {
            removePendingTurn(pendingTurnsRef.current, fingerprint, clientTurnId);
          }
          releaseTurnSlot();
          return reconciled && !wasAborted;
        }
        if (turn.status === "processing") {
          setError("这次问答仍在服务端处理中，请稍后用同一草稿重试。");
          releaseTurnSlot();
          return false;
        }
      } catch {
        if (controller.signal.aborted) {
          releaseTurnSlot();
          return false;
        }
        // 状态读取失败时仍用同一 turn ID 请求，后端 reservation 会阻止重复生成。
      }
    }

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
    const discardOptimisticPair = () => {
      setMessagesBySession((current) => ({
        ...current,
        [sessionId]: (current[sessionId] ?? []).filter(
          (message) => message.id !== userMessage.id && message.id !== assistantId,
        ),
      }));
    };
    setMessagesBySession((current) => ({
      ...current,
      [sessionId]: [...(current[sessionId] ?? []), userMessage, assistantMessage],
    }));
    setError(null);

    let terminalEventReceived = false;
    let streamFailed = false;
    let streamErrorMessage: string | null = null;

    try {
      for await (const event of streamChat(
        userId,
        sessionId,
        content,
        mode,
        clientTurnId,
        controller.signal,
      )) {
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
          streamFailed = true;
          streamErrorMessage = event.content;
          updateMessage(sessionId, assistantId, (message) => ({
            ...message,
            state: "error",
            stage: undefined,
            error: event.content,
          }));
          break;
        } else if (event.type === "done") {
          terminalEventReceived = true;
          updateMessage(sessionId, assistantId, (message) => ({
            ...message,
            state: "complete",
            stage: undefined,
          }));
          break;
        }
      }
      if (!terminalEventReceived) throw new Error("流式响应未正常结束");
      if (streamFailed) {
        setError(streamErrorMessage ?? "回答生成中断，请重试。");
        discardOptimisticPair();
        return false;
      }
      removePendingTurn(pendingTurnsRef.current, fingerprint, clientTurnId);
      void refreshSessions().catch(() => undefined);
      return true;
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "TURN_ID_REUSED") {
        removePendingTurn(pendingTurnsRef.current, fingerprint, clientTurnId);
      }
      if (cause instanceof ApiError && cause.code === "TURN_IN_PROGRESS") {
        try {
          const turn = await getChatTurnStatus(userId, sessionId, clientTurnId, controller.signal);
          if (controller.signal.aborted) return false;
          if (turn.status === "completed") {
            const reconciled = await reconcileHistory(sessionId, controller.signal);
            if (controller.signal.aborted || !reconciled) return false;
            removePendingTurn(pendingTurnsRef.current, fingerprint, clientTurnId);
            setError(null);
            return true;
          }
        } catch {
          // 继续使用原始冲突信息，并保留 turn ID 供下次精确重试。
        }
        if (controller.signal.aborted) return false;
      }
      setError(readableError(cause));
      updateMessage(sessionId, assistantId, (message) => ({
        ...message,
        state: "error",
        stage: undefined,
        error: readableError(cause),
      }));
      discardOptimisticPair();
      return false;
    } finally {
      releaseTurnSlot();
    }
  }, [createNewSession, isSessionListLoading, reconcileHistory, refreshSessions, selectedId, updateMessage, userId]);

  const stopSelected = useCallback(() => {
    controllersRef.current.get(selectedId ?? NEW_SESSION_TURN_SLOT)?.abort();
  }, [selectedId]);

  const retryHistory = useCallback(() => {
    if (!selectedId || historyFailedId !== selectedId) return;
    setError(null);
    setHistoryFailedId(null);
    setHistoryRetryVersion((current) => current + 1);
  }, [historyFailedId, selectedId]);

  const activeMessages = useMemo(
    () => selectedId ? messagesBySession[selectedId] ?? [] : [],
    [messagesBySession, selectedId],
  );

  return {
    sessions,
    selectedId,
    activeMessages,
    isLoading: isSessionListLoading || Boolean(selectedId && historyFailedId !== selectedId && (
      loadingId === selectedId || !loadedSessionIdsRef.current.has(selectedId)
    )),
    isCreating,
    isDeleting,
    isStreaming: streamingIds.has(selectedId ?? NEW_SESSION_TURN_SLOT),
    hasActiveStreams: streamingIds.size > 0,
    canRetryHistory: Boolean(selectedId && historyFailedId === selectedId),
    error,
    selectSession: setSelectedId,
    createNewSession,
    removeSelected,
    sendMessage,
    stopSelected,
    retryHistory,
    clearError: () => setError(null),
  };
}
