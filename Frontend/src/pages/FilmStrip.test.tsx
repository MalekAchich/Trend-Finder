import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import type { TrendCard } from "../api/types";
import { FilmStrip } from "./Review";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("FilmStrip", () => {
  it("unmounts cleanly when scrollIntoView returns a promise (current Chromium)", () => {
    Element.prototype.scrollIntoView = function () { return Promise.resolve() as unknown as void; };
    const card = { id: "a", rank: 1, contact_sheet_url: null } as TrendCard;
    const host = document.createElement("div");
    const root = createRoot(host);
    act(() => root.render(<FilmStrip cards={[card]} index={0} ratings={{}} onPick={() => {}} />));
    expect(() => act(() => root.unmount())).not.toThrow();
  });
});
