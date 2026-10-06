// Live run through the page (spends subscription usage): pick a character, add a trend URL and a target, start,
// watch the stream, rate a saved video with a note and score the run. Usage: node e2e/live.mjs [baseUrl] [shotDir]
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://127.0.0.1:8000";
const shots = process.argv[3];
const TREND = "https://www.tiktok.com/@egoliftclub0/video/7685930048183864589";
const TARGET = "https://www.youtube.com/shorts/QqvZAUEOrAQ";
const log = (m) => console.log(`${new Date().toISOString().slice(11, 19)} ${m}`);
const browser = await chromium.launch();
const p = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
const shot = (n, full = false) => shots && p.screenshot({ path: `${shots}/live_${n}.png`, fullPage: full });

await p.goto(base);
await p.getByRole("radio", { name: "Nicolaiz" }).click();
await p.getByRole("button", { name: /Trending AI-influencer videos/ }).click();
await p.getByLabel("Trending video link").fill(TREND);
await p.keyboard.press("Enter");
await p.getByRole("button", { name: /Target videos I found/ }).click();
await p.getByLabel("Target video 1").fill(TARGET);
await p.getByRole("button", { name: /Time limit/ }).click();
await p.getByRole("button", { name: "15 min" }).click();
await p.keyboard.press("Escape");
await p.getByRole("button", { name: "Start run" }).click();
await p.getByRole("region", { name: "Live agent run" }).waitFor({ timeout: 30_000 });
log("run started");
for (const [after, name] of [[45_000, "early"], [180_000, "mid"], [420_000, "late"]]) {
  await p.waitForTimeout(after - (name === "early" ? 0 : name === "mid" ? 45_000 : 180_000));
  await p.getByRole("region", { name: "Live agent run" }).screenshot({ path: `${shots}/live_engine_${name}.png` });
  log(`screenshot ${name}: ${await p.getByRole("status").first().textContent()}`);
}
await p.getByText("No run going").waitFor({ timeout: 30 * 60_000 });
log("run finished");
await p.waitForTimeout(3000);
await shot("finished", true);
const up = p.getByRole("button", { name: "I like it" }).first();
await up.waitFor({ timeout: 60_000 });
await up.click();
await p.getByRole("button", { name: /Add note|Edit note/ }).first().click();
await p.getByLabel("Note").first().fill("Great contrast for him, more like this");
await p.getByRole("button", { name: "Save note" }).first().click();
await p.getByRole("radio", { name: "8" }).first().click();
await p.getByRole("button", { name: "Save score" }).first().click();
await p.getByRole("button", { name: "Saved" }).first().waitFor({ timeout: 15_000 });
log("rated and scored");
await shot("rated", true);
log(errors.length ? `page errors: ${errors.join(" | ")}` : "no page errors");
await browser.close();
process.exit(errors.length ? 1 : 0);
