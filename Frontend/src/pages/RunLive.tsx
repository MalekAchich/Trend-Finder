import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { RunDetail, RunEvent, RunTask } from "../api/types";
import { ErrorNote, StatePill } from "../components/bits";
import { useRunEvents } from "../hooks/useRunEvents";

const TERMINAL = ["review_ready", "stopped", "failed"];
const taskKind: Record<string, string> = {
  scout: "Scout", radar: "Trend radar", deep_dive: "Deep dive", seed_study: "Seed study", analyze: "Analysis",
};
const taskState: Record<string, string> = {
  queued: "Waiting", running: "Working", succeeded: "Done", failed: "Failed", cancelled: "Cancelled",
};

function scopeText(scope: RunTask["scope"]) {
  return Object.entries(scope)
    .map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v ?? "")])
    .filter(([, v]) => v.trim()).map(([k, v]) => `${k}: ${v}`).join("; ");
}

const count = (v: unknown) => (Array.isArray(v) ? v.length : typeof v === "number" ? v : 0);

function eventLine(e: RunEvent): string | null {
  const p = e.payload;
  switch (e.type) {
    case "run.state": return `Run is now ${String(p.state).replace("_", " ")}${p.stop_reason ? `: ${p.stop_reason}` : ""}`;
    case "plan.created": return `Round ${p.round} planned with ${p.tasks} searches`;
    case "round.finished": return `Round ${p.round} finished with ${p.new_analyzed ?? 0} new analyzed videos`;
    case "task.finished": return p.type === "analyze" ? null
      : `A ${taskKind[String(p.type)]?.toLowerCase() ?? "worker"} returned ${count(p.accepted)} ${count(p.accepted) === 1 ? "candidate" : "candidates"}`;
    default: return null;
  }
}

function Worker({ task, latest }: { task: RunTask; latest?: string }) {
  const working = task.state === "running";
  return (
    <li className={`rounded-md border p-3 ${working ? "border-olive bg-paper" : "border-rule bg-paper/60"}`}>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-semibold">{taskKind[task.type] ?? task.type}{task.platform ? ` on ${task.platform}` : ""}</span>
        <span className={working ? "font-semibold text-olive" : task.state === "failed" ? "text-grease" : "text-graphite"}>
          {taskState[task.state] ?? task.state}
        </span>
      </div>
      {task.goal && <p className="mt-1 line-clamp-2 text-sm">{task.goal}</p>}
      {scopeText(task.scope) && <p className="mt-1 truncate text-xs text-graphite" title={scopeText(task.scope)}>{scopeText(task.scope)}</p>}
      {working && latest && <p className="mt-2 truncate text-xs text-olive">{latest}</p>}
      {task.state === "succeeded" && task.type !== "analyze" && (
        <p className="num mt-2 text-xs text-graphite">{count(task.result.accepted)} candidates, {count(task.result.leads)} leads</p>
      )}
    </li>
  );
}

