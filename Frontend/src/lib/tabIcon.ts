/** The browser-tab icon: still while idle, animated only while an agent run is active. */
export const ICON_URL = "/trend-finder-icon-animated.svg";

/** The icon's own reduced-motion rules define its still pose; applying them always gives the idle icon. */
export function stillVariant(svg: string): string {
  return svg.replace(/@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)/g, "@media all");
}

export const svgDataUrl = (svg: string) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;

/** Firefox plays SVG tab icons itself; Chromium shows only their first frame. */
export const playsSvgIcons = (ua: string) => /firefox/i.test(ua);

const FRAMES = 40;
const MEASURE_PX = 448;
const MARGIN = 0.01; // of the logo's size: just enough that anti-aliased stroke edges aren't cut

/** The square viewBox that fits the logo's visible pixels (`bounds` in px of a MEASURE_PX render). */
export function fitViewBox(viewBox: number[], bounds: { x0: number; y0: number; x1: number; y1: number }, px: number): string {
  const [vx, vy, vw, vh] = viewBox;
  const sx = vw / px, sy = vh / px;
  const x0 = vx + bounds.x0 * sx, x1 = vx + (bounds.x1 + 1) * sx;
  const y0 = vy + bounds.y0 * sy, y1 = vy + (bounds.y1 + 1) * sy;
  const side = Math.max(x1 - x0, y1 - y0) * (1 + 2 * MARGIN);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const r = (n: number) => Number(n.toFixed(2));
  return `${r(cx - side / 2)} ${r(cy - side / 2)} ${r(side)} ${r(side)}`;
}

/** The icon zoomed to its visible logo: its empty margin is measured, not assumed. Null when it can't be measured. */
async function tightViewBox(doc: Document, still: string): Promise<string | null> {
  const vb = still.match(/viewBox="([^"]+)"/)?.[1].trim().split(/[\s,]+/).map(Number);
  if (!vb || vb.length !== 4 || vb.some((n) => !Number.isFinite(n))) return null;
  const canvas = doc.createElement("canvas");
  canvas.width = canvas.height = MEASURE_PX;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  const img = new Image(MEASURE_PX, MEASURE_PX);
  img.src = svgDataUrl(still);
  await img.decode();
  ctx.drawImage(img, 0, 0, MEASURE_PX, MEASURE_PX);
  const { data } = ctx.getImageData(0, 0, MEASURE_PX, MEASURE_PX);
  let x0 = MEASURE_PX, y0 = MEASURE_PX, x1 = -1, y1 = -1;
  for (let y = 0; y < MEASURE_PX; y++) {
    for (let x = 0; x < MEASURE_PX; x++) {
      if (data[(y * MEASURE_PX + x) * 4 + 3] > 8) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  return x1 < 0 ? null : fitViewBox(vb, { x0, y0, x1, y1 }, MEASURE_PX);
}

const withViewBox = (svg: string, vb: string | null) => vb ? svg.replace(/viewBox="[^"]+"/, `viewBox="${vb}"`) : svg;

/** The icon's animation cycle in seconds: its longest `animation: name <n>s` (the file's own timing). */
export function cycleSeconds(svg: string): number {
  const durations = [...svg.matchAll(/animation\s*:\s*[\w-]+\s+([\d.]+)s/g)].map((m) => Number(m[1]));
  return Math.max(0, ...durations.filter((d) => Number.isFinite(d)));
}

/** The icon frozen at second `t` of its cycle (every animation shares the cycle, so one shared offset). */
export function frameAt(svg: string, t: number): string {
  const freeze = `<style>*{animation-play-state:paused!important;animation-delay:-${t.toFixed(3)}s!important}</style>`;
  return svg.replace(/<\/svg>\s*$/, `${freeze}</svg>`);
}

async function renderFrames(doc: Document, svg: string, cycle: number): Promise<string[]> {
  const canvas = doc.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (!ctx) return [];
  const out: string[] = [];
  for (let i = 0; i < FRAMES; i++) {
    const img = new Image(64, 64);
    img.src = svgDataUrl(frameAt(svg, (cycle * i) / FRAMES));
    await img.decode();
    ctx.clearRect(0, 0, 64, 64);
    ctx.drawImage(img, 0, 0, 64, 64);
    out.push(canvas.toDataURL("image/png"));
  }
  return out;
}

export function createTabIcon(doc: Document = document, ua: string = navigator.userAgent) {
  let link = doc.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = doc.createElement("link");
    link.rel = "icon";
    doc.head.appendChild(link);
  }
  const icon = link;
  let urls: { animated: string; still: string } | null = null;
  let source = "";
  let running = false;
  let timer: ReturnType<typeof setInterval> | null = null;
  let frames: Promise<string[]> | null = null;

  const reduced = () => doc.defaultView?.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

  function stopFrames() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  async function startFrames() {
    // Chromium shows only an SVG icon's first frame: play pre-rendered frames of the same animation instead
    const cycle = cycleSeconds(source);
    if (!cycle) return;
    frames ??= renderFrames(doc, source, cycle).catch(() => []);
    const list = await frames;
    if (!running || !list.length || timer) return;
    let i = 0;
    timer = setInterval(() => { icon.href = list[i++ % list.length]; }, (cycle * 1000) / list.length);
  }

  function apply() {
    if (!urls) return;
    stopFrames();
    if (!running || reduced()) {
      icon.type = "image/svg+xml";
      icon.href = urls.still;
    } else if (playsSvgIcons(ua)) {
      icon.type = "image/svg+xml";
      icon.href = urls.animated;
    } else {
      icon.type = "image/png";
      void startFrames();
    }
  }

  const ready = fetch(ICON_URL).then((r) => r.text()).then(async (raw) => {
    const vb = await tightViewBox(doc, stillVariant(raw)).catch(() => null);
    const svg = withViewBox(raw, vb);
    source = svg;
    urls = { animated: svgDataUrl(svg), still: svgDataUrl(stillVariant(svg)) };
    apply();
  }).catch(() => undefined); // the static <link> in index.html stays as the fallback

  return {
    ready,
    set(next: boolean) {
      if (next === running) return;
      running = next;
      apply();
    },
    dispose: stopFrames,
  };
}
