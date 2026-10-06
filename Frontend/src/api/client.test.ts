import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

describe("api client", () => {
  it("sends the client header on mutations only", async () => {
    const f = mockFetch(200, { ok: true });
    await api.post("/api/runs/1/stop");
    await api.get("/api/runs");
    const [postInit, getInit] = [f.mock.calls[0][1]!, f.mock.calls[1][1]!];
    expect(new Headers(postInit.headers).get("x-trendfinder-client")).toBe("web");
    expect(new Headers(getInit.headers).get("x-trendfinder-client")).toBeNull();
  });

  it("turns FastAPI errors into readable messages", async () => {
    mockFetch(409, { detail: "this run is running; ratings are accepted once it is ready for review" });
    await expect(api.post("/api/runs/1/feedback", {})).rejects.toThrowError(/ready for review/);
  });

  it("summarises validation errors", async () => {
    mockFetch(422, { detail: [{ loc: ["body", "weights"], msg: "weights must sum to 1" }] });
    const err = (await api.put("/api/settings", {}).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("weights: weights must sum to 1");
    expect(err.status).toBe(422);
  });
});
