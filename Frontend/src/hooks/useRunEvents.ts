import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { RunEvent } from "../api/types";

const FINAL = new Set(["review_ready", "stopped", "failed"]);

/** Live run events over SSE. The browser resends Last-Event-ID on reconnect, so nothing is lost or repeated. */
export function useRunEvents(runId: string | undefined) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    const source = new EventSource(`/api/runs/${runId}/events`);
    const seen = new Set<number>();
    const onEvent = (raw: MessageEvent) => {
      const e = JSON.parse(raw.data) as RunEvent;
      if (seen.has(e.id)) return;
      seen.add(e.id);
      setEvents((prev) => [...prev, e]);
      if (e.type === "run.state" || e.type === "round.finished" || e.type === "task.finished") {
        queryClient.invalidateQueries({ queryKey: ["run", runId] });
      }
      if (e.type === "run.state" && FINAL.has(String(e.payload.state))) {
        queryClient.invalidateQueries({ queryKey: ["trends", runId] });
        source.close();
        setConnected(false);
      }
    };
    for (const type of ["run.state", "plan.created", "task.progress", "task.finished", "round.finished"]) {
      source.addEventListener(type, onEvent);
    }
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, [runId, queryClient]);

  return { events, connected };
}
