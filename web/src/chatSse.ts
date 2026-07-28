import type { ChatSource, ChatStreamEvent } from "./types";

const knownEvents = new Set(["token", "status", "sources", "done", "error"]);

function decodeEvent(block: string): ChatStreamEvent | null {
  let eventType = "message";
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") eventType = value;
    if (field === "data") dataLines.push(value);
  }

  if (!knownEvents.has(eventType) || dataLines.length === 0) return null;
  const payload = JSON.parse(dataLines.join("\n")) as { content?: unknown };
  if (eventType === "done") return { type: "done" };
  if (eventType === "sources") {
    return {
      type: "sources",
      content: Array.isArray(payload.content) ? payload.content as ChatSource[] : [],
    };
  }
  return {
    type: eventType as "token" | "status" | "error",
    content: typeof payload.content === "string" ? payload.content : "",
  };
}

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatStreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });

      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        const delimiter = buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] ?? "\n\n";
        buffer = buffer.slice(boundary + delimiter.length);
        const event = decodeEvent(block);
        if (event) yield event;
        boundary = buffer.search(/\r?\n\r?\n/);
      }

      if (done) break;
    }

    if (buffer.trim()) {
      const event = decodeEvent(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}
