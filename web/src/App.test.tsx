import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const firstNote = {
  id: 1,
  user_id: "u1",
  concept: "注意力机制",
  content: "第一篇正文",
  created_at: "2026-07-28T10:00:00",
  updated_at: "2026-07-28T10:00:00",
  version: "a".repeat(64),
};

const secondNote = {
  ...firstNote,
  id: 2,
  concept: "RAG 检索",
  content: "第二篇正文",
  version: "b".repeat(64),
};

function jsonResponse(value: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function sseResponse(chunks: string[]) {
  const encoder = new TextEncoder();
  return Promise.resolve(new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } }));
}

describe("笔记工作区", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/app/");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
        if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
        if (url.includes("/summaries")) {
          return jsonResponse([
            { id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at },
            { id: 2, concept: secondNote.concept, updated_at: secondNote.updated_at },
          ]);
        }
        if (init?.method === "PUT") {
          return jsonResponse({ ...firstNote, content: "已修改", version: "c".repeat(64) });
        }
        if (url.includes("/notes/1?")) return jsonResponse(firstNote);
        if (url.includes("/notes/2?")) return jsonResponse(secondNote);
        return jsonResponse({ detail: "ok" });
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("加载列表后可切换笔记，并在缓存命中时立即恢复正文", async () => {
    render(<App />);

    expect(await screen.findByDisplayValue("第一篇正文")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /RAG 检索/ }));
    expect(await screen.findByDisplayValue("第二篇正文")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /注意力机制/ }));
    expect(screen.getByDisplayValue("第一篇正文")).toBeInTheDocument();
  });

  it("输入立即标记未保存，并在防抖后后台保存", async () => {
    render(<App />);
    const editor = await screen.findByDisplayValue("第一篇正文");

    fireEvent.change(editor, { target: { value: "已修改" } });
    expect(screen.getByText("未保存")).toBeInTheDocument();

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/notes/1"),
        expect.objectContaining({ method: "PUT" }),
      ), { timeout: 2000 });
    expect(screen.getByText("已保存")).toBeInTheDocument();
  });

  it("删除确认后立即从列表移除，不等待后端清理向量", async () => {
    render(<App />);
    await screen.findByDisplayValue("第一篇正文");

    fireEvent.click(screen.getByRole("button", { name: "删除当前笔记" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    expect(screen.queryByRole("button", { name: /注意力机制/ })).not.toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/notes/1"),
      expect.objectContaining({ method: "DELETE" }),
    ));
  });

  it("版本冲突时保留本地正文并停止自动覆盖", async () => {
    const baseFetch = vi.mocked(fetch);
    baseFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/summaries")) {
        return jsonResponse([{ id: 1, concept: firstNote.concept, updated_at: firstNote.updated_at }]);
      }
      if (init?.method === "PUT") {
        return jsonResponse({ detail: { code: "NOTE_VERSION_CONFLICT" } }, 409);
      }
      if (url.includes("/notes/1?")) return jsonResponse(firstNote);
      return jsonResponse({ detail: "ok" });
    });

    render(<App />);
    const editor = await screen.findByDisplayValue("第一篇正文");
    fireEvent.change(editor, { target: { value: "本地重要草稿" } });

    expect(await screen.findByText("发现版本冲突", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByDisplayValue("本地重要草稿")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保留本地版本" })).toBeInTheDocument();
  });

  it("新建请求失败时保留草稿并允许重试", async () => {
    let createAttempts = 0;
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/summaries")) return jsonResponse([]);
      if (url.endsWith("/notes") && init?.method === "POST") {
        createAttempts += 1;
        return createAttempts === 1
          ? jsonResponse({ detail: "failed" }, 500)
          : jsonResponse({ ...firstNote, id: 8, concept: "", content: "" });
      }
      return jsonResponse({ detail: "ok" });
    });

    render(<App />);
    await screen.findByText("还没有匹配的笔记");
    fireEvent.click(screen.getByRole("button", { name: /新建笔记/ }));
    expect(await screen.findByText("保存失败")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试保存" }));
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(createAttempts).toBe(2);
  });

  it("可以进入问答工作区并加载最近会话", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/sessions")) {
        return jsonResponse([{
          id: "session-1",
          user_id: "u1",
          title: "RAG 是什么",
          status: "active",
          created_at: "2026-07-28T10:00:00",
          last_active: "2026-07-28T10:00:00",
        }]);
      }
      if (url.includes("/chat/history")) return jsonResponse([
        {
          id: 1,
          role: "user",
          content: "RAG 是什么？",
          mode: "deep",
          created_at: "2026-07-28T10:00:00",
          sources: [],
        },
        {
          id: 2,
          role: "assistant",
          content: "RAG 是检索增强生成。",
          mode: "deep",
          created_at: "2026-07-28T10:00:01",
          sources: [{ source: "rag-guide.pdf", score: 0.82 }],
        },
      ]);
      if (url.includes("/summaries")) return jsonResponse([]);
      return jsonResponse({ detail: "ok" });
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "问答" }));

    expect(await screen.findByText("RAG 是什么？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /RAG 是什么/ })).toHaveAttribute("aria-current", "page");
    const restoredAnswer = screen.getByLabelText("AI 回答");
    expect(within(restoredAnswer).getByText("深度研究")).toBeInTheDocument();
    expect(within(restoredAnswer).getByText("rag-guide.pdf")).toBeInTheDocument();
  });

  it("普通问答逐块显示回答并在完成后展示来源", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/sessions")) {
        return jsonResponse([{
          id: "session-1",
          user_id: "u1",
          title: null,
          status: "active",
          created_at: "2026-07-28T10:00:00",
          last_active: "2026-07-28T10:00:00",
        }]);
      }
      if (url.includes("/chat/history")) return jsonResponse([]);
      if (url.endsWith("/chat") && init?.method === "POST") {
        return sseResponse([
          "event: token\ndata: {\"content\":\"RAG 是\"}\n\n",
          "event: token\ndata: {\"content\":\"检索增强生成。\"}\n\n",
          "event: sources\ndata: {\"content\":[{\"source\":\"rag.md\",\"score\":0.1}]}\n\n",
          "event: done\ndata: {}\n\n",
        ]);
      }
      if (url.includes("/summaries")) return jsonResponse([]);
      return jsonResponse({ detail: "ok" });
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "问答" }));
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "解释一下 RAG" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));

    expect(await screen.findByText("RAG 是检索增强生成。")).toBeInTheDocument();
    expect(screen.getByText("rag.md")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/chat"),
      expect.objectContaining({
        body: expect.stringMatching(/"mode":"normal".*"client_turn_id":"[0-9a-f-]{36}"/),
      }),
    );
  });

  it("深度研究模式发送 deep，并显示阶段事件后的完整回答", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/sessions")) return jsonResponse([{
        id: "session-deep", user_id: "u1", title: "深度研究", status: "active",
        created_at: "2026-07-28T10:00:00", last_active: "2026-07-28T10:00:00",
      }]);
      if (url.includes("/chat/history")) return jsonResponse([]);
      if (url.endsWith("/chat") && init?.method === "POST") {
        return sseResponse([
          "event: status\ndata: {\"content\":\"正在拆解问题…\"}\n\n",
          "event: token\ndata: {\"content\":\"研究结论\"}\n\n",
          "event: done\ndata: {}\n\n",
        ]);
      }
      if (url.includes("/summaries")) return jsonResponse([]);
      return jsonResponse({ detail: "ok" });
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "问答" }));
    await screen.findByText("从你的资料里找到答案");
    fireEvent.click(screen.getByRole("button", { name: "深度研究" }));
    fireEvent.change(screen.getByRole("textbox", { name: "输入问题" }), { target: { value: "研究 RAG 架构" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));

    expect(await screen.findByText("研究结论")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/chat"),
      expect.objectContaining({ body: expect.stringContaining('"mode":"deep"') }),
    );
  });

  it("删除会话后立即切换到下一会话", async () => {
    const sessions = [
      { id: "s1", user_id: "u1", title: "第一个会话", status: "active", created_at: "2026-07-28T10:00:00", last_active: "2026-07-28T11:00:00" },
      { id: "s2", user_id: "u1", title: "第二个会话", status: "active", created_at: "2026-07-28T09:00:00", last_active: "2026-07-28T10:00:00" },
    ];
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/sessions/s1") && init?.method === "DELETE") return jsonResponse({ detail: "删除成功" });
      if (url.includes("/sessions")) return jsonResponse(sessions);
      if (url.includes("/chat/history")) return jsonResponse([]);
      if (url.includes("/summaries")) return jsonResponse([]);
      return jsonResponse({ detail: "ok" });
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "问答" }));
    expect(await screen.findByRole("button", { name: /第一个会话/ })).toHaveAttribute("aria-current", "page");
    fireEvent.click(screen.getByRole("button", { name: "删除当前会话" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/sessions/s1"),
      expect.objectContaining({ method: "DELETE" }),
    ));
    expect(screen.queryByRole("button", { name: /第一个会话/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /第二个会话/ })).toHaveAttribute("aria-current", "page");
  });

  it("退出登录后撤销服务端会话并返回令牌输入页", async () => {
    render(<App />);
    await screen.findByDisplayValue("第一篇正文");

    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(await screen.findByLabelText("访问令牌")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/web/session"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
