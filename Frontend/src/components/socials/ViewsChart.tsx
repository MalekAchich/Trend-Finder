import { useQueries } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { SocialPost, SocialSnapshot } from "../../api/types";
import { compact, postNumbers } from "../../lib/socials";

// the validated categorical order (dataviz reference palette, light surface): assigned by post, never by rank
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
const W = 760, H = 220, PAD = { l: 44, r: 56, t: 12, b: 26 };
const MAX_SERIES = SERIES.length;

interface Series { post: SocialPost; color: string; label: string; points: [number, number][] }

/** Views over each post's life: x = hours since it was posted, so a new post compares with the others at the same age. */
export function ViewsChart({ posts }: { posts: SocialPost[] }) {
  const shown = useMemo(() => posts.filter((p) => p.posted_at)
    .sort((a, b) => Date.parse(a.posted_at!) - Date.parse(b.posted_at!)).slice(-MAX_SERIES), [posts]);
  const histories = useQueries({ queries: shown.map((p) => ({
    queryKey: ["social-history", p.id], queryFn: () => api.get<SocialSnapshot[]>(`/api/socials/posts/${p.id}/history`) })) });
  const numbers = postNumbers(posts);
  const series: Series[] = shown.map((p, i) => ({
    post: p, color: SERIES[i], label: `#${numbers.get(p.id)} ${(p.format ?? p.caption ?? "post").slice(0, 32)}`,
    points: (histories[i]?.data ?? []).filter((s) => s.views != null)
      .map((s) => [(Date.parse(s.taken_at) - Date.parse(p.posted_at!)) / 3_600_000, s.views!] as [number, number]),
  })).filter((s) => s.points.length);
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);

  if (!series.length) {
    return <p className="rounded-xl border border-dashed border-rule px-4 py-10 text-center text-[13px] text-ink-2">
      Views over time appear after the second sync (every 3 hours).</p>;
  }
  const maxX = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p[0])));
  const maxY = niceMax(Math.max(1, ...series.flatMap((s) => s.points.map((p) => p[1]))));
  const x = (h: number) => PAD.l + (h / maxX) * (W - PAD.l - PAD.r);
  const y = (v: number) => H - PAD.b - (v / maxY) * (H - PAD.t - PAD.b);
  const at = (s: Series, h: number) => [...s.points].reverse().find((p) => p[0] <= h)?.[1] ?? null;
  const move = (e: React.PointerEvent) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box) return;
    const px = ((e.clientX - box.left) / box.width) * W;
    setHover(Math.min(Math.max((px - PAD.l) / (W - PAD.l - PAD.r), 0), 1) * maxX);
  };
  const rows = hover == null ? [] : series.map((s) => ({ s, v: at(s, hover) })).filter((r) => r.v != null)
    .sort((a, b) => b.v! - a.v!);

  return (
    <figure className="rounded-2xl bg-card p-4 shadow-[0_1px_0_var(--color-rule)]">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[13.5px] font-semibold text-ink">Views by age of the post</span>
        <span className="text-[12px] text-ink-2">{hover == null ? "Hover to compare posts at the same age"
          : `${hoursLabel(hover)} after posting`}</span>
      </figcaption>
      <div className="relative mt-2">
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block w-full" role="img"
          aria-label="Views over time for each post" onPointerMove={move} onPointerLeave={() => setHover(null)}>
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(maxY * t)} y2={y(maxY * t)} stroke="var(--color-rule)" />
              <text x={PAD.l - 8} y={y(maxY * t) + 4} textAnchor="end" className="num fill-[var(--color-ink-2)] text-[11px]">{compact(maxY * t)}</text>
            </g>
          ))}
          {[0, 0.5, 1].map((t) => (
            <text key={t} x={x(maxX * t)} y={H - 6} textAnchor={t === 0 ? "start" : t === 1 ? "end" : "middle"}
              className="num fill-[var(--color-ink-2)] text-[11px]">{hoursLabel(maxX * t)}</text>
          ))}
          {series.map((s) => {
            const d = s.points.map(([h, v], i) => `${i ? "L" : "M"}${x(h).toFixed(1)},${y(v).toFixed(1)}`).join("");
            const [lh, lv] = s.points[s.points.length - 1];
            return (
              <g key={s.post.id}>
                <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                <circle cx={x(lh)} cy={y(lv)} r={4} fill={s.color} stroke="var(--color-card)" strokeWidth={2} />
                <text x={x(lh) + 7} y={y(lv) + 4} className="num fill-[var(--color-ink)] text-[11px] font-medium">{compact(lv)}</text>
              </g>
            );
          })}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b} stroke="var(--color-ink-2)" strokeWidth={1} strokeDasharray="3 3" />}
        </svg>
        {rows.length > 0 && (
          <div className="pointer-events-none absolute top-2 rounded-lg bg-ink px-3 py-2 text-[12px] text-white shadow-lg"
            style={{ left: `${Math.min((x(hover!) / W) * 100, 70)}%` }}>
            {rows.map(({ s, v }) => (
              <div key={s.post.id} className="flex items-center gap-2 whitespace-nowrap">
                <span className="h-0.5 w-3 rounded" style={{ background: s.color }} />
                <span className="num font-semibold">{compact(v)}</span>
                <span className="text-white/70">{s.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-ink-2" aria-label="Posts">
        {series.map((s) => (
          <li key={s.post.id} className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded" style={{ background: s.color }} />{s.label}</li>
        ))}
      </ul>
    </figure>
  );
}

function niceMax(v: number): number {
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((n) => n >= v) ?? v;
}

function hoursLabel(h: number): string {
  return h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`;
}
