import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  createNote as createNoteRequest,
  deleteNote as deleteNoteRequest,
  getNote,
  listNoteSummaries,
  updateNote,
} from "./api";
import type { Note, NoteSummary } from "./types";

const AUTOSAVE_DELAY_MS = 600;

type NoteCache = Record<number, Note>;

export function useNotesWorkspace(userId: string | null) {
  const [summaries, setSummaries] = useState<NoteSummary[]>([]);
  const [notes, setNotes] = useState<NoteCache>({});
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notesRef = useRef<NoteCache>({});
  const timersRef = useRef(new Map<number, number>());
  const inFlightRef = useRef(new Set<number>());
  const pendingRef = useRef(new Set<number>());
  const tempIdRef = useRef(-1);

  const updateCache = useCallback((updater: (current: NoteCache) => NoteCache) => {
    const next = updater(notesRef.current);
    notesRef.current = next;
    setNotes(next);
  }, []);

  const scheduleSave = useCallback((noteId: number) => {
    const previous = timersRef.current.get(noteId);
    if (previous) window.clearTimeout(previous);
    const timer = window.setTimeout(() => {
      timersRef.current.delete(noteId);
      void persist(noteId);
    }, AUTOSAVE_DELAY_MS);
    timersRef.current.set(noteId, timer);
  // persist 通过运行时闭包读取最新 notesRef；避免输入时重建定时器依赖。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function persist(noteId: number, force = false) {
    if (!userId || noteId < 0) return;
    if (inFlightRef.current.has(noteId)) {
      pendingRef.current.add(noteId);
      return;
    }
    const snapshot = notesRef.current[noteId];
    if (!snapshot || (!force && snapshot.saveState !== "dirty" && snapshot.saveState !== "offline")) return;

    inFlightRef.current.add(noteId);
    updateCache((current) => current[noteId]
      ? { ...current, [noteId]: { ...current[noteId], saveState: "saving" } }
      : current);
    try {
      const saved = await updateNote(userId, snapshot, force);
      updateCache((current) => {
        const latest = current[noteId];
        if (!latest) return current;
        const unchanged = latest.concept === snapshot.concept && latest.content === snapshot.content;
        return {
          ...current,
          [noteId]: unchanged
            ? { ...saved, saveState: "saved" }
            : { ...latest, version: saved.version, updated_at: saved.updated_at, saveState: "dirty" },
        };
      });
      setSummaries((current) => current.map((item) => item.id === noteId
        ? { ...item, concept: snapshot.concept, updated_at: saved.updated_at }
        : item));
    } catch (cause) {
      const isConflict = cause instanceof ApiError && cause.status === 409;
      updateCache((current) => current[noteId]
        ? { ...current, [noteId]: { ...current[noteId], saveState: isConflict ? "conflict" : "offline" } }
        : current);
    } finally {
      inFlightRef.current.delete(noteId);
      const latest = notesRef.current[noteId];
      const needsAnotherSave = pendingRef.current.delete(noteId)
        || latest?.saveState === "dirty";
      if (needsAnotherSave) scheduleSave(noteId);
    }
  }

  const loadNote = useCallback(async (noteId: number) => {
    if (!userId || notesRef.current[noteId]) return;
    setLoadingId(noteId);
    try {
      const loaded = await getNote(userId, noteId);
      updateCache((current) => ({ ...current, [noteId]: { ...loaded, saveState: "saved" } }));
    } catch {
      setError("这篇笔记读取失败，请稍后重试。");
    } finally {
      setLoadingId((current) => current === noteId ? null : current);
    }
  }, [updateCache, userId]);

  useEffect(() => {
    if (!userId) return;
    const activeUserId = userId;
    let cancelled = false;
    async function load() {
      setError(null);
      try {
        const items = await listNoteSummaries(activeUserId);
        if (cancelled) return;
        setSummaries(items);
        const firstId = items[0]?.id ?? null;
        setSelectedId(firstId);
        if (firstId !== null) await loadNote(firstId);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError && cause.status === 401
          ? "会话已失效，请刷新页面重新认证。"
          : "笔记列表读取失败，请确认后端正在运行。");
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [loadNote, userId]);

  useEffect(() => () => {
    timersRef.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  useEffect(() => {
    const warnWhenDirty = (event: BeforeUnloadEvent) => {
      if (Object.values(notesRef.current).some((note) => note.saveState && note.saveState !== "saved")) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", warnWhenDirty);
    return () => window.removeEventListener("beforeunload", warnWhenDirty);
  }, []);

  const selectNote = useCallback((noteId: number) => {
    setSelectedId(noteId);
    void loadNote(noteId);
  }, [loadNote]);

  const editSelected = useCallback((field: "concept" | "content", value: string) => {
    if (selectedId === null) return;
    if (field === "content" && new TextEncoder().encode(value).length > 65_535) {
      setError("正文已达到 65,535 字节上限，请精简后继续输入。");
      return;
    }
    updateCache((current) => {
      const note = current[selectedId];
      if (!note) return current;
      return { ...current, [selectedId]: { ...note, [field]: value, saveState: "dirty" } };
    });
    if (field === "concept") {
      setSummaries((current) => current.map((item) => item.id === selectedId
        ? { ...item, concept: value }
        : item));
    }
    if (selectedId >= 0) scheduleSave(selectedId);
  }, [scheduleSave, selectedId, updateCache]);

  const createTemporaryOnServer = useCallback(async (tempId: number) => {
    if (!userId) return;
    const pendingDraft = notesRef.current[tempId];
    if (!pendingDraft) return;
    setIsCreating(true);
    setError(null);
    updateCache((current) => current[tempId]
      ? { ...current, [tempId]: { ...current[tempId], saveState: "saving" } }
      : current);
    try {
      const created = await createNoteRequest(userId);
      const draft = notesRef.current[tempId] ?? pendingDraft;
      const hasDraft = Boolean(draft.concept || draft.content);
      updateCache((current) => {
        const { [tempId]: _removed, ...rest } = current;
        return {
          ...rest,
          [created.id]: hasDraft
            ? { ...created, concept: draft.concept, content: draft.content, saveState: "dirty" }
            : { ...created, saveState: "saved" },
        };
      });
      setSummaries((current) => current.map((item) => item.id === tempId
        ? { id: created.id, concept: draft.concept, updated_at: created.updated_at }
        : item));
      setSelectedId((current) => current === tempId ? created.id : current);
      if (hasDraft) scheduleSave(created.id);
    } catch {
      setError("新建失败，当前草稿尚未保存到服务器。");
      updateCache((current) => current[tempId]
        ? { ...current, [tempId]: { ...current[tempId], saveState: "offline" } }
        : current);
    } finally {
      setIsCreating(false);
    }
  }, [scheduleSave, updateCache, userId]);

  const createNewNote = useCallback(async () => {
    if (!userId || isCreating) return;
    const tempId = tempIdRef.current--;
    const now = new Date().toISOString();
    const optimistic: Note = {
      id: tempId,
      user_id: userId,
      concept: "",
      content: "",
      created_at: now,
      updated_at: now,
      version: "",
      saveState: "saving",
    };
    updateCache((current) => ({ ...current, [tempId]: optimistic }));
    setSummaries((current) => [optimistic, ...current]);
    setSelectedId(tempId);
    await createTemporaryOnServer(tempId);
  }, [createTemporaryOnServer, isCreating, updateCache, userId]);

  const removeSelected = useCallback(async () => {
    if (!userId || selectedId === null || selectedId < 0) return;
    const removedId = selectedId;
    const previousSummaries = summaries;
    const previousCache = notesRef.current;
    const timer = timersRef.current.get(removedId);
    if (timer) window.clearTimeout(timer);
    timersRef.current.delete(removedId);
    const remaining = summaries.filter((item) => item.id !== removedId);
    const nextId = remaining[0]?.id ?? null;
    setSummaries(remaining);
    updateCache((current) => {
      const { [removedId]: _removed, ...rest } = current;
      return rest;
    });
    setSelectedId(nextId);
    if (nextId !== null) void loadNote(nextId);
    try {
      await deleteNoteRequest(userId, removedId);
    } catch {
      setSummaries(previousSummaries);
      updateCache(() => previousCache);
      setSelectedId(removedId);
      setError("删除失败，笔记已恢复到列表中。");
    }
  }, [loadNote, selectedId, summaries, updateCache, userId]);

  const loadServerVersion = useCallback(async () => {
    if (!userId || selectedId === null || selectedId < 0) return;
    try {
      const latest = await getNote(userId, selectedId);
      updateCache((current) => ({ ...current, [selectedId]: { ...latest, saveState: "saved" } }));
      setSummaries((current) => current.map((item) => item.id === selectedId
        ? { ...item, concept: latest.concept, updated_at: latest.updated_at }
        : item));
    } catch {
      setError("服务器版本读取失败。");
    }
  }, [selectedId, updateCache, userId]);

  const activeNote = selectedId === null ? null : notes[selectedId] ?? null;
  const hasUnsavedChanges = useMemo(
    () => Object.values(notes).some((note) => note.saveState && note.saveState !== "saved"),
    [notes],
  );

  return {
    summaries,
    activeNote,
    selectedId,
    isLoading: selectedId !== null && loadingId === selectedId && !activeNote,
    isCreating,
    error,
    hasUnsavedChanges,
    selectNote,
    editSelected,
    createNewNote,
    removeSelected,
    retrySave: () => {
      if (selectedId === null) return;
      if (selectedId < 0) {
        void createTemporaryOnServer(selectedId);
      } else {
        void persist(selectedId);
      }
    },
    forceSave: () => selectedId !== null && void persist(selectedId, true),
    loadServerVersion,
    saveNow: () => {
      if (selectedId === null) return;
      const timer = timersRef.current.get(selectedId);
      if (timer) window.clearTimeout(timer);
      timersRef.current.delete(selectedId);
      void persist(selectedId);
    },
    clearError: () => setError(null),
  };
}
