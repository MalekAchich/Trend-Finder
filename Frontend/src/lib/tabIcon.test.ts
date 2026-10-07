import { afterEach, describe, expect, it, vi } from "vitest";
import { createTabIcon, cycleSeconds, fitViewBox, frameAt, playsSvgIcons, stillVariant, svgDataUrl } from "./tabIcon";

const SVG = `<svg><style>.a{animation:x 4s infinite} @media (prefers-reduced-motion:reduce) { .a{animation:none!important} }</style></svg>`;

describe("tab icon", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("takes its still pose from the icon's own reduced-motion rules", () => {
    expect(stillVariant(SVG)).toContain("@media all { .a{animation:none!important} }");
    expect(stillVariant(SVG)).not.toContain("prefers-reduced-motion");
  });

  it("reads the cycle from the icon and freezes it at any second", () => {
    expect(cycleSeconds(SVG)).toBe(4);
    expect(frameAt(SVG, 1.5)).toMatch(/animation-delay:-1\.500s!important\}<\/style><\/svg>$/);
  });

  it("zooms to the logo's visible pixels, as a square with a small margin", () => {
    // a 448 px render of a 112-unit icon whose logo covers px 40..407 across and 60..387 down
    expect(fitViewBox([0, 0, 112, 112], { x0: 40, y0: 60, x1: 407, y1: 387 }, 448)).toBe("9.08 9.08 93.84 93.84");
  });

  it("knows which browsers play SVG tab icons", () => {
    expect(playsSvgIcons("Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0")).toBe(true);
    expect(playsSvgIcons("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153.0 Safari/537.36")).toBe(false);
  });

  it("is still while idle and animated only while a run is active", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ text: async () => SVG })));
    const icon = createTabIcon(document, "Firefox/150.0");
    await icon.ready;
    const link = () => document.querySelector<HTMLLinkElement>('link[rel="icon"]')!.href;
    expect(link()).toBe(svgDataUrl(stillVariant(SVG)));
    icon.set(true);
    expect(link()).toBe(svgDataUrl(SVG));
    icon.set(false);
    expect(link()).toBe(svgDataUrl(stillVariant(SVG)));
  });
});
