// Browser check of the single page against a running backend: renders at desktop/laptop/phone widths without
// console errors, the composer works, the library shows a character's runs, and hovering a card starts a preview.
// Usage: node e2e/smoke.mjs [baseUrl] [shotDir]
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://127.0.0.1:8000";
const shots = process.argv[3];
const browser = await chromium.launch();
const errors = [];
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); console.log(`${ok ? "ok  " : "FAIL"} ${what}`); };

for (const [w, h, name] of [[1440, 900, "desktop"], [1280, 800, "laptop"], [390, 844, "phone"]]) {
  const p = await browser.newPage({ viewport: { width: w, height: h } });
  p.on("console", (m) => m.type() === "error" && !/tiktok|youtube|doubleclick|googlevideo/i.test(m.text()) && errors.push(`${name}: ${m.text()}`));
  p.on("pageerror", (e) => errors.push(`${name}: ${e.message}`));
  await p.goto(base);
  await p.getByRole("radio").first().waitFor({ timeout: 15_000 });
  await p.locator("article[data-video-id]").first().waitFor({ timeout: 15_000 }).catch(() => {});
  await p.waitForTimeout(500);
  if (shots) await p.screenshot({ path: `${shots}/page_${name}.png`, fullPage: true });
  check((await p.getByRole("heading", { level: 1 }).textContent()).includes("post next"), `${name}: headline names the character`);
  check(await p.getByRole("radio").count() >= 1, `${name}: characters come from the folder`);
  check(await p.getByText("Found videos").isVisible(), `${name}: library is on the same page`);
  check(!(await p.locator("nav, aside[role=navigation]").count()), `${name}: no navigation menu`);
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!overflow, `${name}: no horizontal scroll`);
  if (name === "desktop") {
    await p.getByRole("button", { name: /Trending AI-influencer videos/ }).click();
    await p.getByLabel("Trending video link").fill("https://www.tiktok.com/@a/video/7400000000000000001");
    await p.keyboard.press("Enter");
    check(await p.getByText("tiktok.com/@a/video/7400000000000000001").isVisible(), "desktop: trend URL becomes a chip");
    await p.getByLabel("Trending video link").fill("https://example.com/nope");
    await p.keyboard.press("Enter");
    check(await p.getByRole("alert").isVisible(), "desktop: a non-video link is refused with a message");
    const card = p.locator("article[data-video-id] button[aria-label^='Open']").first();
    if (await card.count()) {
      await card.hover();
      await p.waitForTimeout(250);
      check(await p.locator("article[data-video-id] iframe").count() === 0, "desktop: no preview before the hover delay");
      await p.waitForTimeout(500);
      check(await p.locator("article[data-video-id] iframe").count() === 1, "desktop: hover plays the platform preview");
      await p.mouse.move(5, 5);
      await p.waitForTimeout(100);
      check(await p.locator("article[data-video-id] iframe").count() === 0, "desktop: leaving stops the preview");
      if (shots) await card.hover().then(() => p.waitForTimeout(3500)).then(() =>
        p.locator("article[data-video-id]").first().screenshot({ path: `${shots}/hover.png` }));
    }
  }
  await p.close();
}
check(errors.length === 0, `no console errors${errors.length ? `: ${errors.join(" | ")}` : ""}`);
await browser.close();
process.exit(failures.length ? 1 : 0);
