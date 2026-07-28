import { useRef, useState } from "react";

import type { KnowledgeDocument } from "../types";
import { DocumentIcon, MenuIcon, TrashIcon, UploadIcon } from "./Icons";

interface DocumentsPanelProps {
  documents: KnowledgeDocument[];
  isLoading: boolean;
  isUploading: boolean;
  onUpload: (file: File) => Promise<boolean>;
  onDelete: (documentId: string) => Promise<boolean>;
  onOpenSidebar: () => void;
}

function formatSize(value: number | null) {
  if (value === null) return "大小未知";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function DocumentsPanel({
  documents,
  isLoading,
  isUploading,
  onUpload,
  onDelete,
  onOpenSidebar,
}: DocumentsPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocument | null>(null);

  async function selectFile(file?: File) {
    if (!file || isLoading || isUploading) return;
    await onUpload(file);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <main className="documents-shell">
      <header className="documents-toolbar">
        <button className="mobile-menu" onClick={onOpenSidebar} aria-label="打开文档导航"><MenuIcon /></button>
        <div><span className="eyebrow">KNOWLEDGE SOURCES</span><strong>文档管理</strong></div>
        <span>{documents.length} 份文档</span>
      </header>

      <div className="documents-page">
        <section className="documents-intro">
          <div>
            <p className="eyebrow">YOUR LIBRARY</p>
            <h1>构建可检索的知识库</h1>
            <p>上传资料后，系统会完成解析、分块和向量索引。处理完成的内容会自动参与问答。</p>
          </div>
          <label
            className={`upload-dropzone ${dragging ? "is-dragging" : ""} ${isLoading || isUploading ? "is-uploading" : ""}`}
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              void selectFile(event.dataTransfer.files[0]);
            }}
          >
            <input
              ref={inputRef}
              className="sr-only"
              type="file"
              aria-label="选择文档"
              accept=".pdf,.docx,.txt,.md,.html,.htm"
              disabled={isLoading || isUploading}
              onChange={(event) => void selectFile(event.target.files?.[0])}
            />
            <span className="upload-icon"><UploadIcon /></span>
            <span><strong>{isUploading ? "正在解析文档…" : "选择文件或拖放到这里"}</strong><small>支持 PDF、DOCX、TXT、Markdown 和 HTML</small></span>
          </label>
        </section>

        <section className="document-library" aria-busy={isLoading}>
          <div className="document-library-heading">
            <div><h2>资料库</h2><p>所有已上传并可用于检索的资料</p></div>
            {isUploading && <span className="processing-label" role="status">正在建立索引</span>}
          </div>

          {isLoading ? (
            <div className="document-loading" aria-label="正在加载文档"><span /><span /><span /></div>
          ) : documents.length === 0 ? (
            <div className="document-empty"><DocumentIcon /><h3>知识库还是空的</h3><p>上传第一份资料，开始建立你的可检索知识库。</p></div>
          ) : (
            <ul className="document-list" aria-label="文档列表">
              {documents.map((document) => (
                <li key={document.id} className={document.status === "processing" ? "is-processing" : ""}>
                  <span className="document-type" aria-hidden="true">{document.file_type.replace(".", "").toUpperCase()}</span>
                  <div className="document-copy">
                    <strong>{document.filename}</strong>
                    <span>
                      {document.status === "processing" ? "正在解析与建立索引" : `${document.chunk_count ?? 0} 个片段`}
                      <i aria-hidden="true">·</i>{formatSize(document.file_size)}
                      <i aria-hidden="true">·</i>{formatDate(document.created_at)}
                    </span>
                  </div>
                  {document.status === "processing" ? (
                    <span className="document-progress" aria-label="正在处理" />
                  ) : (
                    <button className="icon-button delete-button" aria-label={`删除 ${document.filename}`} onClick={() => setDeleteTarget(document)}><TrashIcon /></button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {deleteTarget && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDeleteTarget(null); }}>
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-document-title">
            <div className="dialog-icon"><TrashIcon /></div>
            <h2 id="delete-document-title">删除这份文档？</h2>
            <p>“{deleteTarget.filename}”的源文件和检索索引都会被移除，此操作无法撤销。</p>
            <div className="dialog-actions">
              <button onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="danger-action" onClick={() => { const id = deleteTarget.id; setDeleteTarget(null); void onDelete(id); }}>确认删除</button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
