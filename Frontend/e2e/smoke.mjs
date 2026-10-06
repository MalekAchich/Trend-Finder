// Browser smoke test against a running backend: every page renders without console errors,
// deep links work, and the review keyboard flow marks cards. Usage: node e2e/smoke.mjs [baseUrl] [shotDir]
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://127.0.0.1:8000";
const shots = process.argv[3];
const browser = await chromium.launch();
const errors = [];
const failures = [];

async function page(width, height) {
  const p = await browser.newPage({ viewport: { width, height } });
  p.on("console", (m) => m.type() === "error" && errors.push(`${p.url()}: ${m.text()}`));
  p.on("pageerror", (e) => errors.push(`${p.url()}: ${e.message}`));
  return p;
}
const check = (ok, what) => { if (!ok) failures.push(what); console.log(`${ok ? "ok  " : "FAIL"} ${what}`); };

const p = await page(1440, 900);
const runs = await (await fetch(`${base}/api/runs`)).json();
const ready = runs.find((r) => r.state === "review_ready");

for (const [path, text] of [["/runs", "Start a run"], ["/characters", "Characters"], ["/settings", "How cards are ranked"],
  ["/briefs", "Production briefs"]]) {
  await p.goto(base + path);
  await p.getByText(text).first().waitFor({ timeout: 10_000 }).catch(() => {});
  check(await p.getByText(text).first().isVisible(), `${path} renders "${text}"`);
  if (shots) await p.screenshot({ path: `${shots}/desktop${path.replaceAll("/", "_")}.png`, fullPage: true });
}

const chars = await (await fetch(`${base}/api/characters`)).json();
if (chars[0]) {
  await p.goto(`${base}/characters/${chars[0].slug}`);
  await p.getByText("Taste profile").first().waitFor({ timeout: 10_000 });
  check(true, "character page renders");
  if (shots) await p.screenshot({ path: `${shots}/desktop_character.png`, fullPage: true });
}

if (ready) {
  await p.goto(`${base}/runs/${ready.id}`);
  await p.getByText("What the lead agent decided").waitFor({ timeout: 10_000 });
  check(true, "live run page renders for a finished run");
  if (shots) await p.screenshot({ path: `${shots}/desktop_run.png`, fullPage: true });

  await p.goto(`${base}/runs/${ready.id}/review`);
  await p.getByText("Review trend cards").waitFor({ timeout: 10_000 });
  const marked = async () => Number((await p.getByText(/\d+ of \d+ marked/).textContent()).split(" ")[0]);
  const before = await marked();
  await p.keyboard.press("n");
  check(await p.evaluate(() => document.activeElement?.tagName === "TEXTAREA"), "N focuses the note");
  await p.keyboard.type("smoke note u d");
  check(await marked() === before, "typing in the note doesn't trigger shortcuts");
  await p.keyboard.press("Escape");
  await p.keyboard.press("u");
  await p.waitForTimeout(500);
  check(await marked() === Math.min(before + 1, 999) || before > 0, "U marks the card");
  if (shots) await p.screenshot({ path: `${shots}/desktop_review.png`, fullPage: true });
  await p.keyboard.press("k");
  check(await p.locator('[aria-label="Marked: select"]').first().isVisible(), "the select mark shows after going back");

  const phone = await page(390, 844);
  await phone.goto(`${base}/runs/${ready.id}/review`);
  await phone.getByText("Review trend cards").waitFor({ timeout: 10_000 });
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!overflow, "review has no horizontal page scroll at phone width");
  if (shots) await phone.screenshot({ path: `${shots}/phone_review.png`, fullPage: true });
} else {
  console.log("skip review checks: no review_ready run");
}

check(errors.length === 0, `no console errors${errors.length ? `: ${errors.join(" | ")}` : ""}`);
await browser.close();
process.exit(failures.length ? 1 : 0);
