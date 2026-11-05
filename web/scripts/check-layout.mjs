/**
 * Layout checks that need a real browser, run against the production build.
 *
 *   npm run build && npm run check:layout
 *
 * At 390 px the page must not scroll sideways and the drawing canvas must match
 * the viewport. At 1440x900 and 1366x768 the play control and all nine drawings
 * must be on the first screen. Exits non-zero on any failure or page error.
 */

import { chromium } from "playwright";
import { preview } from "vite";

const server = await preview({ preview: { host: "127.0.0.1", port: 4173, strictPort: true } });
const url = server.resolvedUrls.local[0];
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || undefined });
const failures = [];

async function open(width, height) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.on("pageerror", (e) => failures.push(`${width}x${height}: ${e}`));
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector(".fact");
  return page;
}

const phone = await open(390, 844);
const narrow = await phone.evaluate(() => ({
  scroll: document.documentElement.scrollWidth,
  client: document.documentElement.clientWidth,
  canvas: document.querySelector("#scene").clientWidth,
}));
if (narrow.scroll !== narrow.client) {
  failures.push(`390 px: page is ${narrow.scroll} px wide in a ${narrow.client} px viewport`);
}
if (narrow.canvas !== narrow.client) {
  failures.push(`390 px: canvas is ${narrow.canvas} px in a ${narrow.client} px viewport`);
}
await phone.close();

for (const [width, height] of [
  [1440, 900],
  [1366, 768],
]) {
  const page = await open(width, height);
  const fold = await page.evaluate(() => {
    const cells = [...document.querySelectorAll(".cell")].map((c) => c.getBoundingClientRect());
    return {
      lastBottom: Math.max(...cells.map((r) => r.bottom)),
      play: document.querySelector("#play").getBoundingClientRect().bottom,
      height: innerHeight,
    };
  });
  if (fold.lastBottom > fold.height + 1) {
    failures.push(`${width}x${height}: the drawings end at ${fold.lastBottom} px`);
  }
  if (fold.play > fold.height) failures.push(`${width}x${height}: play is below the fold`);
  await page.close();
}

await browser.close();
server.httpServer.close();

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("layout checks passed");
