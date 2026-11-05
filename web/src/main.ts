/**
 * Wire the sheet: load the district, draw nine views, sweep the day, and show
 * the year for whichever roof plane is selected.
 */

import "./style.css";

import { type District, FLAG_FLAT, FLAG_SIMPLIFIED, loadDistrict } from "./bundle";
import { CARPET_SCALE_W_M2, drawCarpet, legendGradient, NIGHT_CSS } from "./carpet";
import { type CellTime, Study } from "./study";

const DISTRICT = "kaptol-donji-grad";
// The middle column follows the slider; the outer two stay this far either side.
const COLUMN_OFFSET_HOURS = 3;
// One play of the sweep, start to end.
const SWEEP_SECONDS = 12;

const COMPASS = [
  "north",
  "north-east",
  "east",
  "south-east",
  "south",
  "south-west",
  "west",
  "north-west",
];

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

/** The selectable slots ordered from most sunlight to least. */
function sunlightOrder(d: District): number[] {
  return Array.from(d.selectablePoa.keys()).sort(
    (a, b) => d.selectablePoa[b] - d.selectablePoa[a] || a - b,
  );
}

/** "About 120 m south-east of Trg bana Jelačića", from the nearest landmark. */
function whereIs(d: District, x: number, y: number): string {
  let best = d.manifest.landmarks[0];
  let bestDist = Infinity;
  for (const l of d.manifest.landmarks) {
    const dist = Math.hypot(x - l.x_m, y - l.y_m);
    if (dist < bestDist) {
      best = l;
      bestDist = dist;
    }
  }
  if (bestDist < 25) return `At ${best.name}`;
  const bearing = (Math.atan2(x - best.x_m, y - best.y_m) * 180) / Math.PI;
  const name = COMPASS[Math.round(((bearing + 360) % 360) / 45) % 8];
  return `About ${Math.round(bestDist / 10) * 10} m ${name} of ${best.name}`;
}

