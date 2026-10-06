import type { ReactNode } from "react";
import type { RunState } from "../api/types";

const stateText: Record<string, string> = {
  created: "Starting", planning: "Planning", running: "Searching", paused_usage: "Paused for usage limits",
  curating: "Ranking results", review_ready: "Ready for review", stopped: "Stopped", failed: "Failed",
  interrupted: "Interrupted",
};

export function StatePill({ state }: { state: RunState | string }) {
  const live = ["planning", "running", "curating", "created"].includes(state);
  const tone = live ? "bg-olive text-paper" : state === "review_ready" ? "bg-grease text-paper"
    : state === "paused_usage" || state === "interrupted" ? "bg-ink text-paper" : "bg-rule text-ink";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-sm font-semibold ${tone}`}>
      {live && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-paper motion-reduce:animate-none" />}
      {stateText[state] ?? state}
    </span>
  );
}

export function ScoreBar({ label, value, hint }: { label: string; value: number | null; hint?: string }) {
  const v = value ?? 0;
  return (
    <div title={hint}>
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-graphite">{label}</span>
        <span className="num font-semibold">{value == null ? "–" : Math.round(v)}</span>
      </div>
      <div className="mt-1 h-1.5 rounded-full bg-rule">
        <div className="h-1.5 rounded-full bg-ink" style={{ width: `${Math.max(0, Math.min(v, 100))}%` }} />
      </div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-rule p-8 text-center">
      <h2>{title}</h2>
      {children && <div className="mt-2 text-graphite">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p role="alert" className="rounded-md border border-grease/40 bg-grease/5 px-3 py-2 text-sm text-grease">
    {error instanceof Error ? error.message : String(error)}</p>;
}

export const views = (n?: number | null) =>
  n == null ? "–" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(n);

export const ago = (iso?: string | null) => {
  if (!iso) return "unknown age";
  const h = (Date.now() - new Date(iso).getTime()) / 36e5;
  return h < 48 ? `${Math.round(h)}h old` : `${Math.round(h / 24)} days old`;
};
