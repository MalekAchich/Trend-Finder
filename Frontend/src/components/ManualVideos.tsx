import { AlertTriangle, ExternalLink, Loader2, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useManualActions, useManualVideos } from "../api/hooks";
import type { Character, ManualVideo } from "../api/types";
import { createHoverIntent } from "../lib/hover";
import { DownloadButtons } from "./DownloadButtons";
import { HoverPreview } from "./HoverPreview";
import { PLATFORM_LABEL, PlatformIcon, formatViews } from "./ui";

type Filter = "all" | "references" | "targets";

/** The owner's manually chosen videos: references (intel for the agents), targets, or both. */
export function ManualPanel({ characters, slug }: { characters: Character[]; slug: string | null }) {
  const list = useManualVideos(slug);
  const [filter, setFilter] = useState<Filter>("all");
  const items = list.data?.items ?? [];
  const shown = items.filter((v) => filter === "all" || (filter === "references" ? v.is_reference : !!v.target));
  const counts = { all: items.length, references: items.filter((v) => v.is_reference).length,
    targets: items.filter((v) => v.target).length };
  const name = characters.find((c) => c.slug === slug)?.name;
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-2" role="group" aria-label="Show">
        {(["all", "references", "targets"] as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)} aria-pressed={filter === f}
            className={`num h-8 rounded-full px-3.5 text-[12.5px] transition ${filter === f ? "bg-ink text-white" : "bg-paper-2 text-ink-2 hover:text-ink"}`}>
            {f === "all" ? "All" : f === "references" ? "References" : "Targets"} · {counts[f]}</button>
        ))}
        {name && <p className="ml-auto text-[12.5px] text-ink-2">Studies and targets shown for {name}</p>}
      </div>
      {list.isSuccess && items.length === 0 && (
        <div className="rounded-2xl border border-dashed border-rule px-6 py-14 text-center">
          <p className="text-[15px] font-medium">No manually chosen videos yet</p>
          <p className="mt-1 text-[13px] text-ink-2">Add links above, in Your reference videos. They're saved here for every run.</p>
        </div>
      )}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-6">
        {shown.map((v) => <ManualCard key={v.id} video={v} characters={characters} />)}
      </div>
    </div>
  );
}

