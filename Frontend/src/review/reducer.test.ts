import { describe, expect, it } from "vitest";
import { initialReview, keyToAction, reviewReducer } from "./reducer";

const ids = ["a", "b", "c"];

describe("review reducer", () => {
  it("moves with J/K and clamps at the ends", () => {
    let s = initialReview(ids);
    s = reviewReducer(s, { type: "prev" });
    expect(s.index).toBe(0);
    s = reviewReducer(s, { type: "next" });
    s = reviewReducer(s, { type: "next" });
    s = reviewReducer(s, { type: "next" });
    expect(s.index).toBe(2);
  });

  it("rating a card advances to the next unrated one", () => {
    let s = initialReview(ids);
    s = reviewReducer(s, { type: "rate", rating: "up" });
    expect(s.ratings.a.rating).toBe("up");
    expect(s.index).toBe(1);
    s = reviewReducer(s, { type: "rate", rating: "down" });
    s = reviewReducer(s, { type: "goto", index: 0 });
    s = reviewReducer(s, { type: "rate", rating: "skip" });
    expect(s.index).toBe(2); // jumps past the rated card b to the unrated c
  });

  it("re-rating replaces and notes are kept per card", () => {
    let s = initialReview(ids, { a: { rating: "up", note: "keep" } });
    expect(s.index).toBe(1); // opens on the first unrated card
    s = reviewReducer(s, { type: "goto", index: 0 });
    expect(s.drafts.a).toBe("keep");
    s = reviewReducer(s, { type: "note", note: "changed my mind" });
    s = reviewReducer(s, { type: "rate", rating: "down" });
    expect(s.ratings.a).toEqual({ rating: "down", note: "changed my mind" });
  });

  it("knows when everything is rated", () => {
    let s = initialReview(["a"]);
    expect(s.complete).toBe(false);
    s = reviewReducer(s, { type: "rate", rating: "up" });
    expect(s.complete).toBe(true);
  });

  it("maps keys but ignores them while typing", () => {
    expect(keyToAction("j", false)).toEqual({ type: "next" });
    expect(keyToAction("u", false)).toEqual({ type: "rate", rating: "up" });
    expect(keyToAction("d", false)).toEqual({ type: "rate", rating: "down" });
    expect(keyToAction("s", false)).toEqual({ type: "rate", rating: "skip" });
    expect(keyToAction("n", false)).toEqual({ type: "focusNote" });
    expect(keyToAction("u", true)).toBeNull();
    expect(keyToAction("x", false)).toBeNull();
  });
});
