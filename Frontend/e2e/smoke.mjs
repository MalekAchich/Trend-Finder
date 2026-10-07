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
  check(await p.getByRole("navigation", { name: "Main" }).isVisible(), `${name}: top navbar`);
  check(await p.getByLabel("Trend Finder").locator("svg").count() === 1, `${name}: logo`);
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!overflow, `${name}: no horizontal scroll`);
  if (name === "desktop") {
    await p.getByRole("button", { name: /Trend references/ }).click();
    await p.getByRole("button", { name: /Targets to recreate/ }).click();
    await p.waitForTimeout(400);
    check(await p.getByLabel("Reference video link").isVisible() && await p.getByLabel("Target video link").isVisible(),
      "desktop: both reference sections open at once");
    if (shots) await p.screenshot({ path: `${shots}/inputs_open.png`, clip: { x: 0, y: 0, width: 1440, height: 900 } });
    await p.getByLabel("Reference video link").fill("https://example.com/nope");
    await p.keyboard.press("Enter");
    check(await p.getByRole("alert").first().isVisible(), "desktop: a non-video link is refused with a message");
    await p.getByRole("tab", { name: /Manually chosen/ }).click();
    await p.waitForTimeout(800);
    const manual = await p.locator("article[data-manual-id]").count();
    check(manual > 0 || await p.getByText("No manually chosen videos yet").isVisible(), `desktop: manually chosen tab (${manual} cards)`);
    if (shots) await p.getByRole("tab", { name: /Manually chosen/ }).scrollIntoViewIfNeeded()
      .then(() => p.screenshot({ path: `${shots}/manual.png`, fullPage: false }));
    await p.getByRole("tab", { name: /Found videos/ }).click();
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
  if (name !== "phone") {
    for (const [path, text] of [["/characters", "What the agents see"], ["/runs", "Agent runs"], ["/settings", "Usage"],
      ["/socials", "Nothing here yet"]]) {
      await p.getByRole("navigation", { name: "Main" }).getByRole("link", { name: path === "/runs" ? "Runs" : path.slice(1, 2).toUpperCase() + path.slice(2) }).click();
      await p.getByText(text).first().waitFor({ timeout: 10_000 }).catch(() => {});
      if (path === "/runs") await p.locator("tbody tr").first().waitFor({ timeout: 10_000 }).catch(() => {});
      await p.waitForTimeout(400);
      check(await p.getByText(text).first().isVisible(), `${name}: ${path} page`);
      if (shots && name === "desktop") await p.screenshot({ path: `${shots}/page${path.replace("/", "_")}.png`, fullPage: true });
    }
    await p.goto(base + "/settings");
    await p.getByRole("radiogroup", { name: "Provider priority" }).waitFor({ timeout: 10_000 }).catch(() => {});
    check(await p.getByRole("radio", { name: "Balanced" }).isVisible(), `${name}: provider priority control`);
    await p.getByRole("heading", { name: "Accounts & keys" }).waitFor({ timeout: 10_000 }).catch(() => {});
    check(await p.getByText("YouTube Data API key").isVisible() && await p.getByText("X scraping account").isVisible(),
      `${name}: accounts & keys section`);
    await p.goto(base + "/");
    const brands = p.getByRole("group", { name: "Platforms" }).locator(".platform-toggle");
    await brands.first().waitFor({ timeout: 10_000 }).catch(() => {});
    check(await brands.count() === 4 && await p.locator('.platform-toggle[data-platform="x"]').getAttribute("aria-pressed") === "false",
      `${name}: four platform buttons, X off by default`);
    await p.goto(base + "/runs");
    const firstRun = p.locator("tbody a").first();
    if (await firstRun.waitFor({ timeout: 5_000 }).then(() => true, () => false)) {
      await firstRun.click();
      await p.getByRole("region", { name: "Live agent run" }).waitFor({ timeout: 10_000 });
      await p.waitForTimeout(2500);
      const stuck = await p.locator('[aria-label="working"]').count();
      check(stuck === 0, `${name}: a finished run's agents aren't stuck on working (${stuck})`);
      if (shots && name === "desktop") await p.screenshot({ path: `${shots}/page_run.png`, fullPage: true });
    } else console.log(`skip ${name}: run replay (no runs yet)`);
  }
  await p.close();
}
check(errors.length === 0, `no console errors${errors.length ? `: ${errors.join(" | ")}` : ""}`);
await browser.close();
process.exit(failures.length ? 1 : 0);
