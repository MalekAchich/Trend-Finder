import { useEffect, useRef, useState } from "react";
import type { FoundVideo, RunAgent, StreamEvent } from "../api/types";
import { applyEvents, initialStream, type StreamState } from "./reduce";

export const EVENT_TYPES = [
  "run.state", "character.read", "trend.studied", "plan.created", "agent.started", "agent.thought", "agent.tool_call",
  "agent.tool_result", "agent.finished", "candidate.rejected", "analysis.started", "analysis.finished", "video.saved",
  "provider.switched", "round.finished", "error",
];

/**
 * Live run events over SSE. The browser resends Last-Event-ID on reconnect; the reducer dedupes by id, so a reload
 * or a dropped connection never shows an event twice. `onLiveSave` fires only for videos saved while watching.
 */
export function useRunStream(runId: string | null, seed: RunAgent[], onLiveSave?: (v: FoundVideo) => void) {
  const [state, setState] = useState<StreamState>(() => initialStream(seed));
  const [connected, setConnected] = useState(false);
  const saveRef = useRef(onLiveSave);
  saveRef.current = onLiveSave;
  const seedKey = seed.map((a) => a.id).join(",");

  useEffect(() => {
    setState((s) => (s.events.length ? s : initialStream(seed)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey]);

  useEffect(() => {
    if (!runId) return;
    setState(initialStream(seed));
    const openedAt = Date.now();
    const source = new EventSource(`/api/runs/${runId}/events`);
    let pending: StreamEvent[] = [];
    let frame = 0;
    const flush = () => {
      frame = 0;
      const batch = pending;
      pending = [];
      setState((s) => applyEvents(s, batch));
    };
    const onEvent = (raw: MessageEvent) => {
      const e = JSON.parse(raw.data) as StreamEvent;
      pending.push(e);
      if (!frame) frame = requestAnimationFrame(flush);
      if (e.type === "video.saved" && Date.parse(e.created_at) > openedAt - 4000) saveRef.current?.(e.payload.video);
    };
    for (const t of EVENT_TYPES) source.addEventListener(t, onEvent as EventListener);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    return () => {
      source.close();
      if (frame) cancelAnimationFrame(frame);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return { state, connected };
}
