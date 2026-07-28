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
