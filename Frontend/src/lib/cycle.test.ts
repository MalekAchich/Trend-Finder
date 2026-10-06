import { afterEach, describe, expect, it, vi } from "vitest";
import { createCycle } from "./cycle";

describe("logo cycle", () => {
  afterEach(() => vi.useRealTimers());

  it("ticks only while running and stops cleanly", () => {
    vi.useFakeTimers();
    let ticks = 0;
    const c = createCycle(3000, () => ticks++);
    c.set(false);
    vi.advanceTimersByTime(10_000);
    expect(ticks).toBe(0);
    c.set(true);
    vi.advanceTimersByTime(9_100);
    expect(ticks).toBe(3);
    c.set(false);
    vi.advanceTimersByTime(10_000);
    expect(ticks).toBe(3);
    c.set(true);
    c.dispose();
    vi.advanceTimersByTime(10_000);
    expect(ticks).toBe(3);
  });
});
