import { AlertTriangle, ArrowRightLeft, Check, ChevronDown, CircleSlash, Eye, Film, Radio, Search, Sparkles, Square,
  Star, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FoundVideo, RunDetail, StreamEvent } from "../api/types";
import { filterEvents, plain, type Group, type StreamState } from "../stream/reduce";
import { PlatformIcon, ROLE_LABEL, ROLE_TINT, STATE_TEXT, clock } from "./ui";

const GROUP_LABEL: Record<Group, string> = {
  thinking: "Thinking", plans: "Plans", tools: "Tools", videos: "Videos", problems: "Problems", status: "Status",
};
const LIVE_STATES = ["created", "reading_character", "studying_trends", "discovering", "planning", "running", "curating", "paused_usage"];
const MAX_ROWS = 400;

interface Props {
  run: RunDetail;
  stream: StreamState;
  connected: boolean;
  onStop: () => void;
  stopping: boolean;
  savedStripRef: React.RefObject<HTMLDivElement | null>;
}

export function Engine({ run, stream, connected, onStop, stopping, savedStripRef }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [agent, setAgent] = useState<string | null>(null);
  const [groups, setGroups] = useState<Set<Group>>(new Set());
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const state = stream.runState ?? run.state;
  const live = run.active || LIVE_STATES.includes(state);
  const startedAt = Date.parse(run.started_at);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live]);
  const elapsed = ((live ? now : Date.parse(run.finished_at ?? run.started_at)) - startedAt) / 1000;

  const shown = useMemo(() => filterEvents(stream.events, { agent, groups, query }), [stream.events, agent, groups, query]);
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (follow && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [shown.length, follow]);

  const toggleGroup = (g: Group) => setGroups((s) => {
    const n = new Set(s);
    if (n.has(g)) n.delete(g); else n.add(g);
    return n;
  });

  return (
    <section className="wrap pb-10" aria-label="Live agent run">
      <div className="overflow-hidden rounded-2xl border border-line bg-panel/40">
        <header className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-line px-5 py-3.5">
          {run.character.image_url && <img src={run.character.image_url} alt="" className="h-8 w-8 rounded-full object-cover object-top" />}
          <div className="min-w-0">
            <p className="text-[14px] font-medium">{run.character.name}</p>
            <p className="flex items-center gap-2 text-[12.5px] text-mist">
              <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-lime pulse-dot" : "bg-mist"}`} />
              <span role="status">{STATE_TEXT[state] ?? state}{run.stop_reason && !live ? ":" : ""}{state === "running" && run.round ? `, round ${run.round}` : ""}</span>
              {run.stop_reason && !live && <span className="text-mist/80">{run.stop_reason}</span>}
            </p>
          </div>
          <span className="num text-[13px] text-mist">{clock(elapsed)} of {run.minutes} min</span>
          {live && !connected && <span className="text-[12px] text-warn">Reconnecting…</span>}
          <div className="ml-auto flex items-center gap-2">
            {live && state !== "curating" && (
              <button className="btn-ghost" onClick={onStop} disabled={stopping}><Square size={12} fill="currentColor" />
                {stopping ? "Stopping…" : "Stop and rank what's found"}</button>
            )}
            <button className="btn-ghost !px-2.5" aria-label={collapsed ? "Show the run" : "Hide the run"}
              aria-expanded={!collapsed} onClick={() => setCollapsed((c) => !c)}>
              <ChevronDown size={16} className={`transition ${collapsed ? "-rotate-90" : ""}`} /></button>
          </div>
        </header>

        {!collapsed && (
          <div className="grid lg:grid-cols-[290px_minmax(0,1fr)]">
            <aside className="border-b border-line p-3 lg:border-b-0 lg:border-r" aria-label="Agents">
              <p className="px-2 pb-2 pt-1 text-[12px] text-mist">Agents {agent && <button className="ml-1 text-lime"
                onClick={() => setAgent(null)}>show all</button>}</p>
              <ul className="scroll-thin max-h-[560px] space-y-1 overflow-y-auto">
                {stream.order.map((id) => {
                  const a = stream.agents[id];
                  if (!a) return null;
                  return (
                    <li key={id}>
                      <button onClick={() => setAgent(agent === id ? null : id)} aria-pressed={agent === id}
                        className={`w-full rounded-lg px-2.5 py-2 text-left transition hover:bg-white/[0.04] ${agent === id ? "bg-white/[0.07]" : ""}`}>
                        <span className="flex items-center gap-2 text-[13px]">
                          <StatusDot status={a.status} />
                          <span className="font-medium" style={{ color: ROLE_TINT[a.role] }}>{ROLE_LABEL[a.role] ?? a.role}</span>
                          {a.platform && <span className="flex items-center gap-1 text-[11.5px] text-mist"><PlatformIcon platform={a.platform} size={11} />{a.platform}</span>}
                        </span>
                        {a.action && <span className="mt-1 line-clamp-2 block pl-4 text-[12px] leading-snug text-mist">{a.action}</span>}
                      </button>
                    </li>
                  );
                })}
                {stream.order.length === 0 && <li className="px-2 text-[12.5px] text-mist">Agents appear as the run starts.</li>}
              </ul>
            </aside>

            <div className="min-w-0">
              <div ref={savedStripRef}>
                {stream.saved.length > 0 && (
                  <div className="scroll-thin flex gap-2 overflow-x-auto border-b border-line px-4 py-3" aria-label="Saved so far">
                    {stream.saved.map((v) => <SavedThumb key={v.id} video={v} />)}
                  </div>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
                <label className="flex items-center gap-2 text-[12.5px] text-mist">
                  <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} className="accent-lime" />
                  Follow live
                </label>
                <span className="mx-1 h-4 w-px bg-line-2" />
                {(Object.keys(GROUP_LABEL) as Group[]).filter((g) => g !== "status").map((g) => (
                  <button key={g} className="chip !h-7 !text-[12px]" aria-pressed={groups.has(g)} onClick={() => toggleGroup(g)}>
                    {GROUP_LABEL[g]}</button>
                ))}
                <label className="relative ml-auto">
                  <span className="sr-only">Search the stream</span>
                  <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-mist" />
                  <input className="field !h-8 !w-56 !pl-8" placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)} />
                </label>
              </div>
              <ol ref={listRef} className="scroll-thin h-[520px] space-y-0.5 overflow-y-auto px-4 py-3" aria-live="polite"
                onWheel={(e) => { if (e.deltaY < 0) setFollow(false); }}>
                {shown.length > MAX_ROWS && <li className="py-2 text-center text-[12px] text-mist">
                  Showing the latest {MAX_ROWS} of {shown.length} events. Search or filter to dig further back.</li>}
                {shown.slice(-MAX_ROWS).map((e) => <EventRow key={e.id} e={e} startedAt={startedAt} />)}
                {shown.length === 0 && <li className="py-10 text-center text-[13px] text-mist">
                  {stream.events.length ? "Nothing matches these filters." : "Waiting for the first agent to report…"}</li>}
              </ol>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function StatusDot({ status }: { status: string }) {
  if (status === "done") return <Check size={11} className="text-lime" aria-label="done" />;
  if (status === "failed") return <span className="h-2 w-2 rounded-full bg-bad" aria-label="failed" />;
  if (status === "working") return <span className="pulse-dot h-2 w-2 rounded-full bg-lime" aria-label="working" />;
  return <span className="h-2 w-2 rounded-full border border-mist" aria-label="waiting" />;
}

function SavedThumb({ video }: { video: FoundVideo }) {
  return (
    <a href={video.url} target="_blank" rel="noreferrer" className="card-enter relative block h-[84px] w-[48px] shrink-0 overflow-hidden rounded-md bg-panel"
      title={video.why ?? video.url} data-saved-id={video.id}>
      {video.thumbnail_url && <img src={video.thumbnail_url} alt="" className="h-full w-full object-cover" />}
      {video.score != null && <span className="num absolute bottom-0.5 right-0.5 rounded bg-black/70 px-1 text-[10px]">{Math.round(video.score)}</span>}
    </a>
  );
}

function EventRow({ e, startedAt }: { e: StreamEvent; startedAt: number }) {
  const p = e.payload;
  const role = p.agent?.role as string | undefined;
  const t = clock((Date.parse(e.created_at) - startedAt) / 1000);
  const who = role && (
    <span className="shrink-0 text-[12px] font-medium" style={{ color: ROLE_TINT[role] }}>{ROLE_LABEL[role] ?? role}</span>
  );
  const row = (icon: React.ReactNode, body: React.ReactNode, tone = "") => (
    <li className={`event-in grid grid-cols-[44px_18px_minmax(0,1fr)] items-start gap-2 rounded-md px-1.5 py-1.5 text-[13px] leading-relaxed ${tone}`}>
      <span className="num pt-px text-[11.5px] text-mist/80">{t}</span>
      <span className="pt-0.5 text-mist">{icon}</span>
      <div className="min-w-0">{body}</div>
    </li>
  );
  switch (e.type) {
    case "run.state":
      return (
        <li className="flex items-center gap-3 py-2 text-[12px] text-mist">
          <span className="h-px flex-1 bg-line" /><span>{STATE_TEXT[p.state] ?? p.state}{p.stop_reason ? `: ${p.stop_reason}` : ""}</span>
          <span className="h-px flex-1 bg-line" />
        </li>
      );
    case "agent.thought":
      return row(<Sparkles size={13} />, <p className="text-snow/90">{who} <Collapsible text={plain(p.text)} /></p>);
    case "character.read":
      return row(<Eye size={13} />, (
        <div className="rounded-xl border border-lime/30 bg-lime/[0.06] p-3.5">
          <p className="text-[12.5px] font-medium text-lime">Reading the character</p>
          <dl className="mt-2 grid gap-1.5 text-[13px]">
            <div><dt className="inline text-mist">Look: </dt><dd className="inline">{p.read.look}</dd></div>
            <div><dt className="inline text-mist">Vibe: </dt><dd className="inline">{p.read.vibe}</dd></div>
            <div><dt className="inline text-mist">On camera: </dt><dd className="inline">{p.read.performance_angle}</dd></div>
            <div><dt className="inline text-mist">For Kling: </dt><dd className="inline">{p.read.kling_constraints}</dd></div>
          </dl>
          <ul className="mt-2.5 flex flex-wrap gap-1.5">
            {(p.read.possible_niches as string[]).map((n) => <li key={n} className="chip !h-6 !text-[11.5px] !text-snow">{n}</li>)}
          </ul>
        </div>
      ));
    case "trend.studied":
      return row(<Film size={13} />, (
        <div className="rounded-xl border border-line-2 p-3">
          <p className="text-[12.5px] text-mist">Studied <a href={p.url} target="_blank" rel="noreferrer" className="underline">{p.url}</a></p>
          <p className="mt-1">{p.study.format}</p>
          <p className="mt-1 text-[12.5px] text-mist">Hook: {p.study.hook}. Why it works: {p.study.why_it_works}</p>
        </div>
      ));
    case "plan.created":
      return row(<Radio size={13} />, (
        <div className="rounded-xl border border-line-2 bg-white/[0.02] p-3.5">
          <p className="text-[12.5px] font-medium" style={{ color: ROLE_TINT.lead }}>Round {p.round} plan</p>
          <p className="mt-1.5"><Collapsible text={plain(p.reasoning)} limit={420} /></p>
          <ul className="mt-2.5 space-y-1">
            {(p.tasks as { task_id: string; role: string; platform: string; goal: string }[]).map((t) => (
              <li key={t.task_id} className="flex gap-2 text-[12.5px]">
                <span className="shrink-0" style={{ color: ROLE_TINT[t.role] }}>{ROLE_LABEL[t.role] ?? t.role}</span>
                <span className="shrink-0 text-mist">{t.platform}</span><span className="text-snow/85">{t.goal}</span>
              </li>
            ))}
          </ul>
          {p.refused?.length > 0 && (
            <details className="mt-2 text-[12.5px] text-mist">
              <summary className="cursor-pointer">{p.refused.length} proposed {p.refused.length === 1 ? "search was" : "searches were"} refused</summary>
              <ul className="mt-1 list-disc pl-5">{(p.refused as { goal: string; reason: string }[]).map((r, i) =>
                <li key={i}>{r.goal}: {r.reason}</li>)}</ul>
            </details>
          )}
        </div>
      ));
    case "agent.tool_call":
      return row(<Wrench size={13} />, (
        <details className="group">
          <summary className="cursor-pointer list-none">{who} <span className="text-mist">calls</span> <span>{p.tool}</span>
            {argSummary(p.args) && <span className="text-mist"> “{argSummary(p.args)}”</span>}</summary>
          <pre className="mt-1.5 overflow-x-auto rounded-lg bg-black/30 p-2.5 text-[11.5px] text-snow/80">{JSON.stringify(p.args, null, 2)}</pre>
        </details>
      ));
    case "agent.tool_result":
      return row(p.ok ? <Check size={13} /> : <AlertTriangle size={13} className="text-warn" />, (
        <p className="text-mist">{who} <span>{p.tool}</span> {p.ok ? "returned" : "failed"}: <Collapsible text={p.summary} limit={160} /></p>
      ));
    case "agent.started":
      return row(<Radio size={13} />, <p>{who} <span className="text-mist">started:</span> {p.goal}</p>);
    case "agent.finished":
      return row(p.failed ? <AlertTriangle size={13} className="text-bad" /> : <Check size={13} className="text-lime" />, (
        <p>{who} {p.failed ? <span className="text-bad">stopped: {p.failed}</span>
          : <span className="text-mist">finished with {p.accepted} {p.accepted === 1 ? "candidate" : "candidates"} and {p.leads} {p.leads === 1 ? "lead" : "leads"}</span>}</p>
      ));
    case "analysis.started":
      return row(<Eye size={13} />, <p className="text-mist">{who} is watching {p.canonical_id}</p>);
    case "analysis.finished":
      return null; // the verdict shows as "saved" or as a rejection with its reason
    case "candidate.rejected":
      return row(<CircleSlash size={13} />, role === "owner"
        ? <p className="text-mist">Your target {p.canonical_id} couldn't be used: {String(p.reason).replace(/^your target: /, "")}</p>
        : <p className="text-mist">{who} rejected {p.canonical_id ?? "a candidate"}: {p.reason}</p>);
    case "video.saved":
      return row(<Star size={13} className="text-lime" />, (
        <a href={p.video.url} target="_blank" rel="noreferrer" className="flex items-center gap-3 rounded-lg border border-lime/25 bg-lime/[0.05] p-2 hover:bg-lime/[0.09]">
          {p.video.thumbnail_url && <img src={p.video.thumbnail_url} alt="" className="h-14 w-8 rounded object-cover" />}
          <span className="min-w-0">
            <span className="block">Saved a {p.video.platform} video{p.video.creator ? ` by @${p.video.creator}` : ""}
              {p.video.score != null && <span className="num text-lime"> scoring {Math.round(p.video.score)}</span>}</span>
            {p.video.why && <span className="line-clamp-1 block text-[12.5px] text-mist">{p.video.why}</span>}
          </span>
        </a>
      ));
    case "provider.switched":
      return row(<ArrowRightLeft size={13} className="text-warn" />,
        <p className="text-warn">{who} switched from {p.from} to {p.to}{p.reason ? `: ${p.reason}` : ""}</p>);
    case "agent.refused":
      return row(<AlertTriangle size={13} className="text-bad" />, (
        <div className="rounded-lg border border-bad/40 bg-bad/[0.07] p-2.5">
          <p className="text-bad">{who} was refused by {p.provider === "chatgpt" ? "ChatGPT" : "Claude"}
            {p.detail ? ` (${String(p.detail).replace(/_/g, " ")})` : ""}. Not retried: the prompt needs a fix.</p>
          {p.request_id && <p className="mt-0.5 text-[12px] text-mist">Request {p.request_id}, details in logs/refusals.jsonl</p>}
        </div>
      ));
    case "error":
      return row(<AlertTriangle size={13} className="text-bad" />, <p className="text-bad">{who} {p.message}</p>);
    case "round.finished":
      return row(<Radio size={13} />, <p className="text-mist">Round {p.round} finished with {p.new_analyzed} new {p.new_analyzed === 1 ? "video" : "videos"} analyzed</p>);
    default:
      return null;
  }
}

function argSummary(args: Record<string, unknown> | undefined): string {
  if (!args) return "";
  const v = args.query ?? args.hashtag ?? args.handle ?? args.url;
  return v == null ? "" : String(v);
}

function Collapsible({ text, limit = 280 }: { text: string; limit?: number }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  if (text.length <= limit) return <>{text}</>;
  return (
    <>
      {open ? text : `${text.slice(0, limit).trimEnd()}… `}
      <button className="text-[12px] text-lime" onClick={() => setOpen((o) => !o)}>{open ? " Show less" : "Show more"}</button>
    </>
  );
}