function ManualCard({ video, characters }: { video: ManualVideo; characters: Character[] }) {
  const { setRoles, recheck, remove } = useManualActions();
  const [previewing, setPreviewing] = useState(false);
  const hover = useMemo(() => createHoverIntent(400, () => setPreviewing(true), () => setPreviewing(false)), []);
  useEffect(() => () => hover.leave(), [hover]);
  const st = video.study;
  const busy = setRoles.isPending || remove.isPending;
  const error = (setRoles.error ?? remove.error) as Error | null;
  const length = video.duration_s != null ? `${Math.round(video.duration_s)}s` : null;

  return (
    <article className="flex flex-col" data-manual-id={video.id}>
      <div onMouseEnter={hover.enter} onMouseLeave={hover.leave}
        className="relative aspect-[9/16] w-full overflow-hidden rounded-[10px] bg-paper-2 text-white shadow-[0_1px_0_var(--color-rule),0_10px_30px_-18px_oklch(0_0_0/.5)]">
        {video.thumbnail_url
          ? <img src={video.thumbnail_url} alt="" loading="lazy" className="h-full w-full object-cover" />
          : <span className="grid h-full place-items-center text-ink-2"><PlatformIcon platform={video.platform} size={26} /></span>}
        {previewing && video.status === "ready" && (
          <HoverPreview platform={video.platform} platformId={video.platform_id} canonicalId={video.canonical_id} />
        )}
        <span className="absolute left-2.5 top-2.5 flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-1 text-[10.5px] font-semibold backdrop-blur">
          <PlatformIcon platform={video.platform} size={11} />{PLATFORM_LABEL[video.platform]}
        </span>
        {st?.fit_score != null && (
          <span className="num absolute right-2.5 top-2.5 rounded-md bg-white px-1.5 py-1 text-[11px] font-semibold text-ink"
            title="How well it suits the character (0-10)">{st.fit_score}/10</span>
        )}
        {video.status === "checking" && (
          <span className="absolute inset-0 grid place-items-center bg-black/45 text-[12px]">
            <span className="flex items-center gap-1.5"><Loader2 size={14} className="animate-spin" /> Checking the link…</span></span>
        )}
        {video.status === "problem" && (
          <span className="absolute inset-0 flex flex-col justify-end gap-2 bg-black/70 p-3 text-[11.5px] leading-snug">
            <span className="flex items-start gap-1.5"><AlertTriangle size={14} className="mt-px shrink-0 text-warn" />
              <span>Can't open it: {video.problem}</span></span>
            <button onClick={() => recheck.mutate(video.id)} className="inline-flex w-fit items-center gap-1 rounded-md bg-white/15 px-2 py-1 hover:bg-white/25">
              <RotateCw size={12} /> Check again</button>
          </span>
        )}
        {video.status === "ready" && !previewing && (
          <span className="absolute inset-0 flex flex-col justify-end bg-[linear-gradient(transparent_50%,oklch(0_0_0/.82))] p-3">
            <span className="line-clamp-2 text-[12px] font-medium leading-snug">{st?.format ?? video.caption ?? ""}</span>
            <span className="mt-1.5 flex justify-between text-[10.5px] opacity-70">
              <span>{video.creator ? `@${video.creator}` : ""}</span>
              <span className="num">{[video.views != null ? `${formatViews(video.views)} views`
                : video.likes != null ? `${formatViews(video.likes)} likes` : null, length].filter(Boolean).join(", ")}</span>
            </span>
          </span>
        )}
      </div>

      <div className="mt-2 space-y-2">
        {st && (
          <div className="flex flex-wrap gap-1">
            {st.trend_type && <span className="rounded-md bg-ink px-1.5 py-0.5 text-[10.5px] font-medium text-lime">{st.trend_type}</span>}
            {st.niche && <span className="rounded-md bg-paper-2 px-1.5 py-0.5 text-[10.5px] text-ink">{st.niche}</span>}
            {(st.tags ?? []).slice(0, 4).map((t) => <span key={t} className="rounded-md px-1 py-0.5 text-[10.5px] text-ink-2">#{t}</span>)}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <button onClick={() => setRoles.mutate({ id: video.id, is_reference: !video.is_reference })}
            disabled={busy || (video.is_reference && !video.target)} aria-pressed={video.is_reference}
            title={video.is_reference && !video.target
              ? "Reference: the agents study it to learn the trend and find more like it. To stop using it, pick a target or remove it."
              : "Reference: the agents study it to learn the trend and find more like it (for every character)"}
            className={`h-7 rounded-md px-2 text-[11.5px] transition ${video.is_reference ? "bg-ink text-white" : "bg-paper-2 text-ink-2 hover:text-ink"}`}>
            Reference</button>
          <select value={video.target?.slug ?? ""} disabled={busy} aria-label="Target for a character"
            onChange={(e) => setRoles.mutate({ id: video.id, target: e.target.value || null })}
            className={`h-7 min-w-0 flex-1 rounded-md px-1.5 text-[11.5px] outline-none ${video.target ? "bg-lime text-night" : "bg-paper-2 text-ink-2"}`}>
            <option value="">Not a target</option>
            {characters.map((c) => <option key={c.slug} value={c.slug}>Target: {c.name}</option>)}
          </select>
        </div>
        {video.status === "ready" && video.canonical_id && <DownloadButtons canonicalId={video.canonical_id} />}
        {video.target && (
          <p className="text-[11.5px] text-ink-2">{video.target.state === "analysed"
            ? `Scored ${video.target.score != null ? Math.round(video.target.score) : "?"} for ${video.target.name}`
            : `Waiting for ${video.target.name}'s next run`}</p>
        )}
        <div className="flex items-center justify-between">
          <a href={video.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[11.5px] text-ink-2 hover:text-ink">
            Open original <ExternalLink size={11} /></a>
          <button onClick={() => remove.mutate(video.id)} disabled={busy} aria-label="Remove from your list"
            className="grid h-7 w-7 place-items-center rounded-md text-ink-2 hover:bg-paper-2 hover:text-bad"><Trash2 size={13} /></button>
        </div>
        {error && <p role="alert" className="text-[11.5px] text-bad">{error.message}</p>}
      </div>
    </article>
  );
}
