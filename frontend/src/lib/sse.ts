// Server-Sent Events over fetch (EventSource cannot send an Authorization header).
// Reconnects with the last seen event id, so no event is lost or duplicated.

import { API_URL, getAccessToken, refreshSession } from "./api";
import type { AgentEvent, TaskStatus } from "./types";

export interface TaskSnapshot {
  status: TaskStatus;
  current_node: string | null;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
}

export interface StreamHandlers {
  onEvent: (event: AgentEvent) => void;
  onSnapshot: (snapshot: TaskSnapshot) => void;
  onEnd?: () => void;
  onError?: (error: unknown) => void;
}

export function streamTaskEvents(taskId: string, handlers: StreamHandlers, startAfter = 0): () => void {
  const controller = new AbortController();
  let lastId = startAfter;
  let stopped = false;

  const run = async () => {
    while (!stopped) {
      try {
        const res = await fetch(`${API_URL}/tasks/${taskId}/stream?after=${lastId}`, {
          headers: { Authorization: `Bearer ${getAccessToken() ?? ""}`, Accept: "text/event-stream" },
          credentials: "include",
          signal: controller.signal,
        });
        if (res.status === 401) {
          if (!(await refreshSession())) throw new Error("session expired");
          continue;
        }
        if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            if (stopped) return;
            const raw = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");
            let type = "message";
            let data = "";
            for (const line of raw.split("\n")) {
              if (line.startsWith("event:")) type = line.slice(6).trim();
              else if (line.startsWith("data:")) data += line.slice(5).trim();
              else if (line.startsWith("id:")) lastId = Number(line.slice(3).trim()) || lastId;
            }
            if (type === "agent_event" && data) handlers.onEvent(JSON.parse(data) as AgentEvent);
            else if (type === "task" && data) handlers.onSnapshot(JSON.parse(data) as TaskSnapshot);
            else if (type === "end") {
              stopped = true;
              handlers.onEnd?.();
              return;
            }
          }
        }
      } catch (error) {
        if (stopped || controller.signal.aborted) return;
        handlers.onError?.(error);
      }
      await new Promise((r) => setTimeout(r, 2000)); // reconnect backoff
    }
  };
  void run();
  return () => {
    stopped = true;
    controller.abort();
  };
}
