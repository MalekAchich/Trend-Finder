import { useEffect, useMemo, useState } from "react";
import { useCharacterVideos, useManualVideos, useSocialActions } from "../../api/hooks";
import type { SocialPost } from "../../api/types";
import { createHoverIntent } from "../../lib/hover";
import { ago, compact, formatDelta, percent } from "../../lib/socials";
import { DownloadButtons } from "../DownloadButtons";
import { HoverPreview } from "../HoverPreview";
import { PLATFORM_LABEL, PlatformIcon } from "../ui";

/** One of our posts: its numbers (unknown ones as "–"), its change in a day, and what it recreates. */
export function PostCard({ post, number, slug }: { post: SocialPost; number: number | undefined; slug: string }) {
  const [previewing, setPreviewing] = useState(false);
  const hover = useMemo(() => createHoverIntent(400, () => setPreviewing(true), () => setPreviewing(false)), []);
  useEffect(() => () => hover.leave(), [hover]);
  const day = formatDelta(post.views_24h);
  const stats: [string, string][] = [
    ["Likes", compact(post.likes)], ["Comments", compact(post.comments)], ["Shares", compact(post.shares)],
    ["Saves", compact(post.saves)], ["Reach", compact(post.reach)], ["Engagement", percent(post.engagement)],
  ];
  return (
    <article className="flex flex-col" data-post-id={post.id}>
      <a href={post.url} target="_blank" rel="noreferrer" onMouseEnter={hover.enter} onMouseLeave={hover.leave}
        className="relative block aspect-[9/16] w-full overflow-hidden rounded-[10px] bg-paper-2 text-white shadow-[0_1px_0_var(--color-rule),0_10px_30px_-18px_oklch(0_0_0/.5)]">
        {post.thumbnail_url
          ? <img src={post.thumbnail_url} alt="" loading="lazy" className="h-full w-full object-cover" />
          : <span className="grid h-full place-items-center text-ink-2"><PlatformIcon platform={post.platform} size={26} /></span>}
        {previewing && <HoverPreview platform={post.platform} platformId={post.platform_id} canonicalId={post.canonical_id} />}
        <span className="absolute left-2.5 top-2.5 flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-1 text-[10.5px] font-semibold backdrop-blur">
          <PlatformIcon platform={post.platform} size={11} />{PLATFORM_LABEL[post.platform]}{number ? ` · #${number}` : ""}</span>
        {!previewing && (
          <span className="absolute inset-0 flex flex-col justify-end bg-[linear-gradient(transparent_45%,oklch(0_0_0/.85))] p-3">
            <span className="num flex items-baseline gap-1.5"><span className="text-[22px] font-semibold">{compact(post.views)}</span>
              <span className="text-[11px] opacity-80">views</span>
              {day && day !== "0" && <span className="text-[11px] text-lime" title="in the last 24 hours">{day}</span>}</span>
            <span className="mt-1 line-clamp-2 text-[12px] leading-snug">{post.format ?? post.caption ?? ""}</span>
            <span className="mt-1 text-[10.5px] opacity-70">posted {ago(post.posted_at)}</span>
          </span>
        )}
      </a>
      <dl className="num mt-2 grid grid-cols-3 gap-x-2 gap-y-1 text-[11.5px]">
        {stats.map(([k, v]) => (
          <div key={k}><dt className="text-ink-2">{k}</dt><dd className="font-medium text-ink">{v}</dd></div>
        ))}
        {post.avg_watch_s != null && (
          <div className="col-span-3"><dt className="inline text-ink-2">Avg watch </dt>
            <dd className="inline font-medium text-ink">{post.avg_watch_s.toFixed(1)} s
              {post.watch_through != null && <> ({percent(post.watch_through, 0)} of it)</>}</dd></div>
        )}
      </dl>
      <RecreatedFrom post={post} slug={slug} />
      <div className="mt-2">{post.canonical_id && <DownloadButtons canonicalId={post.canonical_id} />}</div>
    </article>
  );
}

function RecreatedFrom({ post, slug }: { post: SocialPost; slug: string }) {
  const { link } = useSocialActions();
  const found = useCharacterVideos(slug, null);
  const manual = useManualVideos(slug);
  const options = useMemo(() => {
    const seen = new Set<string>();
    const out: { id: string; label: string }[] = [];
    for (const v of manual.data?.items ?? []) {
      if (v.canonical_id && !seen.has(v.canonical_id)) {
        seen.add(v.canonical_id);
        out.push({ id: v.canonical_id, label: `Your pick · @${v.creator ?? "?"} · ${(v.study?.format ?? v.caption ?? "").slice(0, 40)}` });
      }
    }
    for (const v of found.data ?? []) {
      if (!seen.has(v.canonical_id)) {
        seen.add(v.canonical_id);
        out.push({ id: v.canonical_id, label: `Found · @${v.creator ?? "?"} · ${(v.why ?? v.caption ?? "").slice(0, 40)}` });
      }
    }
    return out;
  }, [found.data, manual.data]);
  return (
    <label className="mt-2 block text-[11.5px] text-ink-2">
      Recreated from
      <select value={post.inspired_by ?? ""} disabled={link.isPending}
        onChange={(e) => link.mutate({ id: post.id, inspired_by: e.target.value || null })}
        className={`mt-0.5 h-7 w-full rounded-md px-1.5 text-[11.5px] outline-none ${post.inspired_by ? "bg-lime text-night" : "bg-paper-2 text-ink-2"}`}>
        <option value="">Not linked</option>
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    </label>
  );
}
