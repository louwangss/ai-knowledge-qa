import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { ChatWorkspace } from "./components/ChatWorkspace";
import type { ChatHistoryMessage, ChatStreamEvent, SessionSummary } from "./types";
import { useChatWorkspace } from "./useChatWorkspace";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    createSession: vi.fn(),
    deleteSession: vi.fn(),
    getChatHistory: vi.fn(),
    getChatTurnStatus: vi.fn(),
    listSessions: vi.fn(),
    streamChat: vi.fn(),
  };
});

const sessions: SessionSummary[] = [
  {
    id: "session-a",
    user_id: "u1",
    title: "会话 A",
    status: "active",
    created_at: "2026-07-29T10:00:00",
    last_active: "2026-07-29T11:00:00",
  },
  {
    id: "session-b",
    user_id: "u1",
    title: "会话 B",
    status: "active",
    created_at: "2026-07-29T09:00:00",
    last_active: "2026-07-29T10:00:00",
  },
];

const canonicalHistory: ChatHistoryMessage[] = [
  {
    id: 7,
    role: "user",
    content: "服务端已确认的问题",
    mode: "normal",
    created_at: "2026-07-29T10:00:00",
  },
  {
    id: 8,
    role: "assistant",
    content: "服务端已确认的回答",
    mode: "normal",
    created_at: "2026-07-29T10:00:01",
  },
];

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function eventsStream(events: ChatStreamEvent[]): AsyncGenerator<ChatStreamEvent> {
  return (async function* () {
    for (const event of events) yield event;
  })();
}

function failedStream(kind: "sse" | "abort" | "network"): AsyncGenerator<ChatStreamEvent> {
  return (async function* () {
    yield { type: "token", content: "未确认的部分回答" };
    if (kind === "sse") {
      yield { type: "error", content: "回答生成中断" };
      return;
    }
    if (kind === "abort") throw new DOMException("Aborted", "AbortError");
    throw new TypeError("Failed to fetch");
  })();
}

