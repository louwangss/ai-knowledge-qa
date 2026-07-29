import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Note } from "../types";
import { NotesWorkspace } from "./NotesWorkspace";
import type { WorkspaceView } from "./WorkspaceTabs";

const firstNote: Note = {
  id: 1,
  user_id: "u1",
  concept: "注意力机制",
  content: "第一篇正文",
  created_at: "2026-07-28T10:00:00",
  updated_at: "2026-07-28T10:00:00",
  version: "a".repeat(64),
};

const secondNote: Note = {
  ...firstNote,
  id: 2,
  concept: "RAG 检索",
  content: "第二篇正文",
  version: "b".repeat(64),
};

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function jsonResponse(value: unknown, status = 200) {
  return Promise.resolve(response(value, status));
}

function WorkspaceHarness({
  onNavigate,
  onLogout = () => undefined,
}: {
  onNavigate: (view: WorkspaceView) => void;
  onLogout?: () => void;
}) {
  const [view, setView] = useState<WorkspaceView>("notes");
  if (view !== "notes") return <div>{view}</div>;
  return (
    <NotesWorkspace
      userId="u1"
      onChangeView={(nextView) => {
        onNavigate(nextView);
        setView(nextView);
      }}
      onLogout={onLogout}
    />
  );
}

describe("NotesWorkspace 导航前保存", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/summaries")) {
        return jsonResponse([
          { id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at },
          { id: 2, concept: secondNote.concept, updated_at: secondNote.updated_at },
        ]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      if (url.includes("/notes/2?")) return jsonResponse(secondNote);
      return jsonResponse({ detail: "ok" });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("编辑后立即切换工作区时，等待防抖中的保存完成再卸载笔记页", async () => {
    let resolveUpdate!: (value: Response) => void;
    const updateResponse = new Promise<Response>((resolve) => { resolveUpdate = resolve; });
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") return updateResponse;
      if (url.includes("/summaries")) {
        return jsonResponse([{ id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at }]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      return jsonResponse({ detail: "ok" });
    });
    const onNavigate = vi.fn();

    render(<WorkspaceHarness onNavigate={onNavigate} />);
    const editor = await screen.findByDisplayValue("第一篇正文");
    fireEvent.change(editor, { target: { value: "尚未保存的正文" } });
    fireEvent.click(screen.getByRole("button", { name: "问答" }));

    expect(screen.getByDisplayValue("尚未保存的正文")).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/notes/1"),
      expect.objectContaining({ method: "PUT" }),
    );

    resolveUpdate(response({ ...firstNote, content: "尚未保存的正文", version: "c".repeat(64) }));

    expect(await screen.findByText("chat")).toBeInTheDocument();
    expect(onNavigate).toHaveBeenCalledWith("chat");
  });

  it("等待所有已编辑笔记保存完成后再切换工作区", async () => {
    const resolvers = new Map<number, (value: Response) => void>();
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") {
        const noteId = url.includes("/notes/1") ? 1 : 2;
        return new Promise<Response>((resolve) => { resolvers.set(noteId, resolve); });
      }
      if (url.includes("/summaries")) {
        return jsonResponse([
          { id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at },
          { id: 2, concept: secondNote.concept, updated_at: secondNote.updated_at },
        ]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      if (url.includes("/notes/2?")) return jsonResponse(secondNote);
      return jsonResponse({ detail: "ok" });
    });
    const onNavigate = vi.fn();

    render(<WorkspaceHarness onNavigate={onNavigate} />);
    fireEvent.change(await screen.findByDisplayValue("第一篇正文"), {
      target: { value: "第一篇待保存" },
    });
    fireEvent.click(screen.getByRole("button", { name: /RAG 检索/ }));
    fireEvent.change(await screen.findByDisplayValue("第二篇正文"), {
      target: { value: "第二篇待保存" },
    });
    fireEvent.click(screen.getByRole("button", { name: "文档" }));

    await waitFor(() => expect(resolvers.size).toBe(2));
    expect(onNavigate).not.toHaveBeenCalled();

    resolvers.get(1)?.(response({ ...firstNote, content: "第一篇待保存", version: "c".repeat(64) }));
    await Promise.resolve();
    expect(onNavigate).not.toHaveBeenCalled();

    resolvers.get(2)?.(response({ ...secondNote, content: "第二篇待保存", version: "d".repeat(64) }));
    expect(await screen.findByText("documents")).toBeInTheDocument();
    expect(onNavigate).toHaveBeenCalledWith("documents");
  });

  it.each([
    { status: 500, state: "保存失败", detail: "网络或后端暂时不可用，本地文字仍保留在当前页面。" },
    { status: 409, state: "发现版本冲突", detail: "服务器上有更新，已暂停自动保存以免覆盖内容。" },
  ])("保存返回 $status 时保留笔记页并展示 $state", async ({ status, state, detail }) => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") return jsonResponse({ detail: "failed" }, status);
      if (url.includes("/summaries")) {
        return jsonResponse([{ id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at }]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      return jsonResponse({ detail: "ok" });
    });
    const onNavigate = vi.fn();

    render(<WorkspaceHarness onNavigate={onNavigate} />);
    fireEvent.change(await screen.findByDisplayValue("第一篇正文"), {
      target: { value: "不能丢失的本地正文" },
    });
    fireEvent.click(screen.getByRole("button", { name: "问答" }));

    expect(await screen.findByText(state)).toBeInTheDocument();
    expect(screen.getByText(detail)).toBeInTheDocument();
    expect(screen.getByDisplayValue("不能丢失的本地正文")).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("编辑后立即退出时，保存完成前不注销", async () => {
    let resolveUpdate!: (value: Response) => void;
    const updateResponse = new Promise<Response>((resolve) => { resolveUpdate = resolve; });
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") return updateResponse;
      if (url.includes("/summaries")) {
        return jsonResponse([{ id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at }]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      return jsonResponse({ detail: "ok" });
    });
    const onLogout = vi.fn();

    render(<WorkspaceHarness onNavigate={vi.fn()} onLogout={onLogout} />);
    fireEvent.change(await screen.findByDisplayValue("第一篇正文"), {
      target: { value: "退出前必须保存" },
    });
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/notes/1"),
      expect.objectContaining({ method: "PUT" }),
    ));
    expect(onLogout).not.toHaveBeenCalled();

    resolveUpdate(response({ ...firstNote, content: "退出前必须保存", version: "c".repeat(64) }));
    await waitFor(() => expect(onLogout).toHaveBeenCalledTimes(1));
  });

  it("非当前笔记保存冲突时，切回冲突笔记并显示原因", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT" && url.includes("/notes/1")) {
        return jsonResponse({ detail: { code: "NOTE_VERSION_CONFLICT" } }, 409);
      }
      if (url.includes("/summaries")) {
        return jsonResponse([
          { id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at },
          { id: 2, concept: secondNote.concept, updated_at: secondNote.updated_at },
        ]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      if (url.includes("/notes/2?")) return jsonResponse(secondNote);
      return jsonResponse({ detail: "ok" });
    });
    const onNavigate = vi.fn();

    render(<WorkspaceHarness onNavigate={onNavigate} />);
    fireEvent.change(await screen.findByDisplayValue("第一篇正文"), {
      target: { value: "第一篇冲突草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: /RAG 检索/ }));
    await screen.findByDisplayValue("第二篇正文");
    fireEvent.click(screen.getByRole("button", { name: "问答" }));

    expect(await screen.findByText("发现版本冲突")).toBeInTheDocument();
    expect(screen.getByDisplayValue("第一篇冲突草稿")).toBeInTheDocument();
    expect(screen.getByText(/注意力机制.*版本冲突/)).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("临时笔记创建完成后才离开工作区", async () => {
    let resolveCreate!: (value: Response) => void;
    const createResponse = new Promise<Response>((resolve) => { resolveCreate = resolve; });
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/notes") && init?.method === "POST") return createResponse;
      if (url.includes("/summaries")) return jsonResponse([]);
      return jsonResponse({ detail: "ok" });
    });
    const onNavigate = vi.fn();

    render(<WorkspaceHarness onNavigate={onNavigate} />);
    await screen.findByText("还没有匹配的笔记");
    fireEvent.click(screen.getByRole("button", { name: /新建笔记/ }));
    fireEvent.click(screen.getByRole("button", { name: "问答" }));

    expect(onNavigate).not.toHaveBeenCalled();
    resolveCreate(response({ ...firstNote, id: 9, concept: "", content: "" }));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("chat"));
  });

  it("flush 期间冻结编辑操作且只保存已接纳的快照", async () => {
    let resolveUpdate!: (value: Response) => void;
    const updateResponse = new Promise<Response>((resolve) => { resolveUpdate = resolve; });
    let updateCount = 0;
    let createCount = 0;
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") {
        updateCount += 1;
        return updateCount === 1
          ? updateResponse
          : jsonResponse({ ...firstNote, content: "flush 期间的新输入", version: "d".repeat(64) });
      }
      if (url.endsWith("/notes") && init?.method === "POST") {
        createCount += 1;
        return jsonResponse({ ...secondNote, id: 10, concept: "", content: "" });
      }
      if (url.includes("/summaries")) {
        return jsonResponse([{ id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at }]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      return jsonResponse({ detail: "ok" });
    });
    const onNavigate = vi.fn();

    render(<WorkspaceHarness onNavigate={onNavigate} />);
    const editor = await screen.findByDisplayValue("第一篇正文");
    fireEvent.change(editor, { target: { value: "离开前快照" } });
    fireEvent.click(screen.getByRole("button", { name: "问答" }));
    await waitFor(() => expect(updateCount).toBe(1));

    fireEvent.change(editor, { target: { value: "flush 期间的新输入" } });
    const createButton = screen.getByRole("button", { name: /正在创建/ });
    expect(createButton).toBeDisabled();
    fireEvent.click(createButton);
    expect(createCount).toBe(0);

    resolveUpdate(response({ ...firstNote, content: "离开前快照", version: "c".repeat(64) }));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("chat"));
    expect(updateCount).toBe(1);
  });

  it("flush 进行中以最后一次离开意图为准，避免迟到导航", async () => {
    let resolveUpdate!: (value: Response) => void;
    const updateResponse = new Promise<Response>((resolve) => { resolveUpdate = resolve; });
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") return updateResponse;
      if (url.includes("/summaries")) {
        return jsonResponse([{ id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at }]);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      return jsonResponse({ detail: "ok" });
    });
    const onNavigate = vi.fn();
    const onLogout = vi.fn();

    render(<WorkspaceHarness onNavigate={onNavigate} onLogout={onLogout} />);
    fireEvent.change(await screen.findByDisplayValue("第一篇正文"), {
      target: { value: "竞态前草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "问答" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/notes/1"),
      expect.objectContaining({ method: "PUT" }),
    ));
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(onNavigate).not.toHaveBeenCalled();
    expect(onLogout).not.toHaveBeenCalled();
    resolveUpdate(response({ ...firstNote, content: "竞态前草稿", version: "c".repeat(64) }));

    await waitFor(() => expect(onLogout).toHaveBeenCalledTimes(1));
    expect(onNavigate).not.toHaveBeenCalled();
  });
});
