import { useState } from "react";
import type { Platform } from "../api/types";
import { embedUrl } from "../lib/embed";

/** Platforms whose embeds can't autoplay: the app relays the video itself while the cursor is on it. */
export const RELAYED: Platform[] = ["instagram", "x"];

/** Plays a video while hovered: the platform's own muted player (TikTok, YouTube) or the relayed video (Instagram, X).
 * Mounted only while previewing, so nothing loads before the hover. */
export function HoverPreview({ platform, platformId, canonicalId }: {
  platform: Platform; platformId: string | null; canonicalId: string | null;
}) {
  const [playing, setPlaying] = useState(false);
  if (RELAYED.includes(platform)) {
    if (!canonicalId) return null;
    return (
      <video src={`/api/preview/${encodeURIComponent(canonicalId)}`} autoPlay muted loop playsInline preload="auto"
        onPlaying={() => setPlaying(true)} aria-hidden
        className={`pointer-events-none absolute inset-0 h-full w-full object-cover transition-opacity duration-300 ${playing ? "opacity-100" : "opacity-0"}`} />
    );
  }
  const src = platformId ? embedUrl(platform, platformId) : null;
  if (!src) return null;
  return <iframe src={src} title="Preview" allow="autoplay; encrypted-media"
    className="pointer-events-none absolute inset-0 h-full w-full border-0 bg-black" />;
}
