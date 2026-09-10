/**
 * Wire the sheet: load the district, draw nine views, sweep the day, and
 * report the year for whichever roof plane the reader picks.
 */

import "./style.css";

import { type District, FLAG_SIMPLIFIED, loadDistrict } from "./bundle";
import { drawCarpet } from "./carpet";
import { type CellTime, Study } from "./study";

const DISTRICT = "kaptol-donji-grad";
// The three canonical columns, and the spacing the sweep keeps between them.
const COLUMN_OFFSET_HOURS = 3;

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

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
    `Irradiance: PVGIS ${m.irradiance.radiation_db}, ${m.irradiance.hours.toLocaleString()} hours. ` +
    `Shading computed from ${m.horizon.bins} azimuth bins to ${m.horizon.radius_m} m ` +
    `over a ${m.horizon.height_field_m} m height field. ` +
    `The ground is one level plane at ${m.ground.z_m.toFixed(0)} m, the median base of the ` +
    `buildings drawn here: ZG3D models buildings and not terrain, so the surface ` +
    `the shadows fall on is an assumption. ` +
    `Of the ${m.counts.buildings_loaded.toLocaleString()} buildings drawn here, ` +
    `<b>${m.counts.simplified_buildings.toLocaleString()} are hatched</b>: the 2008 capture ` +
    `gave them one roof plane for the whole building.`;

  const canvas = $<HTMLCanvasElement>("#scene");
  const studyEl = $<HTMLElement>("#study");
  const cellEls = Array.from(document.querySelectorAll<HTMLElement>(".cell"));
  const cellRects = () => cellEls.map((el) => el.getBoundingClientRect());
  const study = new Study(canvas, district);

  const slider = $<HTMLInputElement>("#time");
  slider.max = String(m.sweep.steps - 1);

  const stepsPerHour = 60 / m.sweep.step_minutes;
  const offsetSteps = Math.round(COLUMN_OFFSET_HOURS * stepsPerHour);

  const hourOfStep = (step: number) =>
    m.sweep.start_hour + (step * m.sweep.step_minutes) / 60;

  const clockOf = (step: number) => {
    const h = hourOfStep(step);
    const hh = Math.floor(h);
    const mm = Math.round((h - hh) * 60);
    return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  };

  const clampStep = (s: number) => Math.max(0, Math.min(m.sweep.steps - 1, s));

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
        out.push({
          date,
          step,
          elevation: sun.elevation,
          azimuth: sun.azimuth,
        });
      }
    }
    return out;
  }

  function applyStep(centreStep: number) {
    const cells = cellsFor(centreStep);
    study.setCells(cells);

    const cols = [cells[0].step, cells[1].step, cells[2].step];
    document.querySelectorAll<HTMLElement>(".colhead").forEach((el, i) => {
      el.textContent = clockOf(cols[i]);
    });
    document.querySelectorAll<HTMLElement>(".cellmeta").forEach((el, i) => {
      const { date, step } = cells[i];
      const sun = m.sweep.sun[date * m.sweep.steps + step];
      el.textContent =
        sun.elevation <= 0
          ? "sun is down"
          : `alt ${sun.elevation.toFixed(0)}°, az ${sun.azimuth.toFixed(0)}°`;
    });
    $<HTMLElement>("#clock").textContent = clockOf(centreStep);
  }

  // ---- selection ----------------------------------------------------------
  let selected = -1;

  function describe(plane: number) {
    const slot = district.slotOfPlane[plane];
    if (slot < 0) return;

    const tilt = district.planeTilt[plane];
    const azimuth = district.planeAzimuth[plane];
    const area = district.planeArea[plane];
    const simplified = (district.planeFlags[plane] & FLAG_SIMPLIFIED) !== 0;
    const result = drawCarpet($<HTMLCanvasElement>("#carpet"), district, plane);
    if (!result) return;

    const compass = azimuth < 0 ? "flat" : bearingName(azimuth);
    const rank = rankOf(district, slot);

    $<HTMLElement>("#roofName").textContent =
      `Roof plane ${plane}, building ${district.planeBuilding[plane]}`;
    $<HTMLElement>("#roofSub").textContent = simplified
      ? "2008 capture, one roof plane for the whole building"
      : "2022 multisensor capture";

    $<HTMLElement>("#facts").innerHTML = [
      fact("tilt", azimuth < 0 ? "flat" : `${tilt.toFixed(0)}°`),
      fact("faces", compass),
      fact("area", `${area.toFixed(0)} m²`),
      fact("beam kept", result.beamKept.toFixed(2)),
      fact("sky kept", result.skyKept.toFixed(2)),
      fact("total kept", result.kept.toFixed(2)),
      fact("in district", `${rank} / ${district.selectableKept.length}`),
    ].join("");

    const stolen = result.hoursStolen;
    $<HTMLElement>("#caption").innerHTML =
      `Day of year across, hour of day up. This plane loses the direct sun for ` +
      `<b>${stolen.toLocaleString()} hours a year</b> to the buildings around it, ` +
      `and keeps <b>${(result.kept * 100).toFixed(0)}%</b> of the irradiance it would ` +
      `receive with the city removed. The nine views above are three columns out of this. ` +
      (simplified
        ? "The hatch is here for the same reason it is on the drawing: this building " +
          "was captured in 2008 with a single roof plane, so this is the shape of a " +
          "roof rather than that roof."
        : "");
  }

  function fact(label: string, value: string) {
    return `<div class="fact"><span class="lab">${label}</span><span class="val">${value}</span></div>`;
  }

  function bearingName(azimuth: number) {
    const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    return names[Math.round(azimuth / 45) % 8];
  }

  function rankOf(d: District, slot: number) {
    const mine = d.selectableKept[slot];
    let better = 1;
    for (let i = 0; i < d.selectableKept.length; i++) {
      if (d.selectableKept[i] > mine) better++;
    }
    return better;
  }

  studyEl.addEventListener("pointermove", (e) => {
    const plane = study.pick(cellRects(), e.clientX, e.clientY);
    study.setHover(plane);
    studyEl.style.cursor = plane >= 0 ? "pointer" : "crosshair";
  });

  studyEl.addEventListener("click", (e) => {
    const plane = study.pick(cellRects(), e.clientX, e.clientY);
    if (plane < 0 || district.slotOfPlane[plane] < 0) return;
    selected = plane;
    study.setSelected(plane);
    describe(plane);
  });

  // ---- sweep --------------------------------------------------------------
  let playing = false;
  let raf = 0;
  let last = performance.now();
  let position = Number(slider.value);

  const playButton = $<HTMLButtonElement>("#play");
  playButton.addEventListener("click", () => {
    playing = !playing;
    playButton.innerHTML = playing ? "&#10073;&#10073;&nbsp; pause" : "&#9654;&nbsp; play";
    last = performance.now();
  });

  slider.addEventListener("input", () => {
    playing = false;
    playButton.innerHTML = "&#9654;&nbsp; play";
    position = Number(slider.value);
    applyStep(position);
  });

  function frame(now: number) {
    const dt = (now - last) / 1000;
    last = now;

    if (playing) {
      // A whole day in about twelve seconds, then round again.
      position += dt * (m.sweep.steps / 12);
      if (position > m.sweep.steps - 1) position = 0;
      slider.value = String(Math.round(position));
      applyStep(Math.round(position));
    }

    study.render(cellRects());
    raf = requestAnimationFrame(frame);
  }

  applyStep(position);

  // Open on the roof the study has most to say about: the worst-shaded plane
  // in the district. A page that opens on nothing selected teaches nothing.
  let worst = 0;
  for (let i = 1; i < district.selectableKept.length; i++) {
    if (district.selectableKept[i] < district.selectableKept[worst]) worst = i;
  }
  selected = district.selectablePlane[worst];
  study.setSelected(selected);
  describe(selected);

  raf = requestAnimationFrame(frame);
  window.addEventListener("beforeunload", () => cancelAnimationFrame(raf));
}

main().catch((error) => {
  document.body.innerHTML =
    `<pre style="padding:24px;font-family:monospace">Could not load the district.\n\n${error}\n\n` +
    `If this is a fresh clone, the bundle should be at web/public/district/. ` +
    `Rebuild it with: python scripts/build_district.py</pre>`;
  throw error;
});
