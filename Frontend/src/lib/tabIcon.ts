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

  const ready = fetch(ICON_URL).then((r) => r.text()).then((svg) => {
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
