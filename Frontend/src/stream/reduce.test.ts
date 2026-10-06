import { describe, expect, it } from "vitest";
import type { StreamEvent } from "../api/types";
import { applyEvents, filterEvents, groupOf, initialStream } from "./reduce";

let n = 0;
const ev = (type: string, payload: Record<string, unknown>, id = ++n): StreamEvent => ({
  id, type, payload, created_at: new Date(2026, 9, 6, 12, 0, id).toISOString(),
});
const scout = { id: "t1", role: "scout", platform: "tiktok" };

describe("stream reducer", () => {
  it("dedupes replayed events and keeps id order (Review Focus 3)", () => {
    const a = ev("agent.started", { agent: scout, goal: "find office dances" }, 1);
    const b = ev("agent.thought", { agent: scout, text: "Searching #deskdance first." }, 2);
    let s = applyEvents(initialStream(), [b]);
    s = applyEvents(s, [a, b]);
    s = applyEvents(s, [a]);
    expect(s.events.map((e) => e.id)).toEqual([1, 2]);
  });

  it("builds the roster with live status and current action", () => {
    let s = applyEvents(initialStream(), [
      ev("run.state", { state: "planning" }),
      ev("plan.created", { agent: { id: "lead", role: "lead", platform: null }, round: 1, reasoning: "go",
        tasks: [{ task_id: "t1", role: "scout", platform: "tiktok", goal: "find office dances" }], refused: [] }),
      ev("agent.started", { agent: scout, goal: "find office dances" }),
      ev("agent.tool_call", { agent: scout, tool: "tiktok_search", args: { query: "office dance" } }),
    ]);
    expect(s.agents.lead.status).toBe("done");
    expect(s.agents.t1).toMatchObject({ status: "working", role: "scout", platform: "tiktok" });
    expect(s.agents.t1.action).toBe("tiktok_search: office dance");
    expect(s.order[0]).toBe("lead");
    s = applyEvents(s, [ev("agent.finished", { agent: scout, accepted: 2, rejected: 0, leads: 1, failed: null })]);
    expect(s.agents.t1.status).toBe("done");
    s = applyEvents(s, [ev("agent.finished", { agent: { ...scout, id: "t2" }, accepted: 0, failed: "budget" })]);
    expect(s.agents.t2.status).toBe("failed");
  });

  it("shows reasoning summaries as plain text", () => {
    const s = applyEvents(initialStream(), [ev("agent.thought", { agent: scout, text: "**Checking gym trends**\n\nNext I look" })]);
    expect(s.agents.t1.action).toBe("Checking gym trends Next I look");
  });

  it("collects saved videos once each", () => {
    const video = { id: "f1", url: "u", score: 70 };
    const s = applyEvents(initialStream(), [
      ev("video.saved", { agent: { id: "analyst-1", role: "analyst" }, video }),
      ev("video.saved", { agent: { id: "analyst-1", role: "analyst" }, video }),
    ]);
    expect(s.saved.map((v) => v.id)).toEqual(["f1"]);
  });

  it("seeds the roster from the run's tasks", () => {
    const s = initialStream([{ id: "t9", role: "radar", platform: "youtube", status: "waiting", goal: "trends" }]);
    expect(s.agents.t9).toMatchObject({ status: "waiting", action: "trends" });
  });

  it("filters by agent, group and text", () => {
    const events = [
      ev("agent.thought", { agent: scout, text: "Checking gym trends" }),
      ev("agent.tool_call", { agent: scout, tool: "tiktok_search", args: { query: "gym" } }),
      ev("candidate.rejected", { agent: { id: "t2", role: "scout" }, reason: "already seen" }),
    ];
    expect(groupOf("agent.thought")).toBe("thinking");
    expect(groupOf("candidate.rejected")).toBe("problems");
    expect(filterEvents(events, { agent: "t1" })).toHaveLength(2);
    expect(filterEvents(events, { groups: new Set(["problems"]) })).toHaveLength(1);
    expect(filterEvents(events, { query: "GYM" })).toHaveLength(2);
  });
});
