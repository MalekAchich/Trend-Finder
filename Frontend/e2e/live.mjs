// Live end-to-end through the UI (spends subscription usage): start a small run, watch it, rate the cards,
// send satisfaction, write a brief for the select. Usage: node e2e/live.mjs [baseUrl] [shotDir]
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://127.0.0.1:8000";
const shots = process.argv[3];
const log = (m) => console.log(`${new Date().toISOString().slice(11, 19)} ${m}`);
const browser = await chromium.launch();
const p = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
p.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const shot = (name) => shots && p.screenshot({ path: `${shots}/live_${name}.png`, fullPage: true });

await p.goto(`${base}/runs`);
await p.getByLabel("Rounds").fill("1");
await p.getByLabel("Searches per round").fill("4");
await p.getByLabel("Trend cards wanted").fill("6");
await p.getByLabel("Time limit (minutes)").fill("25");
await p.getByRole("button", { name: "Start run" }).click();
await p.waitForURL(/\/runs\/[0-9a-f-]+$/);
const runId = p.url().split("/").pop();
log(`run ${runId} started`);

await p.waitForTimeout(90_000);
await shot("running");
await p.getByRole("link", { name: "Review trend cards" }).waitFor({ timeout: 40 * 60_000 });
log("review ready");
await shot("finished");
await p.getByRole("link", { name: "Review trend cards" }).click();
await p.getByText(/\d+ of \d+ marked/).waitFor();
const total = Number((await p.getByText(/\d+ of \d+ marked/).textContent()).split(" of ")[1].split(" ")[0]);
log(`${total} cards`);
for (let i = 0; i < total; i++) {
  await p.keyboard.press("n");
  await p.keyboard.type(i === 0 ? "Love the deadpan potential here" : "Not his energy");
  await p.keyboard.press("Escape");
  await p.keyboard.press(i === 0 ? "u" : "d");
  await p.waitForTimeout(300);
}
await shot("rated");
const slider = p.getByLabel("Satisfaction from 1 to 10");
await slider.focus();
await p.keyboard.press("ArrowRight");
await p.getByLabel("Run note").fill("Good start. More solo, full-body, still-camera formats please.");
await p.getByRole("button", { name: "Send feedback" }).click();
await p.getByText(/^Feedback saved\./).waitFor({ timeout: 10 * 60_000 });
log(`feedback: ${(await p.getByText(/^Feedback saved\./).textContent())}`);
await shot("feedback");
await p.getByRole("button", { name: "Write brief" }).first().click();
await p.getByRole("link", { name: "Open brief" }).first().waitFor({ timeout: 10 * 60_000 });
log("brief written");
await p.getByRole("link", { name: "Open brief" }).first().click();
await p.getByRole("heading", { name: "Production briefs" }).waitFor();
await p.waitForTimeout(800);
await shot("brief");
log(errors.length ? `console errors: ${errors.join(" | ")}` : "no console errors");
await browser.close();
process.exit(errors.length ? 1 : 0);
