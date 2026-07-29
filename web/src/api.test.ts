import { afterEach, describe, expect, it, vi } from "vitest";

import { getChatTurnStatus, streamChat } from "./api";


describe("Chat API 幂等协议", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("序列化 client_turn_id 并保留结构化 409 错误码", async () => {
    const clientTurnId = "123e4567-e89b-42d3-a456-426614174000";
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "TURN_IN_PROGRESS",
        message: "该请求正在处理中",
      },
    }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const stream = streamChat("u1", "s1", "问题", "normal", clientTurnId);
    await expect(stream.next()).rejects.toMatchObject({
      status: 409,
      code: "TURN_IN_PROGRESS",
      message: "该请求正在处理中",
    });

    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(request.body as string)).toEqual({
      user_id: "u1",
      session_id: "s1",
      message: "问题",
      mode: "normal",
      client_turn_id: clientTurnId,
    });
  });

  it("turn 状态查询透传取消信号", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      client_turn_id: "123e4567-e89b-42d3-a456-426614174000",
      status: "processing",
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await getChatTurnStatus(
      "u1",
      "s1",
      "123e4567-e89b-42d3-a456-426614174000",
      controller.signal,
    );

    expect(fetchMock.mock.calls[0][1]).toMatchObject({ signal: controller.signal });
  });
});
