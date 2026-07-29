import { parseSseStream } from "./chatSse";
import type {
  ApiErrorBody,
  ChatHistoryMessage,
  ChatMode,
  ChatStreamEvent,
  ChatTurnStatus,
  KnowledgeDocument,
  Note,
  NoteSummary,
  SessionSummary,
} from "./types";

const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code?: string,
    message?: string,
  ) {
    super(message ?? (status === 409 ? "请求冲突" : "请求失败"));
  }
}

async function responseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody = {};
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // 非 JSON 错误仍统一映射为 ApiError。
  }
  const detail = typeof body.detail === "object" ? body.detail : undefined;
  return new ApiError(response.status, detail?.code, detail?.message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (typeof init?.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_ROOT}${path}`, {
    credentials: "include",
    ...init,
    headers,
  });
  if (!response.ok) {
    throw await responseError(response);
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

export async function listDocuments(userId: string): Promise<KnowledgeDocument[]> {
  return request(`/documents?user_id=${encodeURIComponent(userId)}`);
}

export async function uploadDocument(userId: string, file: File): Promise<KnowledgeDocument> {
  const body = new FormData();
  body.append("user_id", userId);
  body.append("file", file);
  return request("/documents", { method: "POST", body });
}

export async function deleteDocument(userId: string, documentId: string): Promise<void> {
  await request(`/documents/${encodeURIComponent(documentId)}?user_id=${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}

export async function listSessions(userId: string): Promise<SessionSummary[]> {
  return request(`/sessions?user_id=${encodeURIComponent(userId)}`);
}

export async function createSession(userId: string, signal?: AbortSignal): Promise<SessionSummary> {
  return request("/sessions", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
    signal,
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
  signal?: AbortSignal,
): Promise<ChatHistoryMessage[]> {
  return request(
    `/chat/history?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}`,
    { signal },
  );
}

export async function getChatTurnStatus(
  userId: string,
  sessionId: string,
  clientTurnId: string,
  signal?: AbortSignal,
): Promise<ChatTurnStatus> {
  const query = new URLSearchParams({
    user_id: userId,
    session_id: sessionId,
    client_turn_id: clientTurnId,
  });
  return request(`/chat/turn?${query.toString()}`, { signal });
}

export async function* streamChat(
  userId: string,
  sessionId: string,
  message: string,
  mode: ChatMode,
  clientTurnId: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(`${API_ROOT}/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      session_id: sessionId,
      message,
      mode,
      client_turn_id: clientTurnId,
    }),
    signal,
  });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new Error("浏览器未提供流式响应");
  yield* parseSseStream(response.body);
}
