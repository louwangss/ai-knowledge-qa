import { ApiError } from "./api";
import type { ChatDisplayMessage, ChatHistoryMessage, ChatMode } from "./types";

export interface PendingTurn {
  fingerprint: string;
  sessionId: string;
  mode: ChatMode;
  clientTurnId: string;
}

const PENDING_TURNS_STORAGE_KEY = "aiqa.pending-chat-turns.v1";
export const NEW_SESSION_TURN_SLOT = "__new-session-turn__";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function readPendingTurns(): Map<string, PendingTurn> {
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

export function writePendingTurns(turns: Map<string, PendingTurn>) {
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

export function removePendingTurn(
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

export async function turnFingerprint(
  sessionId: string,
  mode: ChatMode,
  content: string,
): Promise<string> {
  const value = `${sessionId}\u0000${mode}\u0000${content}`;
  try {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    return fallbackFingerprint(value);
  }
}

export function toDisplayMessage(message: ChatHistoryMessage): ChatDisplayMessage {
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

export function readableError(cause: unknown) {
  if (cause instanceof ApiError && cause.code === "TURN_IN_PROGRESS") {
    return "这次问答仍在服务端处理中，请稍后用同一草稿重试。";
  }
  if (cause instanceof ApiError && cause.code === "TURN_ID_REUSED") {
    return "重试标识与原问题不一致，已为下次发送重置。";
  }
  if (cause instanceof DOMException && cause.name === "AbortError") return "回答已停止";
  return "连接中断，已收到的内容仍保留。请检查后端后重试。";
}
