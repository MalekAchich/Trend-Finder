import { describe, expect, it } from "vitest";
import { ago, compact, engagementRate, formatDelta, percent, watchThrough } from "./socials";

describe("socials numbers", () => {
  it("unknown is a dash, never zero", () => {
    expect(compact(null)).toBe("–");
    expect(percent(null)).toBe("–");
    expect(formatDelta(undefined)).toBeNull();
    expect(engagementRate({ views: null, likes: 4, comments: null, shares: null, saves: null })).toBeNull();
    expect(watchThrough(null, 12)).toBeNull();
  });
  it("formats counts, deltas and rates", () => {
    expect([compact(950), compact(1234), compact(12_345), compact(2_500_000)]).toEqual(["950", "1.2k", "12k", "2.5M"]);
    expect([formatDelta(1200), formatDelta(-40), formatDelta(0)]).toEqual(["+1.2k", "−40", "0"]);
    expect(engagementRate({ views: 1000, likes: 80, comments: 10, shares: 5, saves: 5 })).toBeCloseTo(0.1);
    expect(percent(0.1)).toBe("10%");
    expect(watchThrough(6, 12)).toBe(0.5);
    expect(watchThrough(20, 12)).toBe(1);  // replays can push the average past the length
  });
  it("says how long ago", () => {
    const now = Date.parse("2026-10-09T12:00:00Z");
    expect(ago("2026-10-09T11:30:00Z", now)).toBe("30 min ago");
    expect(ago("2026-10-08T12:00:00Z", now)).toBe("24 h ago");
    expect(ago(null, now)).toBe("never");
  });
});
