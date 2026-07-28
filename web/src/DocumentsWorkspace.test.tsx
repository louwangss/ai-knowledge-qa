import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const firstDocument = {
  id: "doc-1",
  filename: "rag-guide.pdf",
  file_type: ".pdf",
  file_size: 2_048,
  chunk_count: 12,
  status: "ready",
  created_at: "2026-07-28T10:00:00",
};

function jsonResponse(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

describe("文档工作区", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/app/?view=documents");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/summaries")) return jsonResponse([]);
      if (url.includes("/documents")) return jsonResponse([firstDocument]);
      return jsonResponse({ detail: "ok" });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("从地址恢复文档视图并展示已有文档元数据", async () => {
    render(<App />);

    expect(await screen.findByText("rag-guide.pdf")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "文档列表" });
    expect(within(list).getByText(/12 个片段/)).toBeInTheDocument();
    expect(within(list).getByText(/2 KB/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "文档" })).toHaveAttribute("aria-current", "page");
  });

  it("主工作区不再暴露已退役的 Gradio 入口", async () => {
    render(<App />);

    await screen.findByText("rag-guide.pdf");
    expect(screen.queryByRole("link", { name: /Gradio/ })).not.toBeInTheDocument();
  });

  it("选择受支持文件后以 multipart 上传并立即加入列表", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/summaries")) return jsonResponse([]);
      if (url.endsWith("/documents") && init?.method === "POST") {
        return jsonResponse({
          ...firstDocument,
          id: "doc-2",
          filename: "attention.md",
          file_type: ".md",
          file_size: 16,
          chunk_count: 3,
        });
      }
      if (url.includes("/documents")) return jsonResponse([firstDocument]);
      return jsonResponse({ detail: "ok" });
    });
    render(<App />);
    await screen.findByText("rag-guide.pdf");

    const file = new File(["attention notes"], "attention.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("选择文档"), { target: { files: [file] } });

    await waitFor(() => expect(screen.getByText("attention.md")).toBeInTheDocument());
    const uploadCall = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "POST");
    expect(uploadCall?.[1]?.body).toBeInstanceOf(FormData);
    expect((uploadCall?.[1]?.body as FormData).get("user_id")).toBe("u1");
    expect((uploadCall?.[1]?.body as FormData).get("file")).toBe(file);
    expect(new Headers(uploadCall?.[1]?.headers).has("Content-Type")).toBe(false);
  });

  it("初始列表尚未完成时禁用上传，避免旧响应覆盖新文档", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/documents")) return new Promise<Response>(() => undefined);
      return jsonResponse({ detail: "ok" });
    });

    render(<App />);

    expect(await screen.findByLabelText("选择文档")).toBeDisabled();
  });

  it("确认删除后乐观移除文档", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/summaries")) return jsonResponse([]);
      if (url.includes("/documents/doc-1") && init?.method === "DELETE") {
        return jsonResponse({ detail: "删除成功" });
      }
      if (url.includes("/documents")) return jsonResponse([firstDocument]);
      return jsonResponse({ detail: "ok" });
    });
    render(<App />);
    await screen.findByText("rag-guide.pdf");

    fireEvent.click(screen.getByRole("button", { name: "删除 rag-guide.pdf" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    expect(screen.queryByText("rag-guide.pdf")).not.toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/documents/doc-1"),
      expect.objectContaining({ method: "DELETE" }),
    ));
  });

  it("删除失败时恢复文档并给出可见提示", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/web/session/status")) return jsonResponse({ authenticated: true });
      if (url.includes("/web/config")) return jsonResponse({ user_id: "u1" });
      if (url.includes("/summaries")) return jsonResponse([]);
      if (url.includes("/documents/doc-1") && init?.method === "DELETE") {
        return jsonResponse({ detail: "failed" }, 503);
      }
      if (url.includes("/documents")) return jsonResponse([firstDocument]);
      return jsonResponse({ detail: "ok" });
    });
    render(<App />);
    await screen.findByText("rag-guide.pdf");

    fireEvent.click(screen.getByRole("button", { name: "删除 rag-guide.pdf" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    expect(await screen.findByText("删除失败，文档已恢复到列表中。")).toBeInTheDocument();
    expect(screen.getByText("rag-guide.pdf")).toBeInTheDocument();
  });
});
