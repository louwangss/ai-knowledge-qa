import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  deleteDocument as deleteDocumentRequest,
  listDocuments,
  uploadDocument as uploadDocumentRequest,
} from "./api";
import type { KnowledgeDocument } from "./types";

const SUPPORTED_EXTENSIONS = new Set(["pdf", "docx", "txt", "md", "html", "htm"]);

function uploadError(cause: unknown) {
  if (!(cause instanceof ApiError)) return "上传失败，请检查后端服务后重试。";
  if (cause.status === 409) return "这份文档已经存在，无需重复上传。";
  if (cause.status === 413) return "文件超过服务端允许的大小。";
  if (cause.status === 415) return "暂不支持这种文件格式。";
  return "文档处理失败，请检查文件内容后重试。";
}

export function useDocumentsWorkspace(userId: string) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uploadSequence = useRef(0);

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    setError(null);
    void listDocuments(userId)
      .then((items) => { if (active) setDocuments(items); })
      .catch(() => { if (active) setError("文档列表加载失败，请确认后端服务可用。"); })
      .finally(() => { if (active) setIsLoading(false); });
    return () => { active = false; };
  }, [userId]);

  const uploadDocument = useCallback(async (file: File) => {
    if (isLoading || isUploading) return false;
    const extension = file.name.split(".").pop()?.toLocaleLowerCase("en-US") ?? "";
    if (!SUPPORTED_EXTENSIONS.has(extension)) {
      setError("请选择 PDF、DOCX、TXT、Markdown 或 HTML 文件。");
      return false;
    }

    const temporaryId = `upload-${++uploadSequence.current}`;
    const optimistic: KnowledgeDocument = {
      id: temporaryId,
      filename: file.name,
      file_type: `.${extension}`,
      file_size: file.size,
      chunk_count: null,
      status: "processing",
      created_at: new Date().toISOString(),
    };
    setError(null);
    setIsUploading(true);
    setDocuments((current) => [optimistic, ...current]);
    try {
      const created = await uploadDocumentRequest(userId, file);
      setDocuments((current) => current.map((item) => item.id === temporaryId ? created : item));
      return true;
    } catch (cause) {
      setDocuments((current) => current.filter((item) => item.id !== temporaryId));
      setError(uploadError(cause));
      return false;
    } finally {
      setIsUploading(false);
    }
  }, [isLoading, isUploading, userId]);

  const removeDocument = useCallback(async (documentId: string) => {
    const targetIndex = documents.findIndex((item) => item.id === documentId);
    const target = documents[targetIndex];
    if (!target || target.status === "processing") return false;
    setError(null);
    setDocuments((current) => current.filter((item) => item.id !== documentId));
    try {
      await deleteDocumentRequest(userId, documentId);
      return true;
    } catch {
      setDocuments((current) => {
        if (current.some((item) => item.id === documentId)) return current;
        const restored = [...current];
        restored.splice(Math.min(targetIndex, restored.length), 0, target);
        return restored;
      });
      setError("删除失败，文档已恢复到列表中。");
      return false;
    }
  }, [documents, userId]);

  return {
    documents,
    isLoading,
    isUploading,
    error,
    uploadDocument,
    removeDocument,
    clearError: () => setError(null),
  };
}
