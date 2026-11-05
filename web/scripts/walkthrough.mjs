/**
 * Record the README walkthrough from the production build.
 *
 *   npm run walkthrough
 *
 * Builds the page, serves it, drives it with Playwright while recording video,
 * then uses ffmpeg to write docs/walkthrough/app-walkthrough.mp4 (H.264,
 * yuv420p, faststart, at the recording size), app-walkthrough.webp for the
 * README, app-walkthrough-poster.jpg, and web/public/og.jpg for link previews.
 *
 * Needs ffmpeg on PATH and a Playwright Chromium (npx playwright install
 * chromium). CHROMIUM_EXECUTABLE points it at another Chromium build.
 */

import { spawnSync } from "node:child_process";
import { mkdtemp, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";
import { preview } from "vite";

// The README column is about 880 px wide, so the page is recorded at that width
// and the animation is shown at its own size.
const VIEWPORT = { width: 880, height: 760 };
const WEBP_FPS = 12;
const WEBP_LIMIT = 5 * 1024 * 1024;

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "..");
const out = path.resolve(web, "..", "docs", "walkthrough");

function ffmpeg(args) {
  const run = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], {
    stdio: "inherit",
  });
  if (run.error) {
    console.error("ffmpeg was not found. Install it (https://ffmpeg.org/download.html) and retry.");
    process.exit(1);
  }
  if (run.status !== 0) process.exit(run.status ?? 1);
}

const server = await preview({ root: web, preview: { host: "127.0.0.1", port: 4180, strictPort: true } });
const url = server.resolvedUrls.local[0];
const scratch = await mkdtemp(path.join(tmpdir(), "suncokret-walkthrough-"));

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || undefined });
const context = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: 1,
  recordVideo: { dir: scratch, size: VIEWPORT },
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

const opened = Date.now();
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForSelector(".fact");
await page.waitForTimeout(400);
const start = (Date.now() - opened) / 1000;

// 1. The sheet as it opens.
await page.waitForTimeout(2500);

// 2. From the start of the day, all nine views sweep together until 15:00.
await page.focus("#time");
await page.keyboard.press("Home");
await page.waitForTimeout(500);
await page.click("#play");
await page.waitForFunction(() => Number(document.querySelector("#time").value) >= 33, null, {
  timeout: 20000,
});
await page.click("#play");
await page.waitForTimeout(700);

// 3. Select a roof in the middle drawing: move across it until the pointer is
//    over a selectable roof, then click it.
const cell = await page.locator('.cell[data-cell="4"]').boundingBox();
const before = await page.textContent("#roofName");
let picked = false;
for (let fy = 0.35; fy <= 0.65 && !picked; fy += 0.05) {
  for (let fx = 0.4; fx <= 0.6 && !picked; fx += 0.04) {
    await page.mouse.move(cell.x + cell.width * fx, cell.y + cell.height * fy, { steps: 4 });
    if ((await page.evaluate(() => document.querySelector("#study").style.cursor)) !== "pointer") {
      continue;
    }
    await page.mouse.click(cell.x + cell.width * fx, cell.y + cell.height * fy);
    await page.waitForTimeout(150);
    picked = (await page.textContent("#roofName")) !== before;
  }
}
if (!picked) throw new Error("no selectable roof found under the pointer in the middle drawing");
await page.waitForTimeout(1200);

// 4. Down to its year, then the roof with the least sunlight in the district.
await page.evaluate(() =>
  document.querySelector(".roofline").scrollIntoView({ behavior: "smooth", block: "start" }),
);
await page.waitForTimeout(2800);
await page.click('[data-pick="worst"]');
await page.waitForTimeout(2800);

const end = (Date.now() - opened) / 1000;
const video = page.video();
await context.close();
const closed = (Date.now() - opened) / 1000;
const recorded = await video.path();
await browser.close();
server.httpServer.close();

// The recording starts a little after the page is created. Its length against
// the wall clock gives that delay, so the cut lands on the moments timed above.
const lastFrame = spawnSync(
  "ffprobe",
  ["-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", recorded],
  { encoding: "utf8" },
)
  .stdout.trim()
  .split(/\s+/)
  .map(Number)
  .filter(Number.isFinite);
const delay = Math.max(0, closed - Math.max(...lastFrame));

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}

const mp4 = path.join(out, "app-walkthrough.mp4");
const webp = path.join(out, "app-walkthrough.webp");
const poster = path.join(out, "app-walkthrough-poster.jpg");
const og = path.join(web, "public", "og.jpg");

ffmpeg([
  "-ss", Math.max(0, start - delay).toFixed(2), "-i", recorded, "-t", (end - start).toFixed(2),
  "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
  "-movflags", "+faststart", "-an", mp4,
]);
ffmpeg(["-i", mp4, "-frames:v", "1", "-q:v", "2", poster]);
ffmpeg(["-i", poster, "-vf", "scale=1200:-1,crop=1200:630:0:0", "-q:v", "3", og]);

for (const quality of [70, 60, 50, 40]) {
  ffmpeg([
    "-i", mp4, "-vf", `fps=${WEBP_FPS}`, "-c:v", "libwebp_anim", "-lossless", "0",
    "-q:v", String(quality), "-compression_level", "6", "-loop", "0", "-an", webp,
  ]);
  if ((await stat(webp)).size < WEBP_LIMIT) break;
}
await rm(scratch, { recursive: true, force: true });

for (const file of [mp4, webp, poster, og]) {
  console.log(`${path.relative(path.resolve(web, ".."), file)}  ${((await stat(file)).size / 1e6).toFixed(2)} MB`);
}
console.log(`recorded ${(end - start).toFixed(1)} s`);