async function main() {
  const district = await loadDistrict(DISTRICT);
  const m = district.manifest;

  $<HTMLElement>("#meta").innerHTML =
    `<b>${m.title}, Zagreb</b><br>` +
    `${m.centre.lat.toFixed(4)}&deg;N ${m.centre.lon.toFixed(4)}&deg;E, ${m.crs}<br>` +
    `${m.counts.buildings_loaded.toLocaleString()} buildings, ` +
    `${m.counts.selectable_planes.toLocaleString()} roof planes`;

  $<HTMLElement>("#colophon").innerHTML =
    `Geometry: ZG3D 2022, City of Zagreb, Otvorena dozvola. ` +
    `Irradiance: PVGIS ${m.irradiance.radiation_db}, ${m.irradiance.hours.toLocaleString()} hours of 2020. ` +
    `Shading from ${m.horizon.bins} azimuth bins to ${m.horizon.radius_m} m ` +
    `over a ${m.horizon.height_field_m} m height field. ` +
    `The ground is one level plane at ${m.ground.z_m.toFixed(0)} m, the median base of the ` +
    `buildings drawn here, because ZG3D models buildings and not terrain. ` +
    `Of the ${m.counts.buildings_loaded.toLocaleString()} buildings drawn, ` +
    `${m.counts.simplified_buildings.toLocaleString()} are hatched: the 2008 capture ` +
    `gave them one roof plane for the whole building. ` +
    `Street names and place positions from OpenStreetMap contributors, ODbL.`;

  $<HTMLElement>("#carpetKey").innerHTML =
    `<span class="k"><i class="ramp" style="background:${legendGradient("reaching")}"></i>` +
    `sunlight reaching the roof, 0 to ${CARPET_SCALE_W_M2} W/m²</span>` +
    `<span class="k"><i class="ramp" style="background:${legendGradient("taken")}"></i>` +
    `direct sun taken by buildings, 0 to ${CARPET_SCALE_W_M2} W/m²</span>` +
    `<span class="k"><i class="sw" style="background:${NIGHT_CSS}"></i>sun below the horizon</span>`;

  const canvas = $<HTMLCanvasElement>("#scene");
  const studyEl = $<HTMLElement>("#study");
  const cellEls = Array.from(document.querySelectorAll<HTMLElement>(".cell"));
  const cellRects = () => cellEls.map((el) => el.getBoundingClientRect());
  const study = new Study(canvas, district);
  study.resize();

  const rings = cellEls.map((el) => {
    const ring = document.createElement("span");
    ring.className = "ring";
    ring.hidden = true;
    el.appendChild(ring);
    return ring;
  });
  const dateNames = m.sweep.dates;

  const slider = $<HTMLInputElement>("#time");
  slider.max = String(m.sweep.steps - 1);

  const stepsPerHour = 60 / m.sweep.step_minutes;
  const offsetSteps = Math.round(COLUMN_OFFSET_HOURS * stepsPerHour);

  const clockOf = (step: number) => {
    const h = m.sweep.start_hour + (step * m.sweep.step_minutes) / 60;
    const hh = Math.floor(h);
    const mm = Math.round((h - hh) * 60);
    return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  };

  const clampStep = (s: number) => Math.max(0, Math.min(m.sweep.steps - 1, s));

  // ---- drawing, only when something changed ---------------------------------
  let selected = -1;
  let pending = false;

  function placeRings() {
    cellEls.forEach((el, c) => {
      const spot = selected >= 0 ? study.locate(selected, el.clientWidth, el.clientHeight) : null;
      const ring = rings[c];
      ring.hidden = !spot;
      if (spot) {
        ring.style.left = `${spot.x}px`;
        ring.style.top = `${spot.y}px`;
      }
    });
  }

  function requestRender() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      study.render(cellRects());
      placeRings();
    });
  }

  function cellsFor(centreStep: number): CellTime[] {
    const cols = [
      clampStep(centreStep - offsetSteps),
      clampStep(centreStep),
      clampStep(centreStep + offsetSteps),
    ];
    const out: CellTime[] = [];
    for (let date = 0; date < 3; date++) {
      for (const step of cols) {
        const sun = m.sweep.sun[date * m.sweep.steps + step];
        out.push({ date, step, elevation: sun.elevation, azimuth: sun.azimuth });
      }
    }
    return out;
  }

  function applyStep(centreStep: number) {
    const cells = cellsFor(centreStep);
    study.setCells(cells);

    document.querySelectorAll<HTMLElement>(".colhead").forEach((el, i) => {
      el.textContent = clockOf(cells[i].step);
    });
    document.querySelectorAll<HTMLElement>(".cellmeta").forEach((el, i) => {
      const { date, step, elevation, azimuth } = cells[i];
      const sun =
        elevation <= 0
          ? "sun below the horizon"
          : `sun ${elevation.toFixed(0)}° up, bearing ${azimuth.toFixed(0)}°`;
      el.innerHTML = `<span class="celldate">${dateNames[date]} ${clockOf(step)}, </span>${sun}`;
    });
    $<HTMLElement>("#clock").textContent = clockOf(centreStep);
    requestRender();
  }

  // ---- selection ------------------------------------------------------------
  const order = sunlightOrder(district);
  const rankOfSlot = new Int32Array(order.length);
  order.forEach((slot, i) => (rankOfSlot[slot] = i));

  function planeCentreXY(plane: number): [number, number] {
    let x = 0;
    let y = 0;
    let n = 0;
    for (let t = 0; t < district.planeOfTriangle.length; t++) {
      if (district.planeOfTriangle[t] !== plane) continue;
      for (let k = 0; k < 3; k++) {
        x += district.positions[(3 * t + k) * 3];
        y += district.positions[(3 * t + k) * 3 + 1];
        n++;
      }
    }
    return [x / n, y / n];
  }

  function bearingName(azimuth: number) {
    return COMPASS[Math.round(azimuth / 45) % 8];
  }

  function fact(label: string, value: string) {
    return `<div class="fact"><span class="lab">${label}</span><span class="val">${value}</span></div>`;
  }

  function describe(plane: number) {
    const slot = district.slotOfPlane[plane];
    if (slot < 0) return;
    selected = plane;
    study.setSelected(plane);

    const tilt = district.planeTilt[plane];
    const azimuth = district.planeAzimuth[plane];
    const area = district.planeArea[plane];
    const flat = (district.planeFlags[plane] & FLAG_FLAT) !== 0;
    const simplified = (district.planeFlags[plane] & FLAG_SIMPLIFIED) !== 0;
    const year = drawCarpet($<HTMLCanvasElement>("#carpet"), district, plane);
    if (!year) return;

    const [x, y] = planeCentreXY(plane);
    const kind = flat ? "Flat roof" : `Roof facing ${bearingName(azimuth)}`;
    const street = m.streets.names[district.selectableStreet[slot]];
    const metres = Math.round(district.selectableStreetDistance[slot]);
    $<HTMLElement>("#roofName").textContent = `${kind}, ${metres} m from ${street}`;
    $<HTMLElement>("#roofSub").textContent =
      `${whereIs(district, x, y)}. ZG3D building ${district.planeBuilding[plane]}, ` +
      `plane ${plane}, ` +
      (simplified ? "2008 capture with one roof plane for the whole building" : "2022 capture");

    // Numbers come from the build, which also ranks every plane; the carpet
    // recomputes the same year in the browser and a test holds the two together.
    const kept = district.selectableKept[slot];
    $<HTMLElement>("#facts").innerHTML = [
      fact("tilt", `${tilt.toFixed(0)}°`),
      fact("area", `${area.toFixed(0)} m²`),
      fact("sunlight", `${Math.round(district.selectablePoa[slot]).toLocaleString()} kWh/m² a year`),
      fact("rank by sunlight", `${rankOfSlot[slot] + 1} of ${order.length}`),
      fact("kept from shading", `${(kept * 100).toFixed(0)}%`),
      fact("direct sun kept", `${(district.selectableBeamKept[slot] * 100).toFixed(0)}%`),
      fact("sky kept", `${(district.selectableSkyKept[slot] * 100).toFixed(0)}%`),
    ].join("");

    const caption =
      `Day of year across, Zagreb clock hour up. Buildings take the direct sun from ` +
      `this plane for <b>${year.hoursTaken.toLocaleString()} hours a year</b>, and it ` +
      `keeps <b>${(kept * 100).toFixed(0)}%</b> of the sunlight it would receive with ` +
      `the city removed. Sunlight here is irradiance arriving on the roof, not ` +
      `electricity. The nine views above are three columns out of this. ` +
      (simplified
        ? "The hatch marks a building captured in 2008 with a single roof plane, so " +
          "this is the shape of a roof rather than that roof."
        : "");
    $<HTMLElement>("#caption").innerHTML = caption;
    $<HTMLElement>("#carpet").setAttribute(
      "aria-label",
      `The year for this roof: ${year.hoursTaken} hours of direct sun taken by buildings.`,
    );
    requestRender();
  }

  document.querySelectorAll<HTMLButtonElement>("[data-pick]").forEach((button) => {
    button.addEventListener("click", () => {
      const current = selected >= 0 ? rankOfSlot[district.slotOfPlane[selected]] : 0;
      const target = {
        best: 0,
        worst: order.length - 1,
        prev: Math.max(0, current - 1),
        next: Math.min(order.length - 1, current + 1),
      }[button.dataset.pick as "best" | "worst" | "prev" | "next"];
      describe(district.selectablePlane[order[target]]);
    });
  });

  let hovered = -1;
  studyEl.addEventListener("pointermove", (e) => {
    const plane = study.pick(cellRects(), e.clientX, e.clientY);
    studyEl.style.cursor = plane >= 0 ? "pointer" : "crosshair";
    if (plane === hovered) return;
    hovered = plane;
    study.setHover(plane);
    requestRender();
  });

  studyEl.addEventListener("click", (e) => {
    const plane = study.pick(cellRects(), e.clientX, e.clientY);
    if (plane >= 0 && district.slotOfPlane[plane] >= 0) describe(plane);
  });

  // ---- sweep ----------------------------------------------------------------
  let playing = false;
  let last = 0;
  let position = Number(slider.value);
  const playButton = $<HTMLButtonElement>("#play");

  function setPlaying(on: boolean) {
    playing = on;
    playButton.innerHTML = on ? "&#10073;&#10073;&nbsp; pause" : "&#9654;&nbsp; play";
    playButton.setAttribute("aria-pressed", String(on));
    if (on) {
      last = performance.now();
      requestAnimationFrame(tick);
    }
  }

  function tick(now: number) {
    if (!playing) return;
    position += ((now - last) / 1000) * (m.sweep.steps / SWEEP_SECONDS);
    last = now;
    if (position > m.sweep.steps - 1) position = 0;
    const step = Math.round(position);
    if (String(step) !== slider.value) {
      slider.value = String(step);
      applyStep(step);
    }
    requestAnimationFrame(tick);
  }

  playButton.addEventListener("click", () => setPlaying(!playing));
  slider.addEventListener("input", () => {
    setPlaying(false);
    position = Number(slider.value);
    applyStep(position);
  });

  window.addEventListener("resize", () => {
    study.resize();
    requestRender();
  });
  window.addEventListener("scroll", requestRender, { passive: true });

  applyStep(position);

  // Open on the most shaded plane in the district.
  let worst = 0;
  for (let i = 1; i < district.selectableKept.length; i++) {
    if (district.selectableKept[i] < district.selectableKept[worst]) worst = i;
  }
  describe(district.selectablePlane[worst]);
}

main().catch((error) => {
  document.body.innerHTML =
    `<pre style="padding:24px;font-family:monospace">Could not load the district.\n\n${error}\n\n` +
    `If this is a fresh clone, the bundle should be at web/public/district/. ` +
    `Rebuild it with: python scripts/build_district.py</pre>`;
  throw error;
});
