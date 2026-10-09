/** Numbers for the Socials page: unknown is "–", never 0 (a platform that doesn't give a number didn't say 0). */

export function compact(n: number | null | undefined): string {
  if (n == null) return "–";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (Math.abs(n) >= 10_000) return `${Math.round(n / 1000)}k`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(Math.round(n));
}

/** "+1.2k", "−40", "0" or null when unknown. */
export function formatDelta(n: number | null | undefined): string | null {
  if (n == null) return null;
  if (n === 0) return "0";
  return `${n > 0 ? "+" : "−"}${compact(Math.abs(n))}`;
}

export function percent(ratio: number | null | undefined, digits = 1): string {
  return ratio == null ? "–" : `${(ratio * 100).toFixed(digits).replace(/\.0$/, "")}%`;
}

/** (likes + comments + shares + saves) / views, from whatever the platform gave. */
export function engagementRate(p: { views: number | null; likes: number | null; comments: number | null;
  shares: number | null; saves: number | null }): number | null {
  if (!p.views) return null;
  return ((p.likes ?? 0) + (p.comments ?? 0) + (p.shares ?? 0) + (p.saves ?? 0)) / p.views;
}

/** Average watch / length: how much of the video people watch. */
export function watchThrough(avgWatch: number | null | undefined, duration: number | null | undefined): number | null {
  return avgWatch != null && duration ? Math.min(avgWatch / duration, 1) : null;
}

export function hoursSince(iso: string | null | undefined, now = Date.now()): number | null {
  return iso ? Math.max((now - new Date(iso).getTime()) / 3_600_000, 0) : null;
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  const h = hoursSince(iso, now);
  if (h == null) return "never";
  if (h < 1 / 60) return "just now";
  if (h < 1) return `${Math.round(h * 60)} min ago`;
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

/** #1 is the channel's first post: the same number on its card and in the chart's legend. */
export function postNumbers(posts: { id: string; posted_at: string | null }[]): Map<string, number> {
  const order = [...posts].filter((p) => p.posted_at).sort((a, b) => Date.parse(a.posted_at!) - Date.parse(b.posted_at!));
  return new Map(order.map((p, i) => [p.id, i + 1]));
}
