export type SaveState = "saved" | "dirty" | "saving" | "offline" | "conflict";

export interface NoteSummary {
  id: number;
  concept: string | null;
  updated_at: string;
}

export interface Note extends NoteSummary {
  user_id: string;
  content: string;
  created_at: string;
  version: string;
  saveState?: SaveState;
}

export interface ApiErrorBody {
  detail?: string | { code?: string; message?: string };
}

export type ChatMode = "normal" | "deep";

export interface SessionSummary {
  id: string;
  user_id: string;
  title: string | null;
  status: string;
  created_at: string;
  last_active: string;
}

export interface ChatHistoryMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  mode: ChatMode | null;
  created_at: string;
}

export interface ChatSource {
  source: string;
  score: number;
}

export type ChatStreamEvent =
  | { type: "token" | "status" | "error"; content: string }
  | { type: "sources"; content: ChatSource[] }
  | { type: "done" };

export interface ChatDisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode: ChatMode | null;
  created_at: string;
  state: "complete" | "streaming" | "error";
  stage?: string;
  sources?: ChatSource[];
  error?: string;
}
