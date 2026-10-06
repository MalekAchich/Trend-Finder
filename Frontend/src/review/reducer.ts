import type { Rating } from "../api/types";

export interface CardRating {
  rating: Rating;
  note: string | null;
}

export interface ReviewState {
  ids: string[];
  index: number;
  ratings: Record<string, CardRating>;
  drafts: Record<string, string>;
  complete: boolean;
}

export type ReviewAction =
  | { type: "next" }
  | { type: "prev" }
  | { type: "goto"; index: number }
  | { type: "rate"; rating: Rating }
  | { type: "note"; note: string }
  | { type: "focusNote" };

export function initialReview(ids: string[], existing: Record<string, CardRating> = {}): ReviewState {
  const drafts = Object.fromEntries(Object.entries(existing).map(([id, r]) => [id, r.note ?? ""]));
  const firstOpen = ids.findIndex((id) => !existing[id]);
  return {
    ids,
    index: firstOpen === -1 ? 0 : firstOpen,
    ratings: { ...existing },
    drafts,
    complete: ids.length > 0 && ids.every((id) => existing[id]),
  };
}

const clamp = (i: number, n: number) => Math.max(0, Math.min(i, n - 1));

function nextUnrated(state: ReviewState, from: number): number {
  for (let step = 1; step <= state.ids.length; step++) {
    const i = (from + step) % state.ids.length;
    if (!state.ratings[state.ids[i]]) return i;
  }
  return clamp(from + 1, state.ids.length);
}

export function reviewReducer(state: ReviewState, action: ReviewAction): ReviewState {
  const n = state.ids.length;
  if (n === 0) return state;
  const id = state.ids[state.index];
  switch (action.type) {
    case "next":
      return { ...state, index: clamp(state.index + 1, n) };
    case "prev":
      return { ...state, index: clamp(state.index - 1, n) };
    case "goto":
      return { ...state, index: clamp(action.index, n) };
    case "note":
      return { ...state, drafts: { ...state.drafts, [id]: action.note } };
    case "rate": {
      const note = state.drafts[id]?.trim() || null;
      const ratings = { ...state.ratings, [id]: { rating: action.rating, note } };
      const rated = { ...state, ratings };
      const complete = state.ids.every((x) => ratings[x]);
      return { ...rated, complete, index: complete ? state.index : nextUnrated(rated, state.index) };
    }
    default:
      return state;
  }
}

export function keyToAction(key: string, typing: boolean): ReviewAction | null {
  if (typing) return null;
  switch (key.toLowerCase()) {
    case "j":
    case "arrowright":
      return { type: "next" };
    case "k":
    case "arrowleft":
      return { type: "prev" };
    case "u":
      return { type: "rate", rating: "up" };
    case "d":
      return { type: "rate", rating: "down" };
    case "s":
      return { type: "rate", rating: "skip" };
    case "n":
      return { type: "focusNote" };
    default:
      return null;
  }
}