describe("React Chat 会话一致性", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.sessionStorage.clear();
    vi.mocked(api.listSessions).mockResolvedValue(sessions);
    vi.mocked(api.getChatHistory).mockResolvedValue([]);
    vi.mocked(api.getChatTurnStatus).mockResolvedValue({
      client_turn_id: "00000000-0000-4000-8000-000000000000",
      status: "failed",
    });
    vi.mocked(api.streamChat).mockImplementation(() => eventsStream([{ type: "done" }]));
  });

  it("历史加载完成前拒绝发送", async () => {
    const history = deferred<ChatHistoryMessage[]>();
    vi.mocked(api.getChatHistory).mockReturnValueOnce(history.promise);
    const { result, unmount } = renderHook(() => useChatWorkspace("u1"));

    await waitFor(() => expect(result.current.selectedId).toBe("session-a"));
    await waitFor(() => expect(result.current.isLoading).toBe(true));

    let accepted = true;
    await act(async () => {
      accepted = await result.current.sendMessage("加载期间的问题", "normal");
    });

    expect(accepted).toBe(false);
    expect(result.current.activeMessages).toEqual([]);
    expect(api.streamChat).not.toHaveBeenCalled();
    unmount();
  });

  it("会话列表首次加载完成前不会新建会话或发送", async () => {
    const sessionList = deferred<SessionSummary[]>();
    vi.mocked(api.listSessions).mockReturnValueOnce(sessionList.promise);
    const { result } = renderHook(() => useChatWorkspace("u1"));

    await waitFor(() => expect(result.current.isLoading).toBe(true));
    let accepted = true;
    await act(async () => {
      accepted = await result.current.sendMessage("不能抢跑的问题", "normal");
    });

    expect(accepted).toBe(false);
    expect(api.createSession).not.toHaveBeenCalled();
    expect(api.streamChat).not.toHaveBeenCalled();

    await act(async () => {
      sessionList.resolve(sessions);
      await Promise.resolve();
    });
  });

  it("首个会话创建期间占用离开门禁，卸载后不会继续启动问答", async () => {
    const createdSession = deferred<SessionSummary>();
    vi.mocked(api.listSessions).mockResolvedValueOnce([]);
    vi.mocked(api.createSession).mockReturnValueOnce(createdSession.promise);
    const { result, unmount } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let sendPromise!: Promise<boolean>;
    act(() => {
      sendPromise = result.current.sendMessage("首个问题", "normal");
    });

    await waitFor(() => expect(api.createSession).toHaveBeenCalledOnce());
    expect(result.current.hasActiveStreams).toBe(true);
    const createSignal = vi.mocked(api.createSession).mock.calls[0][1];

    unmount();
    expect(createSignal?.aborted).toBe(true);
    createdSession.resolve({
      id: "created-session",
      user_id: "u1",
      title: null,
      status: "active",
      created_at: "2026-07-29T12:00:00",
      last_active: "2026-07-29T12:00:00",
    });

    await expect(sendPromise).resolves.toBe(false);
    expect(api.streamChat).not.toHaveBeenCalled();
  });

  it("首个会话创建挂起时可以从界面停止", async () => {
    vi.mocked(api.listSessions).mockResolvedValueOnce([]);
    vi.mocked(api.createSession).mockImplementationOnce((_userId, signal) => new Promise((_, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    render(<ChatWorkspace userId="u1" onChangeView={vi.fn()} onLogout={vi.fn()} />);

    const input = await screen.findByRole("textbox", { name: "输入问题" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "创建时可停止" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));

    const stop = await screen.findByRole("button", { name: "停止回答" });
    fireEvent.click(stop);

    await waitFor(() => expect(screen.queryByRole("button", { name: "停止回答" })).not.toBeInTheDocument());
    expect(api.streamChat).not.toHaveBeenCalled();
  });

  it("历史加载失败后可以重试并恢复服务端消息", async () => {
    vi.mocked(api.getChatHistory)
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(canonicalHistory);
    const { result } = renderHook(() => useChatWorkspace("u1"));

    await waitFor(() => expect(result.current.canRetryHistory).toBe(true));
    expect(result.current.isLoading).toBe(false);

    act(() => result.current.retryHistory());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.canRetryHistory).toBe(false);
    expect(result.current.activeMessages.map((message) => message.content)).toEqual([
      "服务端已确认的问题",
      "服务端已确认的回答",
    ]);
  });

  it("历史加载期间禁用输入框", () => {
    render(
      <ChatPanel
        messages={[]}
        sessionId="session-a"
        isLoading
        isStreaming={false}
        isDeleting={false}
        onSend={vi.fn()}
        onStop={vi.fn()}
        onDelete={vi.fn()}
        onCreate={vi.fn()}
        onOpenSidebar={vi.fn()}
      />,
    );

    expect(screen.getByRole("textbox", { name: "输入问题" })).toBeDisabled();
  });

  it("历史加载失败后在正文保留重试入口并禁用输入", () => {
    const onRetry = vi.fn();
    render(
      <ChatPanel
        messages={[]}
        sessionId="session-a"
        isLoading={false}
        canRetryHistory
        isStreaming={false}
        isDeleting={false}
        onSend={vi.fn()}
        onStop={vi.fn()}
        onDelete={vi.fn()}
        onCreate={vi.fn()}
        onRetryHistory={onRetry}
        onOpenSidebar={vi.fn()}
      />,
    );

    expect(screen.getByRole("textbox", { name: "输入问题" })).toBeDisabled();
    screen.getByRole("button", { name: "重试读取" }).click();
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("删除确认框冻结打开时的会话目标", async () => {
    const onDelete = vi.fn().mockResolvedValue(true);
    const commonProps = {
      messages: [],
      isLoading: false,
      isStreaming: false,
      isDeleting: false,
      onSend: vi.fn(),
      onStop: vi.fn(),
      onDelete,
      onCreate: vi.fn(),
      onOpenSidebar: vi.fn(),
    };
    const { rerender } = render(
      <ChatPanel {...commonProps} sessionId="session-a" />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除当前会话" }));
    rerender(<ChatPanel {...commonProps} sessionId="session-c" />);
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith("session-a"));
  });

  it("有回答生成时阻止切换工作区和退出登录", async () => {
    const doneGate = deferred<void>();
    vi.mocked(api.streamChat).mockImplementationOnce(() => (async function* () {
      await doneGate.promise;
      yield { type: "done" } as const;
    })());
    const onChangeView = vi.fn();
    const onLogout = vi.fn();
    render(<ChatWorkspace userId="u1" onChangeView={onChangeView} onLogout={onLogout} />);

    const input = await screen.findByRole("textbox", { name: "输入问题" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "仍在生成的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "停止回答" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "笔记" }));
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(onChangeView).not.toHaveBeenCalled();
    expect(onLogout).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("仍有回答正在生成");

    await act(async () => {
      doneGate.resolve();
      await Promise.resolve();
    });
  });

  it.each([
    ["SSE error", "sse"],
    ["Abort", "abort"],
    ["网络异常", "network"],
  ] as const)("%s 后立即移除未确认消息并返回 false", async (_label, kind) => {
    vi.mocked(api.getChatHistory).mockResolvedValueOnce([]);
    vi.mocked(api.streamChat).mockImplementationOnce(() => failedStream(kind));
    const { result } = renderHook(() => useChatWorkspace("u1"));

    await waitFor(() => expect(api.getChatHistory).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let accepted = true;
    await act(async () => {
      accepted = await result.current.sendMessage("这次回答会失败", "normal");
    });

    expect(accepted).toBe(false);
    expect(api.getChatHistory).toHaveBeenCalledTimes(1);
    expect(result.current.activeMessages).toEqual([]);
  });

  it("停止流后不启动不可取消的历史对账并立即释放离开门禁", async () => {
    vi.mocked(api.getChatHistory).mockResolvedValueOnce([]);
    vi.mocked(api.streamChat).mockImplementationOnce((...args) => {
      const signal = args[5];
      return (async function* () {
        await new Promise<void>((_, reject) => {
          signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
        });
        if (false) yield { type: "done" } as const;
      })();
    });
    const { result } = renderHook(() => useChatWorkspace("u1"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    let sendPromise!: Promise<boolean>;
    act(() => {
      sendPromise = result.current.sendMessage("停止中的问题", "normal");
    });
    await waitFor(() => expect(result.current.hasActiveStreams).toBe(true));
    act(() => result.current.stopSelected());

    await act(async () => expect(await sendPromise).toBe(false));
    expect(result.current.activeMessages).toEqual([]);
    expect(result.current.hasActiveStreams).toBe(false);
    expect(api.getChatHistory).toHaveBeenCalledTimes(1);
  });

  it("同一失败草稿重试时复用 client turn id", async () => {
    vi.mocked(api.streamChat)
      .mockImplementationOnce(() => failedStream("network"))
      .mockImplementationOnce(() => eventsStream([{ type: "done" }]));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      expect(await result.current.sendMessage("需要安全重试", "normal")).toBe(false);
    });
    await act(async () => {
      expect(await result.current.sendMessage("需要安全重试", "normal")).toBe(true);
    });

    const firstTurnId = vi.mocked(api.streamChat).mock.calls[0][4];
    const secondTurnId = vi.mocked(api.streamChat).mock.calls[1][4];
    expect(firstTurnId).toMatch(/^[0-9a-f-]{36}$/);
    expect(secondTurnId).toBe(firstTurnId);
  });

  it("失败后修改正文会创建新的 client turn id", async () => {
    vi.mocked(api.streamChat)
      .mockImplementationOnce(() => failedStream("network"))
      .mockImplementationOnce(() => eventsStream([{ type: "done" }]));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      expect(await result.current.sendMessage("原问题", "normal")).toBe(false);
    });
    await act(async () => {
      expect(await result.current.sendMessage("修改后的问题", "normal")).toBe(true);
    });

    expect(vi.mocked(api.streamChat).mock.calls[1][4]).not.toBe(
      vi.mocked(api.streamChat).mock.calls[0][4],
    );
  });

  it("切换工作区导致 Hook 重挂载后仍复用待重试 turn id", async () => {
    vi.mocked(api.streamChat)
      .mockImplementationOnce(() => failedStream("network"))
      .mockImplementationOnce(() => eventsStream([{ type: "done" }]));
    const first = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(first.result.current.isLoading).toBe(false));
    await act(async () => {
      expect(await first.result.current.sendMessage("跨页面重试", "normal")).toBe(false);
    });
    const originalTurnId = vi.mocked(api.streamChat).mock.calls[0][4];
    const storedPending = window.sessionStorage.getItem("aiqa.pending-chat-turns.v1");
    expect(storedPending).toContain(originalTurnId);
    expect(storedPending).not.toContain("跨页面重试");
    first.unmount();

    const second = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(second.result.current.isLoading).toBe(false));
    await act(async () => {
      expect(await second.result.current.sendMessage("跨页面重试", "normal")).toBe(true);
    });

    expect(vi.mocked(api.streamChat).mock.calls[1][4]).toBe(originalTurnId);
    expect(window.sessionStorage.getItem("aiqa.pending-chat-turns.v1")).toBeNull();
  });

  it("同一会话的多个失败问题分别保留自己的 turn id", async () => {
    vi.mocked(api.streamChat)
      .mockImplementationOnce(() => failedStream("network"))
      .mockImplementationOnce(() => failedStream("network"))
      .mockImplementationOnce(() => eventsStream([{ type: "done" }]))
      .mockImplementationOnce(() => eventsStream([{ type: "done" }]));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => { expect(await result.current.sendMessage("问题 A", "normal")).toBe(false); });
    await act(async () => { expect(await result.current.sendMessage("问题 B", "normal")).toBe(false); });
    const turnA = vi.mocked(api.streamChat).mock.calls[0][4];
    const turnB = vi.mocked(api.streamChat).mock.calls[1][4];
    await act(async () => { expect(await result.current.sendMessage("问题 B", "normal")).toBe(true); });
    await act(async () => { expect(await result.current.sendMessage("问题 A", "normal")).toBe(true); });

    expect(vi.mocked(api.streamChat).mock.calls[2][4]).toBe(turnB);
    expect(vi.mocked(api.streamChat).mock.calls[3][4]).toBe(turnA);
  });

  it("过期的会话列表响应不会覆盖新建会话或删除它的待重试 turn", async () => {
    const staleRefresh = deferred<SessionSummary[]>();
    const createdSession: SessionSummary = {
      id: "session-new",
      user_id: "u1",
      title: null,
      status: "active",
      created_at: "2026-07-29T12:00:00",
      last_active: "2026-07-29T12:00:00",
    };
    vi.mocked(api.listSessions)
      .mockResolvedValueOnce(sessions)
      .mockReturnValueOnce(staleRefresh.promise);
    vi.mocked(api.createSession).mockResolvedValueOnce(createdSession);
    vi.mocked(api.streamChat)
      .mockImplementationOnce(() => eventsStream([{ type: "done" }]))
      .mockImplementationOnce(() => failedStream("network"));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      expect(await result.current.sendMessage("触发后台刷新", "normal")).toBe(true);
    });
    await waitFor(() => expect(api.listSessions).toHaveBeenCalledTimes(2));
    await act(async () => {
      expect(await result.current.createNewSession()).toBe("session-new");
    });
    await act(async () => {
      expect(await result.current.sendMessage("新会话中的失败问题", "deep")).toBe(false);
    });
    const pendingTurnId = vi.mocked(api.streamChat).mock.calls[1][4] as string;

    await act(async () => {
      staleRefresh.resolve(sessions);
      await Promise.resolve();
    });

    expect(result.current.selectedId).toBe("session-new");
    expect(result.current.sessions.map((session) => session.id)).toContain("session-new");
    expect(window.sessionStorage.getItem("aiqa.pending-chat-turns.v1")).toContain(pendingTurnId);
  });

  it("删除进行中的会话列表刷新在请求完成前后都不会复活目标会话", async () => {
    const deletion = deferred<void>();
    const staleRefresh = deferred<SessionSummary[]>();
    vi.mocked(api.deleteSession).mockReturnValueOnce(deletion.promise);
    vi.mocked(api.listSessions)
      .mockResolvedValueOnce(sessions)
      .mockReturnValueOnce(staleRefresh.promise);
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let deletePromise!: Promise<boolean>;
    act(() => {
      deletePromise = result.current.removeSelected();
    });
    await waitFor(() => expect(result.current.selectedId).toBe("session-b"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      expect(await result.current.sendMessage("删除期间完成的其他会话问答", "normal")).toBe(true);
    });
    await waitFor(() => expect(api.listSessions).toHaveBeenCalledTimes(2));
    await act(async () => {
      staleRefresh.resolve(sessions);
      await Promise.resolve();
    });

    expect(result.current.sessions.map((session) => session.id)).toEqual(["session-b"]);
    expect(result.current.selectedId).toBe("session-b");

    await act(async () => {
      deletion.resolve(undefined);
      expect(await deletePromise).toBe(true);
    });

    expect(result.current.sessions.map((session) => session.id)).toEqual(["session-b"]);
    expect(result.current.selectedId).toBe("session-b");
  });

  it("删除失败只恢复目标，不覆盖先前已发出的新建会话结果", async () => {
    const creation = deferred<SessionSummary>();
    const deletion = deferred<void>();
    const createdSession: SessionSummary = {
      id: "session-c",
      user_id: "u1",
      title: null,
      status: "active",
      created_at: "2026-07-29T12:00:00",
      last_active: "2026-07-29T12:00:00",
    };
    vi.mocked(api.createSession).mockReturnValueOnce(creation.promise);
    vi.mocked(api.deleteSession).mockReturnValueOnce(deletion.promise);
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let createPromise!: Promise<string | null>;
    act(() => {
      createPromise = result.current.createNewSession();
    });
    await waitFor(() => expect(api.createSession).toHaveBeenCalledOnce());

    let deletePromise!: Promise<boolean>;
    act(() => {
      deletePromise = result.current.removeSelected("session-a");
    });
    await waitFor(() => expect(result.current.selectedId).toBe("session-b"));

    await act(async () => {
      creation.resolve(createdSession);
      expect(await createPromise).toBe("session-c");
    });
    expect(result.current.selectedId).toBe("session-c");

    await act(async () => {
      deletion.reject(new Error("delete failed"));
      expect(await deletePromise).toBe(false);
    });

    expect(result.current.sessions.map((session) => session.id)).toEqual([
      "session-a",
      "session-c",
      "session-b",
    ]);
    expect(result.current.selectedId).toBe("session-c");
  });

  it("待重试 turn 仍在处理时不重复发起流请求", async () => {
    vi.mocked(api.streamChat).mockImplementationOnce(() => failedStream("network"));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      expect(await result.current.sendMessage("仍在服务端处理", "normal")).toBe(false);
    });
    vi.mocked(api.getChatTurnStatus).mockResolvedValueOnce({
      client_turn_id: vi.mocked(api.streamChat).mock.calls[0][4] as string,
      status: "processing",
    });

    await act(async () => {
      expect(await result.current.sendMessage("仍在服务端处理", "normal")).toBe(false);
    });

    expect(api.streamChat).toHaveBeenCalledTimes(1);
    expect(result.current.error).toContain("仍在服务端处理中");
  });

  it("停止回答会取消待重试 turn 的状态预检", async () => {
    vi.mocked(api.streamChat).mockImplementationOnce(() => failedStream("network"));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      expect(await result.current.sendMessage("可取消的重试", "normal")).toBe(false);
    });

    const turnStatus = deferred<{
      client_turn_id: string;
      status: "failed";
    }>();
    vi.mocked(api.getChatTurnStatus).mockReturnValueOnce(turnStatus.promise);
    let retryPromise!: Promise<boolean>;
    act(() => {
      retryPromise = result.current.sendMessage("可取消的重试", "normal");
    });
    await waitFor(() => expect(api.getChatTurnStatus).toHaveBeenCalledOnce());
    const statusSignal = vi.mocked(api.getChatTurnStatus).mock.calls[0][3];
    expect(result.current.hasActiveStreams).toBe(true);

    act(() => result.current.stopSelected());
    expect(statusSignal?.aborted).toBe(true);
    turnStatus.resolve({
      client_turn_id: vi.mocked(api.streamChat).mock.calls[0][4] as string,
      status: "failed",
    });

    await act(async () => {
      expect(await retryPromise).toBe(false);
    });
    expect(api.streamChat).toHaveBeenCalledTimes(1);
    expect(result.current.hasActiveStreams).toBe(false);
  });

  it("待重试 turn 已完成时读取正式历史并接受原草稿", async () => {
    vi.mocked(api.getChatHistory)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce(canonicalHistory);
    vi.mocked(api.streamChat).mockImplementationOnce(() => failedStream("network"));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      expect(await result.current.sendMessage("服务端随后完成", "normal")).toBe(false);
    });
    vi.mocked(api.getChatTurnStatus).mockResolvedValueOnce({
      client_turn_id: vi.mocked(api.streamChat).mock.calls[0][4] as string,
      status: "completed",
    });

    await act(async () => {
      expect(await result.current.sendMessage("服务端随后完成", "normal")).toBe(true);
    });

    expect(api.streamChat).toHaveBeenCalledTimes(1);
    expect(result.current.activeMessages.map((message) => message.content)).toEqual([
      "服务端已确认的问题",
      "服务端已确认的回答",
    ]);
  });

  it("completed 对账被停止时保留原 turn id，随后仍可安全恢复", async () => {
    const pendingHistory = deferred<ChatHistoryMessage[]>();
    vi.mocked(api.getChatHistory)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(pendingHistory.promise)
      .mockResolvedValueOnce(canonicalHistory);
    vi.mocked(api.streamChat).mockImplementationOnce(() => failedStream("network"));
    const { result } = renderHook(() => useChatWorkspace("u1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      expect(await result.current.sendMessage("已完成但未收到", "normal")).toBe(false);
    });
    const originalTurnId = vi.mocked(api.streamChat).mock.calls[0][4] as string;
    vi.mocked(api.getChatTurnStatus)
      .mockResolvedValueOnce({ client_turn_id: originalTurnId, status: "completed" })
      .mockResolvedValueOnce({ client_turn_id: originalTurnId, status: "completed" });

    let interruptedRetry!: Promise<boolean>;
    act(() => {
      interruptedRetry = result.current.sendMessage("已完成但未收到", "normal");
    });
    await waitFor(() => expect(api.getChatHistory).toHaveBeenCalledTimes(2));
    act(() => result.current.stopSelected());
    pendingHistory.resolve(canonicalHistory);
    await act(async () => expect(await interruptedRetry).toBe(false));
    expect(window.sessionStorage.getItem("aiqa.pending-chat-turns.v1")).toContain(originalTurnId);

    await act(async () => {
      expect(await result.current.sendMessage("已完成但未收到", "normal")).toBe(true);
    });
    expect(api.streamChat).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.getChatTurnStatus).mock.calls[1][2]).toBe(originalTurnId);
    expect(window.sessionStorage.getItem("aiqa.pending-chat-turns.v1")).toBeNull();
  });

  it("会话 A 的后台流完成时保留用户当前选择的会话 B", async () => {
    const tokenGate = deferred<void>();
    const doneGate = deferred<void>();
    const sessionBHistory = deferred<ChatHistoryMessage[]>();
    const refreshGate = deferred<SessionSummary[]>();
    vi.mocked(api.listSessions)
      .mockResolvedValueOnce(sessions)
      .mockReturnValueOnce(refreshGate.promise);
    vi.mocked(api.getChatHistory)
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(sessionBHistory.promise);
    vi.mocked(api.streamChat).mockImplementationOnce(() => (async function* () {
      await tokenGate.promise;
      yield { type: "token", content: "后台 token" } as const;
      await doneGate.promise;
      yield { type: "done" } as const;
    })());
    const { result } = renderHook(() => useChatWorkspace("u1"));

    await waitFor(() => expect(api.getChatHistory).toHaveBeenCalledWith("u1", "session-a"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let sendPromise!: Promise<boolean>;
    act(() => {
      sendPromise = result.current.sendMessage("会话 A 的问题", "normal");
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(true));

    act(() => result.current.selectSession("session-b"));
    await waitFor(() => expect(result.current.selectedId).toBe("session-b"));
    await waitFor(() => expect(api.getChatHistory).toHaveBeenCalledWith("u1", "session-b"));

    await act(async () => {
      tokenGate.resolve();
      await Promise.resolve();
    });
    expect(api.getChatHistory).toHaveBeenCalledTimes(2);

    await act(async () => {
      sessionBHistory.resolve([]);
      await Promise.resolve();
    });

    let accepted = false;
    await act(async () => {
      doneGate.resolve();
      accepted = await sendPromise;
    });
    expect(accepted).toBe(true);
    expect(api.listSessions).toHaveBeenCalledTimes(2);

    await act(async () => {
      refreshGate.resolve(sessions);
      await Promise.resolve();
    });
    expect(result.current.selectedId).toBe("session-b");
  });
});
