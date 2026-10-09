import { useEffect, useRef, useState } from "react";
import { compact } from "../../lib/socials";

/** A small one-series line chart in real pixels (text never scales with the width), with a hover readout.
 * `points` are [x, y] with x in milliseconds; one point is still drawn (as a dot with its value). */
export function MiniChart({ title, points, height = 120, empty }: { title: string; points: [number, number][];
  height?: number; empty: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const last = points[points.length - 1];
  const pad = { l: 36, r: 12, t: 10, b: 20 };
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), yMax = Math.max(niceTop(Math.max(1, ...ys)), 4);  // small counts: steps of whole numbers
  const x = (v: number) => pad.l + (x1 === x0 ? (width - pad.l - pad.r) : ((v - x0) / (x1 - x0)) * (width - pad.l - pad.r));
  const y = (v: number) => height - pad.b - (v / yMax) * (height - pad.t - pad.b);
  const near = hover == null ? null : points.reduce((a, b) => Math.abs(x(b[0]) - hover) < Math.abs(x(a[0]) - hover) ? b : a);
  return (
    <figure className="rounded-xl border border-line bg-panel/40 p-3">
      <figcaption className="flex items-baseline justify-between gap-2 text-[12px]">
        <span className="font-medium text-snow">{title}</span>
        <span className="num text-mist">{near ? `${compact(near[1])} · ${when(near[0])}` : last ? `${compact(last[1])} now` : ""}</span>
      </figcaption>
      <div ref={box} className="relative mt-1.5" style={{ height }}>
        {!points.length && <p className="grid h-full place-items-center text-[12px] text-mist">{empty}</p>}
        {points.length > 0 && width > 0 && (
          <svg width={width} height={height} role="img" aria-label={`${title}: ${compact(last[1])} now`}
            onPointerMove={(e) => setHover(e.clientX - e.currentTarget.getBoundingClientRect().left)}
            onPointerLeave={() => setHover(null)}>
            {[0, 0.5, 1].map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={y(yMax * t)} y2={y(yMax * t)} stroke="var(--color-line)" />
                <text x={pad.l - 6} y={y(yMax * t) + 4} textAnchor="end" fontSize={10.5} fill="var(--color-mist)" className="num">{compact(yMax * t)}</text>
              </g>
            ))}
            <text x={pad.l} y={height - 4} fontSize={10.5} fill="var(--color-mist)" className="num">{when(x0)}</text>
            {x1 !== x0 && <text x={width - pad.r} y={height - 4} textAnchor="end" fontSize={10.5} fill="var(--color-mist)" className="num">{when(x1)}</text>}
            {points.length > 1 && (
              <>
                <path d={`${line(points, x, y)}L${x(x1)},${y(0)}L${x(x0)},${y(0)}Z`} fill="var(--color-lime)" opacity={0.08} />
                <path d={line(points, x, y)} fill="none" stroke="var(--color-lime)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              </>
            )}
            <circle cx={x(last[0])} cy={y(last[1])} r={4} fill="var(--color-lime)" stroke="var(--color-night)" strokeWidth={2} />
            {near && (
              <>
                <line x1={x(near[0])} x2={x(near[0])} y1={pad.t} y2={height - pad.b} stroke="var(--color-mist)" strokeDasharray="3 3" />
                <circle cx={x(near[0])} cy={y(near[1])} r={4} fill="var(--color-snow)" />
              </>
            )}
          </svg>
        )}
      </div>
    </figure>
  );
}

function line(points: [number, number][], x: (v: number) => number, y: (v: number) => number): string {
  return points.map(([a, b], i) => `${i ? "L" : "M"}${x(a).toFixed(1)},${y(b).toFixed(1)}`).join("");
}

/** The top of the scale on a round number (1, 2 or 5 × 10ⁿ). */
export function niceTop(v: number): number {
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 5, 10].map((m) => m * p).find((n) => n >= v) ?? v;
}

function when(ms: number): string {
  const d = new Date(ms);
  const today = d.toDateString() === new Date().toDateString();
  return today ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
