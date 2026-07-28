import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
});
