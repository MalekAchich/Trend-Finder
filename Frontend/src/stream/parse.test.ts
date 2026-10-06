import { describe, expect, it } from "vitest";
import { parseEvent } from "./parse";

describe("parseEvent", () => {
  it("reads server events and ignores the browser's own connection-error events", () => {
    const e = { id: 3, type: "error", payload: { message: "x" }, created_at: "2026-10-06T00:00:00Z" };
    expect(parseEvent({ data: JSON.stringify(e) } as MessageEvent)).toEqual(e);
    expect(parseEvent(new Event("error") as unknown as MessageEvent)).toBeNull();
    expect(parseEvent({ data: "not json" } as MessageEvent)).toBeNull();
  });
});
