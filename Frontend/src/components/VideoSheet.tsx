import { ExternalLink, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { FoundVideo } from "../api/types";
import { PLATFORM_LABEL, age, formatViews } from "./ui";

const SCORES: { key: keyof FoundVideo["scores"]; label: string }[] = [
  { key: "fit", label: "Fits the character" }, { key: "feasibility", label: "Easy to recreate" },
  { key: "momentum", label: "Growing fast" }, { key: "freshness", label: "Still fresh" },
];

function player(v: FoundVideo): string | null {
  if (v.platform === "youtube") return `https://www.youtube.com/embed/${v.platform_id}?autoplay=1&mute=1&playsinline=1`;
  if (v.platform === "tiktok") return `https://www.tiktok.com/player/v1/${v.platform_id}?autoplay=1&muted=1`;
  return null;
}

export function VideoSheet({ video, onClose, children }: { video: FoundVideo | null; onClose: () => void;
  children?: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (video && !d.open) d.showModal();
    if (!video && d.open) d.close();
  }, [video]);
  const src = video ? player(video) : null;
  return (
    <dialog ref={ref} onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}
      className="m-auto max-h-[92dvh] w-[min(980px,94vw)] overflow-hidden rounded-2xl bg-paper p-0 text-ink shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-sm">
      {video && (
        <div className="grid max-h-[92dvh] md:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
          <div className="bg-black">
            {src
              ? <iframe src={src} title="Video" allow="autoplay; encrypted-media; fullscreen" className="aspect-[9/16] h-full max-h-[92dvh] w-full border-0" />
              : video.thumbnail_url && <img src={video.thumbnail_url} alt="" className="aspect-[9/16] w-full object-cover" />}
          </div>
          <div className="overflow-y-auto p-6">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-[12.5px] text-ink-2">{PLATFORM_LABEL[video.platform]}{video.creator ? `, @${video.creator}` : ""}</p>
                <h2 className="mt-1 text-[20px] font-semibold leading-snug">{video.caption || "Untitled video"}</h2>
                <p className="num mt-1 text-[12.5px] text-ink-2">
                  {[formatViews(video.views) && `${formatViews(video.views)} views`, age(video.posted_at),
                    video.duration_s && `${Math.round(video.duration_s)}s long`].filter(Boolean).join(", ")}</p>
              </div>
              <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-ink-2 hover:bg-paper-2"><X size={18} /></button>
            </div>
            <div className="mt-5 flex items-end gap-3">
              <p className="num font-heading text-[44px] font-semibold leading-none">{video.score == null ? "–" : Math.round(video.score)}</p>
              <p className="pb-1 text-[12.5px] text-ink-2">match out of 100</p>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3">
              {SCORES.map(({ key, label }) => {
                const v = video.scores[key];
                return (
                  <div key={key}>
                    <div className="flex justify-between text-[12.5px]"><span className="text-ink-2">{label}</span>
                      <span className="num font-medium">{v == null ? "–" : Math.round(v)}</span></div>
                    <div className="mt-1 h-1.5 rounded-full bg-paper-2">
                      <div className="h-1.5 rounded-full bg-ink" style={{ width: `${Math.max(0, Math.min(v ?? 0, 100))}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
            {video.why && <Block title="Why it fits">{video.why}</Block>}
            {video.adaptation && <Block title="How the character could do it">{video.adaptation}</Block>}
            {video.best_segment && (
              <Block title="Best part to copy">
                <span className="num">{video.best_segment.start_s.toFixed(1)}s to {video.best_segment.end_s.toFixed(1)}s</span></Block>
            )}
            {video.watch_out && <Block title="Watch out for">{video.watch_out}</Block>}
            {video.niche_guess && <Block title="Niche">{video.niche_guess}</Block>}
            {video.trend_type && <Block title="Trend type">{video.trend_type}</Block>}
            {video.audio_use && <Block title="Audio">{video.audio_use}</Block>}
            {(video.tags ?? []).length > 0 && <Block title="Tags">{video.tags.map((t) => `#${t}`).join("  ")}</Block>}
            <a href={video.url} target="_blank" rel="noreferrer"
              className="mt-6 inline-flex items-center gap-1.5 text-[13px] font-medium underline underline-offset-4">
              Open the original on {PLATFORM_LABEL[video.platform]} <ExternalLink size={13} /></a>
            {children && <div className="mt-6 border-t border-rule pt-4">{children}</div>}
          </div>
        </div>
      )}
    </dialog>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-5">
      <h3 className="text-[13px] font-semibold">{title}</h3>
      <p className="mt-1 text-[13.5px] leading-relaxed text-ink/85">{children}</p>
    </section>
  );
}
