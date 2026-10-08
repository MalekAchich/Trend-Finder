import { MessageSquareText, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FoundVideo, Rating } from "../api/types";
import { createHoverIntent } from "../lib/hover";
import { DownloadButtons } from "./DownloadButtons";
import { HoverPreview } from "./HoverPreview";
import { PLATFORM_LABEL, PlatformIcon, age, formatViews } from "./ui";

interface Props {
  video: FoundVideo;
  fresh?: boolean;
  onOpen: (v: FoundVideo) => void;
  onRate: (rating: Rating | null, note: string | null) => void;
}

export function VideoCard({ video, fresh, onOpen, onRate }: Props) {
  const [previewing, setPreviewing] = useState(false);
  const hover = useMemo(() => createHoverIntent(400, () => setPreviewing(true), () => setPreviewing(false)), []);
  useEffect(() => () => hover.leave(), [hover]);
  const rated = video.feedback?.rating ?? null;
  const rateable = !!video.cluster_id;

  return (
    <article className={`group flex flex-col ${fresh ? "card-enter" : ""}`} data-video-id={video.id}>
      <button onMouseEnter={hover.enter} onMouseLeave={hover.leave} onFocus={hover.enter} onBlur={hover.leave}
        onClick={() => onOpen(video)} aria-label={`Open ${video.caption ?? video.url}`}
        className="relative block aspect-[9/16] w-full overflow-hidden rounded-[10px] bg-paper-2 text-left text-white shadow-[0_1px_0_var(--color-rule),0_10px_30px_-18px_oklch(0_0_0/.5)]">
        {video.thumbnail_url
          ? <img src={video.thumbnail_url} alt="" loading="lazy" className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.035]" />
          : <span className="grid h-full place-items-center text-[12px] text-ink-2">No preview</span>}
        {previewing && <HoverPreview platform={video.platform} platformId={video.platform_id} canonicalId={video.canonical_id} />}
        <span className="absolute left-2.5 top-2.5 flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-1 text-[10.5px] font-semibold backdrop-blur">
          <PlatformIcon platform={video.platform} size={11} />{PLATFORM_LABEL[video.platform]}
        </span>
        {video.score != null && (
          <span className="num absolute right-2.5 top-2.5 rounded-md bg-white px-1.5 py-1 text-[11px] font-semibold text-ink">{Math.round(video.score)}</span>
        )}
        {!previewing && (
          <span className="absolute inset-0 flex flex-col justify-end bg-[linear-gradient(transparent_45%,oklch(0_0_0/.82))] p-3">
            <span className="line-clamp-2 text-[12px] font-medium leading-snug">{video.why ?? video.caption ?? ""}</span>
            <span className="mt-1.5 flex justify-between text-[10.5px] opacity-70">
              <span>{video.creator ? `@${video.creator}` : ""}</span>
              <span className="num">{[formatViews(video.views) && `${formatViews(video.views)} views`, age(video.posted_at)].filter(Boolean).join(", ")}</span>
            </span>
          </span>
        )}
        {video.source === "owner" && <span className="absolute bottom-2.5 left-2.5 rounded bg-lime px-1.5 py-0.5 text-[10px] font-semibold text-night">Your pick</span>}
      </button>
      <Feedback rated={rated} note={video.feedback?.note ?? null} rateable={rateable} onRate={onRate} />
      <div className="mt-1.5"><DownloadButtons canonicalId={video.canonical_id} /></div>
    </article>
  );
}

export function Feedback({ rated, note, rateable, onRate }: { rated: Rating | null; note: string | null; rateable: boolean;
  onRate: (r: Rating | null, n: string | null) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note ?? "");
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => setDraft(note ?? ""), [note]);
  useEffect(() => { if (editing) area.current?.focus(); }, [editing]);
  if (!rateable) return <p className="mt-2 text-[11.5px] text-ink-2">You can rate it once the run has ranked its videos.</p>;
  const tap = (r: Rating) => onRate(rated === r ? null : r, draft.trim() || null);
  return (
    <div className="mt-2">
      <div className="flex items-center gap-1">
        <button aria-label="I like it" aria-pressed={rated === "up"} onClick={() => tap("up")}
          className={`grid h-8 w-8 place-items-center rounded-lg transition ${rated === "up" ? "bg-ink text-lime" : "text-ink-2 hover:bg-paper-2"}`}>
          <ThumbsUp size={15} /></button>
        <button aria-label="Not for this character" aria-pressed={rated === "down"} onClick={() => tap("down")}
          className={`grid h-8 w-8 place-items-center rounded-lg transition ${rated === "down" ? "bg-ink text-white" : "text-ink-2 hover:bg-paper-2"}`}>
          <ThumbsDown size={15} /></button>
        <button onClick={() => setEditing((e) => !e)} aria-expanded={editing}
          className="ml-auto inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[12px] text-ink-2 hover:bg-paper-2">
          <MessageSquareText size={13} /> {note ? "Edit note" : "Add note"}</button>
      </div>
      {!editing && note && <p className="mt-1 line-clamp-2 text-[12px] text-ink-2">“{note}”</p>}
      {editing && (
        <form className="mt-1.5" onSubmit={(e) => { e.preventDefault(); onRate(rated, draft.trim() || null); setEditing(false); }}>
          <textarea ref={area} value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={1000} rows={3}
            placeholder="What works or doesn't for this character" aria-label="Note"
            className="w-full resize-none rounded-lg border border-rule bg-white p-2 text-[12.5px] text-ink outline-none focus:border-ink" />
          <div className="mt-1 flex justify-end gap-2">
            <button type="button" className="text-[12px] text-ink-2" onClick={() => { setDraft(note ?? ""); setEditing(false); }}>Cancel</button>
            <button disabled={!rated} className="rounded-md bg-ink px-2.5 py-1 text-[12px] font-medium text-white disabled:opacity-40">Save note</button>
          </div>
          {!rated && <p className="mt-1 text-[11.5px] text-ink-2">Pick 👍 or 👎 first; the note is saved with it.</p>}
        </form>
      )}
    </div>
  );
}
