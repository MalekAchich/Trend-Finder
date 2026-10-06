import { afterEach, describe, expect, it, vi } from "vitest";
import { embedUrl } from "./embed";
import { createHoverIntent } from "./hover";

describe("embedUrl", () => {
  it("builds muted autoplay players for YouTube and TikTok, none for Instagram", () => {
    expect(embedUrl("youtube", "OUZbZ8cz4j8")).toBe(
      "https://www.youtube.com/embed/OUZbZ8cz4j8?autoplay=1&mute=1&controls=0&loop=1&playlist=OUZbZ8cz4j8&playsinline=1");
    expect(embedUrl("tiktok", "7400000000000000001")).toBe(
      "https://www.tiktok.com/player/v1/7400000000000000001?autoplay=1&muted=1&controls=0&loop=1&progress_bar=0&description=0&music_info=0");
    expect(embedUrl("instagram", "DTfu8CIDezV")).toBeNull();
    expect(embedUrl("youtube", "x\"><script>")).toBeNull();
  });
});

describe("hover intent", () => {
  afterEach(() => vi.useRealTimers());

  it("starts only after the delay and stops on leave", () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const h = createHoverIntent(400, () => calls.push("start"), () => calls.push("end"));
    h.enter();
    vi.advanceTimersByTime(300);
    h.leave();
    vi.advanceTimersByTime(500);
    expect(calls).toEqual(["end"]);
    h.enter();
    vi.advanceTimersByTime(400);
    expect(calls).toEqual(["end", "start"]);
    h.leave();
    expect(calls).toEqual(["end", "start", "end"]);
  });
});
