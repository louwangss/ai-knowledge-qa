import { parseSseStream } from "./chatSse";
import type {
  ApiErrorBody,
  ChatHistoryMessage,
  ChatMode,
  ChatStreamEvent,
  Note,
  NoteSummary,
  SessionSummary,
} from "./types";

const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(status === 409 ? "笔记版本冲突" : "请求失败");
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // 非 JSON 错误仍统一映射为 ApiError。
    }
    const code = typeof body.detail === "object" ? body.detail.code : undefined;
    throw new ApiError(response.status, code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function createWebSession(token: string): Promise<void> {
  await request<void>("/web/session", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function deleteWebSession(): Promise<void> {
  await request<void>("/web/session", { method: "DELETE" });
}

export async function getWebConfig(): Promise<{ user_id: string }> {
  return request("/web/config");
}

export async function getWebSessionStatus(): Promise<{ authenticated: boolean }> {
  return request("/web/session/status");
}

export async function listNoteSummaries(userId: string): Promise<NoteSummary[]> {
  return request(`/notes/summaries?user_id=${encodeURIComponent(userId)}`);
}

export async function getNote(userId: string, noteId: number): Promise<Note> {
  return request(`/notes/${noteId}?user_id=${encodeURIComponent(userId)}`);
}

export async function createNote(userId: string): Promise<Note> {
  return request("/notes", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, concept: "", content: "" }),
  });
}

export async function updateNote(
  userId: string,
  note: Note,
  force = false,
): Promise<Note> {
  return request(`/notes/${note.id}?user_id=${encodeURIComponent(userId)}`, {
    method: "PUT",
    body: JSON.stringify({
      concept: note.concept ?? "",
      content: note.content,
      ...(force ? {} : { version: note.version }),
    }),
  });
}

export async function deleteNote(userId: string, noteId: number): Promise<void> {
  await request(`/notes/${noteId}?user_id=${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}

export async function listSessions(userId: string): Promise<SessionSummary[]> {
  return request(`/sessions?user_id=${encodeURIComponent(userId)}`);
}

export async function createSession(userId: string): Promise<SessionSummary> {
  return request("/sessions", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
}

export async function deleteSession(userId: string, sessionId: string): Promise<void> {
  await request(`/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}

export async function getChatHistory(
  userId: string,
  sessionId: string,
): Promise<ChatHistoryMessage[]> {
  return request(`/chat/history?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}`);
}

export async function* streamChat(
  userId: string,
  sessionId: string,
  message: string,
  mode: ChatMode,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(`${API_ROOT}/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, session_id: sessionId, message, mode }),
    signal,
  });
  if (!response.ok) throw new ApiError(response.status);
  if (!response.body) throw new Error("浏览器未提供流式响应");
  yield* parseSseStream(response.body);
}
