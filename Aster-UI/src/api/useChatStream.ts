import { ASTER_API_BASE } from "./config";

export type StreamStatus = "idle" | "streaming" | "done" | "error";

export function useChatStream() {
  async function sendMessage(
    text: string,
    onChunk: (chunk: string) => void,
    onDone: () => void,
    onError: (err: string) => void,
  ): Promise<void> {
    try {
      const res = await fetch(`${ASTER_API_BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });

      if (!res.ok) { onError(`HTTP ${res.status}`); return; }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6);
          if (data === "[DONE]") { onDone(); return; }
          onChunk(data);
        }
      }
      onDone();
    } catch (err) {
      onError(String(err));
    }
  }

  return { sendMessage };
}
