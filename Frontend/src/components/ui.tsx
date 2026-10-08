import type { Platform } from "../api/types";
import { BrandLogo } from "./brand";

export const PLATFORM_LABEL: Record<Platform, string> = { tiktok: "TikTok", instagram: "Instagram", youtube: "YouTube", x: "X" };

export function PlatformIcon({ platform, size = 12 }: { platform: Platform | string | null; size?: number }) {
  return <BrandLogo platform={platform} size={size} />;
}

export const ROLE_LABEL: Record<string, string> = {
  lead: "Lead agent", reader: "Reader", learner: "Learner", scout: "Scout", radar: "Trend radar",
  deep_dive: "Deep dive", analyst: "Analyst", seed_study: "Trend study", look_check: "Look check", owner: "You",
};

export const ROLE_TINT: Record<string, string> = {
  lead: "oklch(0.938 0.203 119)", reader: "oklch(0.85 0.1 300)", learner: "oklch(0.85 0.1 300)",
  scout: "oklch(0.82 0.12 210)", radar: "oklch(0.84 0.13 170)", deep_dive: "oklch(0.8 0.13 250)",
  analyst: "oklch(0.86 0.13 75)", seed_study: "oklch(0.83 0.12 330)", look_check: "oklch(0.86 0.12 30)",
  owner: "oklch(0.95 0 0)",
};

export const STATE_TEXT: Record<string, string> = {
  created: "Starting", reading_character: "Reading the character", studying_trends: "Studying your trend videos",
  discovering: "Finding videos like your references",
  planning: "Planning", running: "Searching", paused_usage: "Paused for usage limits", curating: "Ranking",
  review_ready: "Done", stopped: "Stopped", failed: "Failed", interrupted: "Interrupted",
};

export const formatViews = (n: number | null | undefined) =>
  n == null ? null : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n);

export function age(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const h = (Date.now() - Date.parse(iso)) / 36e5;
  if (h < 1) return "just posted";
  if (h < 48) return `${Math.round(h)}h old`;
  return `${Math.round(h / 24)} days old`;
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    return (u.hostname.replace(/^www\./, "") + u.pathname).replace(/\/$/, "");
  } catch {
    return url;
  }
}

export function platformOf(url: string): Platform | null {
  if (/tiktok\.com/i.test(url)) return "tiktok";
  if (/instagram\.com/i.test(url)) return "instagram";
  if (/youtube\.com|youtu\.be/i.test(url)) return "youtube";
  if (/^https?:\/\/(www\.|mobile\.)?(x|twitter)\.com\//i.test(url)) return "x";
  return null;
}
