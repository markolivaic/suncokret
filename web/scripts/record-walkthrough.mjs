/**
 * Record the walkthrough that goes in the README.
 *
 * The walkthrough is not a narrated tour, it is the thing the page does: one
 * sweep of the day with all nine views moving together. So this drives the real
 * page, steps the real control, and captures the real frames. Nothing is staged
 * and there is no script to drift out of date.
 *
 *   cd web && npm run record:walkthrough
 *
 * Writes docs/walkthrough-frames/*.png.
 *
 * This is the frame-by-frame version and it is not what gets published. The
 * checklist wants one uninterrupted recording of the running application and
 * says no generated frames, so stitching these into a GIF produced a slideshow
 * that broke the rule it was supposed to satisfy. It is kept because it already
 * drives the real page through the real control, which is the half a video
 * recorder needs. The other half needs ffmpeg.
 */

import { mkdir, rm } from "node:fs/promises";
import { chromium } from "playwright";

const URL = process.env.SUNCOKRET_URL ?? "http://localhost:5183/";
const OUT = "../docs/walkthrough-frames";

// The sweep window is 04:00 to 21:00 in 20 minute steps. Every third step keeps
// the file small while still moving the shadows visibly between frames.
const STEP_EVERY = 3;

const VIEWPORT = { width: 1500, height: 940 };

async function main() {
  await rm(OUT, { recursive: true, force: true });
  await mkdir(OUT, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
  });

  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });

  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector("#time");
  // The first paint of a WebGL scene lands a frame or two after load.
  await page.waitForTimeout(1200);

  const steps = await page.$eval("#time", (el) => Number(el.max));
  const grid = await page.$("#study");
  if (!grid) throw new Error("no study grid on the page");

  let frame = 0;
  for (let step = 0; step <= steps; step += STEP_EVERY) {
    await page.$eval(
      "#time",
      (el, value) => {
        const input = el;
        input.value = String(value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      },
      step,
    );
    // Two frames: one to apply the state, one to draw it.
    await page.evaluate(
      () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
    );
    await page.screenshot({
      path: `${OUT}/frame-${String(frame).padStart(3, "0")}.png`,
      clip: await grid.boundingBox(),
    });
    frame++;
  }

  await browser.close();

  if (errors.length) {
    console.error("page reported errors:");
    for (const e of errors) console.error("  " + e);
    process.exitCode = 1;
    return;
  }
  console.log(`captured ${frame} frames of ${steps + 1} sweep steps into ${OUT}`);
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
