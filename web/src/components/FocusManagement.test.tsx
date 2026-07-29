import type { ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";
import { DocumentsPanel } from "./DocumentsPanel";
import { Editor } from "./Editor";

function createChatProps(): ComponentProps<typeof ChatPanel> {
  return {
    messages: [],
    sessionId: "session-1",
    isLoading: false,
    isStreaming: false,
    isDeleting: false,
    onSend: vi.fn(async () => true),
    onStop: vi.fn(),
    onDelete: vi.fn(async () => true),
    onCreate: vi.fn(),
    onOpenSidebar: vi.fn(),
  };
}

describe("焦点管理", () => {
  it("回答结束后把焦点还给聊天输入框", () => {
    const props = createChatProps();
    const { rerender } = render(<ChatPanel {...props} isStreaming />);

    const stopButton = screen.getByRole("button", { name: "停止回答" });
    stopButton.focus();
    expect(stopButton).toHaveFocus();

    rerender(<ChatPanel {...props} isStreaming={false} />);

    expect(screen.getByRole("textbox", { name: "输入问题" })).toHaveFocus();
  });

  it("回答结束时不抢走用户主动移到其他控件的焦点", () => {
    const props = createChatProps();
    const { rerender } = render(
      <>
        <button>其他操作</button>
        <ChatPanel {...props} isStreaming />
      </>,
    );

    const otherAction = screen.getByRole("button", { name: "其他操作" });
    otherAction.focus();
    rerender(
      <>
        <button>其他操作</button>
        <ChatPanel {...props} isStreaming={false} />
      </>,
    );

    expect(otherAction).toHaveFocus();
  });

  it("关闭会话删除弹窗后把焦点还给删除按钮", () => {
    render(<ChatPanel {...createChatProps()} />);
    const deleteButton = screen.getByRole("button", { name: "删除当前会话" });

    deleteButton.focus();
    fireEvent.click(deleteButton);
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteButton).toHaveFocus();
  });

  it("关闭笔记删除弹窗后把焦点还给删除按钮", () => {
    render(
      <Editor
        note={{
          id: 1,
          user_id: "default-user",
          concept: "测试笔记",
          content: "内容",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          version: "v1",
          saveState: "saved",
        }}
        isLoading={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRetry={vi.fn()}
        onLoadServer={vi.fn()}
        onForceSave={vi.fn()}
        onOpenSidebar={vi.fn()}
      />,
    );
    const deleteButton = screen.getByRole("button", { name: "删除当前笔记" });

    deleteButton.focus();
    fireEvent.click(deleteButton);
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteButton).toHaveFocus();
  });

  it("关闭文档删除弹窗后把焦点还给对应文档的删除按钮", () => {
    render(
      <DocumentsPanel
        documents={[{
          id: "document-1",
          filename: "产品说明.pdf",
          file_type: ".pdf",
          file_size: 1024,
          chunk_count: 3,
          status: "ready",
          created_at: "2026-01-01T00:00:00Z",
        }]}
        isLoading={false}
        isUploading={false}
        onUpload={vi.fn(async () => true)}
        onDelete={vi.fn(async () => true)}
        onOpenSidebar={vi.fn()}
      />,
    );
    const deleteButton = screen.getByRole("button", { name: "删除 产品说明.pdf" });

    deleteButton.focus();
    fireEvent.click(deleteButton);
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteButton).toHaveFocus();
  });
});
