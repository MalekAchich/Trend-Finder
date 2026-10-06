import type { StreamEvent } from "../api/types";

/** A server-sent run event, or null for anything else (EventSource fires its own data-less "error" events). */
export function parseEvent(raw: MessageEvent): StreamEvent | null {
  if (typeof raw.data !== "string") return null;
  try {
    const e = JSON.parse(raw.data) as StreamEvent;
    return typeof e?.id === "number" && typeof e.type === "string" ? e : null;
  } catch {
    return null;
  }
}
