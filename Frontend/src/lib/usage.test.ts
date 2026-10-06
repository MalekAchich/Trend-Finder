import { describe, expect, it } from "vitest";
import { windowView } from "./usage";

const NOW = Date.parse("2026-10-06T21:53:00Z");

describe("windowView", () => {
  it("shows ChatGPT like its website: % left, remaining bar, relative reset", () => {
    const w = { name: "seven_day", used_percent: 17, window_minutes: 10080, resets_at: NOW / 1000 + (4 * 24 + 17) * 3600 };
    expect(windowView("chatgpt", w, NOW)).toEqual({ label: "Weekly limit", value: "83% left", bar: 83, reset: "Resets in 4d 17h" });
    const five = { name: "five_hour", used_percent: 0, window_minutes: 300, resets_at: NOW / 1000 + 5 * 3600 + 60 };
    expect(windowView("chatgpt", five, NOW)).toMatchObject({ value: "100% left", bar: 100, reset: "Resets in 5h 1m" });
  });

  it("shows Claude like its website: % used and the reset day", () => {
    const w = { name: "five_hour", used_percent: 74, window_minutes: 300, resets_at: null };
    expect(windowView("claude", w, NOW)).toMatchObject({ label: "Current session", value: "74% used", bar: 74 });
    expect(windowView("claude", { ...w, name: "seven_day" }, NOW).label).toBe("This week");
  });
});