export default function RunLive() {
  const { id } = useParams();
  const run = useQuery({
    queryKey: ["run", id], queryFn: () => api.get<RunDetail>(`/api/runs/${id}`),
    refetchInterval: (q) => (q.state.data && TERMINAL.includes(q.state.data.state) ? false : 8_000),
  });
  const { events, connected } = useRunEvents(id);
  const stop = useMutation({ mutationFn: () => api.post(`/api/runs/${id}/stop`), onSuccess: () => run.refetch() });
  const resume = useMutation({ mutationFn: () => api.post(`/api/runs/${id}/resume`), onSuccess: () => run.refetch() });

  const latestByTask = useMemo(() => {
    const m: Record<string, string> = {};
    for (const e of events) if (e.type === "task.progress") m[String(e.payload.task_id)] = String(e.payload.detail);
    return m;
  }, [events]);
  const feed = useMemo(() => events.map((e) => ({ e, line: eventLine(e) })).filter((x) => x.line).reverse().slice(0, 40), [events]);

  if (run.error) return <ErrorNote error={run.error} />;
  const r = run.data;
  if (!r) return <p className="text-graphite">Loading run…</p>;
  const finished = TERMINAL.includes(r.state);
  const workers = r.tasks.filter((t) => t.type !== "analyze");
  const analysis = r.tasks.filter((t) => t.type === "analyze");
  const analyzing = analysis.filter((t) => t.state === "running").length;
  const found = Object.values(r.findings).reduce((a, b) => a + b, 0);

  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="capitalize">{r.character}</h1>
        <StatePill state={r.state} />
        {!finished && <span className="text-sm text-graphite">{connected ? "Live" : "Reconnecting…"}</span>}
        <div className="ml-auto flex gap-2">
          {r.state === "review_ready" && <Link className="btn btn-primary" to={`/runs/${id}/review`}>Review trend cards</Link>}
          {!finished && r.active && r.state !== "curating" && <button className="btn" onClick={() => stop.mutate()} disabled={stop.isPending}>Stop and rank what's found</button>}
          {r.active && r.state === "curating" && <span className="self-center text-sm text-graphite">Ranking what was found…</span>}
          {!finished && !r.active && <button className="btn" onClick={() => resume.mutate()} disabled={resume.isPending}>Resume run</button>}
        </div>
      </div>
      <ErrorNote error={stop.error ?? resume.error} />
      {r.error && <p className="mt-3 text-grease">{r.error}</p>}
      <dl className="num mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
        <div><dt className="text-graphite">Round</dt><dd className="text-lg font-semibold">{r.round} of {String(r.settings.rounds ?? "?")}</dd></div>
        <div><dt className="text-graphite">Videos found</dt><dd className="text-lg font-semibold">{found}</dd></div>
        <div><dt className="text-graphite">Analyzed</dt><dd className="text-lg font-semibold">{r.findings.analyzed ?? 0}</dd></div>
        <div><dt className="text-graphite">Filtered out</dt><dd className="text-lg font-semibold">{r.findings.filtered_feasibility ?? 0}</dd></div>
        <div><dt className="text-graphite">Being analyzed</dt><dd className="text-lg font-semibold">{analyzing}</dd></div>
        {r.explore_ratio != null && <div><dt className="text-graphite">New ideas vs proven</dt>
          <dd className="text-lg font-semibold">{Math.round(r.explore_ratio * 100)}% new</dd></div>}
      </dl>

      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_20rem]">
        <div className="min-w-0 space-y-8">
          <section>
            <h2>What the lead agent decided</h2>
            {r.rounds.length === 0 && <p className="mt-2 text-graphite">Planning the first round…</p>}
            <ol className="mt-3 space-y-4">
              {r.rounds.map((rd) => (
                <li key={rd.number} className="print rounded-lg p-4">
                  <h3>Round {rd.number}</h3>
                  <p className="mt-1 whitespace-pre-line">{rd.summary ?? "Planning…"}</p>
                  {rd.rejections.length > 0 && (
                    <details className="mt-2 text-sm text-graphite">
                      <summary className="cursor-pointer">{rd.rejections.length} proposed searches were refused</summary>
                      <ul className="mt-1 list-disc pl-5">{rd.rejections.map((x, i) => <li key={i}>{x.reason}</li>)}</ul>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          </section>
          <section>
            <h2>Search agents</h2>
            {workers.length === 0 && <p className="mt-2 text-graphite">No searches yet.</p>}
            <ul className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {workers.map((t) => <Worker key={t.id} task={t} latest={latestByTask[t.id]} />)}
            </ul>
          </section>
        </div>
        <aside>
          <h2>Activity</h2>
          {feed.length === 0 && <p className="mt-2 text-sm text-graphite">Waiting for the first update.</p>}
          <ol className="mt-3 space-y-2 text-sm">
            {feed.map(({ e, line }) => (
              <li key={e.id} className="border-l-2 border-rule pl-3">
                <span className="num block text-xs text-graphite">{new Date(e.created_at).toLocaleTimeString()}</span>
                {line}
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </div>
  );
}
