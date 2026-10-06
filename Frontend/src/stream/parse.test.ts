import { describe, expect, it } from "vitest";
import { endsStream, parseEvent } from "./parse";

describe("parseEvent", () => {
  it("reads server events and ignores the browser's own connection-error events", () => {
    const e = { id: 3, type: "error", payload: { message: "x" }, created_at: "2026-10-06T00:00:00Z" };
    expect(parseEvent({ data: JSON.stringify(e) } as MessageEvent)).toEqual(e);
    expect(parseEvent(new Event("error") as unknown as MessageEvent)).toBeNull();
    expect(parseEvent({ data: "not json" } as MessageEvent)).toBeNull();
  });
});

describe("endsStream", () => {
  it("closes the live connection once the run has finished (review #7: no endless reconnects)", () => {
    const ev = (state: string) => ({ id: 1, type: "run.state", payload: { state }, created_at: "" });
    expect(endsStream(ev("review_ready"))).toBe(true);
    expect(endsStream(ev("stopped"))).toBe(true);
    expect(endsStream(ev("curating"))).toBe(false);
    expect(endsStream({ ...ev("failed"), type: "agent.thought" })).toBe(false);
  });
});
