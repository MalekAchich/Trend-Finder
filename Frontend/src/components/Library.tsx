import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { useCharacterRuns, useRateVideo, useRunVideos, useScoreRun } from "../api/hooks";
import type { Character, FoundVideo, RunSummary } from "../api/types";
import { STATE_TEXT } from "./ui";
import { VideoCard } from "./VideoCard";
import { VideoSheet } from "./VideoSheet";

interface Props {
  characters: Character[];
  slug: string | null;
  onSlug: (slug: string) => void;
  activeRunId: string | null;
  freshIds: Set<string>;
  gridRef: React.RefObject<HTMLDivElement | null>;
}

export function Library({ characters, slug, onSlug, activeRunId, freshIds, gridRef }: Props) {
  const runs = useCharacterRuns(slug);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const list = runs.data ?? [];
  const isOpen = (r: RunSummary, i: number) => open[r.id] ?? (i === 0 || r.id === activeRunId);
  return (
    <section className="bg-paper pb-24 text-ink" aria-label="Found videos">
      <div className="wrap" ref={gridRef}>
        <div className="flex flex-wrap items-end justify-between gap-4 pb-8 pt-2">
          <div>
            <h2 className="text-[26px] font-semibold">Found videos</h2>
            <p className="mt-1 text-[13px] text-ink-2">Everything the agents saved, by character and run. Hover a video to preview it.</p>
          </div>
          <div className="flex gap-1.5 rounded-full bg-paper-2 p-1" role="tablist" aria-label="Character">
            {characters.map((c) => (
              <button key={c.slug} role="tab" aria-selected={c.slug === slug} onClick={() => onSlug(c.slug)}
                className={`flex items-center gap-2 rounded-full py-1 pl-1 pr-3 text-[13px] transition
                  ${c.slug === slug ? "bg-white font-medium shadow-sm" : "text-ink-2 hover:text-ink"}`}>
                {c.image_url && <img src={c.image_url} alt="" className="h-6 w-6 rounded-full object-cover object-top" />}
                {c.name}
              </button>
            ))}
          </div>
        </div>
        {runs.isSuccess && list.length === 0 && (
          <div className="rounded-2xl border border-dashed border-rule px-6 py-14 text-center">
            <p className="text-[15px] font-medium">No runs for this character yet</p>
            <p className="mt-1 text-[13px] text-ink-2">Start one above. Videos land here as the agents save them.</p>
          </div>
        )}
        <div className="space-y-4">
          {list.map((r, i) => (
            <RunSection key={r.id} run={r} slug={slug!} open={isOpen(r, i)} live={r.id === activeRunId || r.active}
              onToggle={() => setOpen((o) => ({ ...o, [r.id]: !isOpen(r, i) }))} freshIds={freshIds} />
          ))}
        </div>
      </div>
    </section>
  );
}

function RunSection({ run, slug, open, live, onToggle, freshIds }: { run: RunSummary; slug: string; open: boolean;
  live: boolean; onToggle: () => void; freshIds: Set<string> }) {
  const when = new Date(run.started_at).toLocaleString(undefined, { month: "long", day: "numeric", hour: "numeric", minute: "2-digit" });
  return (
    <div className="rounded-2xl border border-rule bg-white/60">
      <button onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-4 gap-y-1.5 whitespace-nowrap px-5 py-4 text-left">
        <span className="text-[14.5px] font-medium">{when}</span>
        <span className={`rounded-full px-2 py-0.5 text-[11.5px] ${live ? "bg-ink text-lime" : "bg-paper-2 text-ink-2"}`}>
          {STATE_TEXT[run.state] ?? run.state}</span>
        <span className="num text-[13px] text-ink-2">{run.videos} {run.videos === 1 ? "video" : "videos"}</span>
        {run.satisfaction != null && <span className="num text-[13px] text-ink-2">rated {run.satisfaction}/10</span>}
        <ChevronDown size={16} className={`ml-auto text-ink-2 transition ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <RunVideos run={run} slug={slug} live={live} freshIds={freshIds} />}
    </div>
  );
}

function RunVideos({ run, slug, live, freshIds }: { run: RunSummary; slug: string; live: boolean; freshIds: Set<string> }) {
  const videos = useRunVideos(run.id, live);
  const rate = useRateVideo(run.id);
  const [opened, setOpened] = useState<FoundVideo | null>(null);
  const list = videos.data ?? [];
  const ended = ["review_ready", "stopped", "failed"].includes(run.state);
  const current = opened && (list.find((v) => v.id === opened.id) ?? opened);
  return (
    <div className="px-5 pb-6">
      {videos.isSuccess && list.length === 0 && (
        <p className="py-8 text-center text-[13px] text-ink-2">{live ? "Nothing saved yet. Watch the agents above."
          : "This run didn't save any videos. Try other platforms, a longer time limit or a few target videos."}</p>
      )}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-6">
        {list.map((v) => (
          <VideoCard key={v.id} video={v} fresh={freshIds.has(v.id)} onOpen={setOpened}
            onRate={(rating, note) => v.cluster_id && rate.mutate({ clusterId: v.cluster_id, rating, note })} />
        ))}
      </div>
      {rate.isError && <p role="alert" className="mt-3 text-[12.5px] text-bad">Couldn't save that rating: {(rate.error as Error).message}</p>}
      {ended && list.length > 0 && <RunScore run={run} slug={slug} />}
      <VideoSheet video={current} onClose={() => setOpened(null)} />
    </div>
  );
}

function RunScore({ run, slug }: { run: RunSummary; slug: string }) {
  const score = useScoreRun(slug);
  const [value, setValue] = useState<number | null>(run.satisfaction);
  const [note, setNote] = useState("");
  return (
    <form className="mt-6 flex flex-wrap items-center gap-3 border-t border-rule pt-5"
      onSubmit={(e) => { e.preventDefault(); if (value) score.mutate({ runId: run.id, satisfaction: value, note: note.trim() || null }); }}>
      <p className="text-[13.5px] font-medium">How useful was this run?</p>
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Score from 1 to 10">
        {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
          <button key={n} type="button" role="radio" aria-checked={value === n} onClick={() => setValue(n)}
            className={`num h-8 w-8 rounded-lg text-[13px] max-sm:h-7 max-sm:w-7 transition ${value === n ? "bg-ink text-lime" : "bg-paper-2 text-ink-2 hover:text-ink"}`}>{n}</button>
        ))}
      </div>
      <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} aria-label="Note for this run"
        placeholder="Optional note for the agents" className="h-9 min-w-[220px] flex-1 rounded-lg border border-rule bg-white px-3 text-[13px] outline-none focus:border-ink" />
      <button disabled={!value || score.isPending} className="h-9 rounded-lg bg-ink px-4 text-[13px] font-medium text-white disabled:opacity-40">
        {score.isSuccess ? "Saved" : score.isPending ? "Saving…" : "Save score"}</button>
      {score.isError && <p role="alert" className="w-full text-[12.5px] text-bad">{(score.error as Error).message}</p>}
    </form>
  );
}
