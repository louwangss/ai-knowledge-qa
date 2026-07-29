import { describe, expect, it } from "vitest";

import { parseSseStream } from "./chatSse";

function chunkedStream(chunks: string[]) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("Chat SSE 解析器", () => {
  it("跨任意网络分块保留完整事件，并解析同一分块中的多个事件", async () => {
    const stream = chunkedStream([
      "event: sta",
      "tus\ndata: {\"content\":\"正在检索\"}\n\nevent: token\ndata: {\"cont",
      "ent\":\"你\"}\n\nevent: token\ndata: {\"content\":\"好\"}\n\n",
      "event: sources\ndata: {\"content\":[{\"source\":\"intro.pdf\",\"score\":0.12}]}\n\nevent: done\ndata: {}\n\n",
    ]);

    const events = [];
    for await (const event of parseSseStream(stream)) events.push(event);

    expect(events).toEqual([
      { type: "status", content: "正在检索" },
      { type: "token", content: "你" },
      { type: "token", content: "好" },
      { type: "sources", content: [{ source: "intro.pdf", score: 0.12 }] },
      { type: "done" },
    ]);
  });

  it("忽略注释与未知事件，但保留后续错误事件", async () => {
    const stream = chunkedStream([
      ": keepalive\n\nevent: future\ndata: {\"content\":\"ignore\"}\n\n",
      "event: error\ndata: {\"content\":\"回答生成中断\"}\n\n",
    ]);

    const events = [];
    for await (const event of parseSseStream(stream)) events.push(event);

    expect(events).toEqual([{ type: "error", content: "回答生成中断" }]);
  });
});
