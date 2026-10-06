import type { Rating } from "../api/types";

/** The editor's grease-pencil mark on a contact sheet: a circle for selects, a strike for passes. */
export function GreaseMark({ rating, size = "large" }: { rating: Rating; size?: "large" | "small" }) {
  const label = rating === "up" ? "select" : rating === "down" ? "pass" : "later";
  const big = size === "large";
  return (
    <div className="pointer-events-none absolute inset-0" aria-label={`Marked: ${label}`}>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full">
        {rating === "up" && (
          <path className="mark-stroke" style={{ ["--len" as string]: 330 }}
            d="M50 4 C82 3 97 22 96 50 C95 80 74 97 47 96 C19 95 3 76 4 49 C5 22 24 6 55 6"
            fill="none" stroke="var(--color-grease)" strokeWidth={big ? 1.6 : 3.5} strokeLinecap="round"
            vectorEffect="non-scaling-stroke" />
        )}
        {rating === "down" && (
          <>
            <path className="mark-stroke" style={{ ["--len" as string]: 140 }} d="M8 10 L92 90" fill="none"
              stroke="var(--color-grease)" strokeWidth={big ? 1.6 : 3.5} strokeLinecap="round"
              vectorEffect="non-scaling-stroke" />
            <path className="mark-stroke" style={{ ["--len" as string]: 140, animationDelay: ".12s" }}
              d="M92 12 L9 89" fill="none" stroke="var(--color-grease)" strokeWidth={big ? 1.6 : 3.5}
              strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>
      {big && (
        <span className="absolute right-3 top-2 rotate-[-6deg] font-marker text-3xl text-grease drop-shadow-sm">
          {label}
        </span>
      )}
      {!big && rating === "skip" && (
        <span className="absolute inset-x-0 bottom-0 bg-paper/80 text-center font-marker text-xs text-graphite">later</span>
      )}
    </div>
  );
}
