import type { Platform } from "../api/types";

const SAFE_ID = /^[A-Za-z0-9_-]{4,40}$/;

/** The platform's own player, muted and looping, for hover previews. Instagram embeds can't autoplay. */
export function embedUrl(platform: Platform, id: string): string | null {
  if (!SAFE_ID.test(id)) return null;
  if (platform === "youtube") {
    return `https://www.youtube.com/embed/${id}?autoplay=1&mute=1&controls=0&loop=1&playlist=${id}&playsinline=1`;
  }
  if (platform === "tiktok") {
    return `https://www.tiktok.com/player/v1/${id}?autoplay=1&muted=1&controls=0&loop=1&progress_bar=0&description=0&music_info=0`;
  }
  return null;
}
