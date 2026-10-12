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

/** EventSource's onerror fires both when the connection drops and for the run's own events named "error" (an
 * agent's failure, which carries data): only the first means the page lost the server. */
export const lostConnection = (e: Event) => typeof (e as MessageEvent).data !== "string";

const FINAL = ["review_ready", "stopped", "failed"];

/** The run is over: the server ends the stream, so the page must stop EventSource's automatic reconnects. */
export const endsStream = (e: StreamEvent) => e.type === "run.state" && FINAL.includes(String(e.payload.state));
