/**
 * The year for one roof plane: day of year across, Zagreb clock hour up.
 *
 * Computed in the browser from the plane's 720-byte horizon profile and the
 * hourly PVGIS components in the bundle. The arithmetic is the same as
 * `shade_plane` in the Python build, and a test holds the two to 1e-3 on every
 * selectable plane.
 */

import { type District, FLAG_SIMPLIFIED, horizonOf } from "./bundle";
import { zagrebDayHour } from "./localtime";
import { incidenceCosine, solarPosition } from "./sun";

/** Below this elevation the direct normal value is not reconstructed (as in Python). */
const MIN_ELEVATION_FOR_DNI_DEG = 2;

/** Top of both colour scales, W/m2. */
export const CARPET_SCALE_W_M2 = 900;

/** Irradiance reaching the roof: dark at none, pale at full. */
const REACHING: [number, number, number][] = [
  [29, 22, 48],
  [58, 47, 82],
  [125, 63, 106],
  [200, 100, 63],
  [239, 155, 23],
  [253, 239, 184],
];

/** Direct sun that a building took: pale at a little, deep at a lot. */
const TAKEN: [number, number, number][] = [
  [205, 211, 224],
  [143, 152, 168],
  [62, 73, 100],
];

/** Hours when the sun is down are left as the paper of the sheet. */
const NIGHT: [number, number, number] = [232, 223, 201];

function rampAt(ramp: [number, number, number][], t: number): [number, number, number] {
  const x = Math.max(0, Math.min(1, t)) * (ramp.length - 1);
  const i = Math.min(Math.floor(x), ramp.length - 2);
  const f = x - i;
  const a = ramp[i];
  const b = ramp[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

export interface PlaneYear {
  /** Share of plane-of-array irradiance kept against the same roof with the city removed. */
  kept: number;
  /** Share of the direct beam kept. */
  beamKept: number;
  /** Share of the sky the neighbours leave. */
  skyKept: number;
  /** Hours with direct sun on the open roof that a building took. */
  hoursTaken: number;
  /** Per hour: sun above the horizon. */
  daylight: Uint8Array;
  /** Per hour: irradiance reaching the roof, W/m2 (beam plus diffuse). */
  reaching: Float32Array;
  /** Per hour: direct beam a building took, W/m2. */
  taken: Float32Array;
}

interface SunSeries {
  elevation: Float64Array;
  azimuth: Float64Array;
  sin: Float64Array;
  positions: ReturnType<typeof solarPosition>[];
}

const sunCache = new WeakMap<District, SunSeries>();

/** The sun for every hour of the bundle's year, computed once per district. */
function sunSeries(district: District): SunSeries {
  const cached = sunCache.get(district);
  if (cached) return cached;
  const m = district.manifest;
  const hours = m.irradiance.hours;
  const series: SunSeries = {
    elevation: new Float64Array(hours),
    azimuth: new Float64Array(hours),
    sin: new Float64Array(hours),
    positions: [],
  };
  for (let i = 0; i < hours; i++) {
    const t = m.irradiance.start_unix + i * m.irradiance.step_seconds;
    const sun = solarPosition(t, m.irradiance.latitude, m.irradiance.longitude);
    series.positions.push(sun);
    series.elevation[i] = sun.elevation;
    series.azimuth[i] = sun.azimuth;
    series.sin[i] = Math.sin((sun.elevation * Math.PI) / 180);
  }
  sunCache.set(district, series);
  return series;
}

function ratio(got: number, open: number): number {
  if (!Number.isFinite(got) || !Number.isFinite(open)) {
    throw new Error("irradiance sums are not finite");
  }
  return open > 0 ? got / open : 1;
}

/** Hour by hour irradiance on one selectable plane, or null if it has no horizon. */
export function planeYear(district: District, plane: number): PlaneYear | null {
  const slot = district.slotOfPlane[plane];
  if (slot < 0) return null;

  const m = district.manifest;
  const horizon = horizonOf(district, slot);
  const bins = m.horizon.bins;
  const tilt = district.planeTilt[plane];
  const azimuth = district.planeAzimuth[plane];
  const skyKept = district.selectableSkyKept[slot];

  const hours = m.irradiance.hours;
  const cosTilt = Math.cos((tilt * Math.PI) / 180);
  const skyView = (1 + cosTilt) / 2;
  const groundView = (1 - cosTilt) / 2;
  const albedo = m.irradiance.ground_albedo;

  const daylight = new Uint8Array(hours);
  const reaching = new Float32Array(hours);
  const taken = new Float32Array(hours);

  let beamGot = 0;
  let beamOpen = 0;
  let totalGot = 0;
  let totalOpen = 0;
  let hoursTaken = 0;
  const sun = sunSeries(district);

  for (let i = 0; i < hours; i++) {
    const gb = district.hourlyBeamHorizontal[i];
    const gd = district.hourlyDiffuseHorizontal[i];
    const elevation = sun.elevation[i];

    let openBeam = 0;
    let lit = true;
    if (elevation >= MIN_ELEVATION_FOR_DNI_DEG) {
      const cosInc = Math.max(0, incidenceCosine(sun.positions[i], tilt, azimuth));
      openBeam = (gb / sun.sin[i]) * cosInc;
      const bin = Math.min(Math.floor((sun.azimuth[i] / 360) * bins), bins - 1);
      lit = elevation > horizon[bin];
    }
    const beam = lit ? openBeam : 0;
    const diffuseOpen = gd * skyView;
    const diffuse = diffuseOpen * skyKept;
    const reflected = (gb + gd) * albedo * groundView;

    beamGot += beam;
    beamOpen += openBeam;
    totalGot += beam + diffuse + reflected;
    totalOpen += openBeam + diffuseOpen + reflected;

    daylight[i] = elevation > 0 ? 1 : 0;
    reaching[i] = beam + diffuse;
    if (!lit && openBeam > 0) {
      taken[i] = openBeam;
      hoursTaken++;
    }
  }

  return {
    kept: ratio(totalGot, totalOpen),
    beamKept: ratio(beamGot, beamOpen),
    skyKept,
    hoursTaken,
    daylight,
    reaching,
    taken,
  };
}

/** Paint the year onto a canvas. Returns the numbers it was painted from. */
export function drawCarpet(
  canvas: HTMLCanvasElement,
  district: District,
  plane: number,
): PlaneYear | null {
  const year = planeYear(district, plane);
  const ctx = canvas.getContext("2d");
  if (!year || !ctx) return null;

  const m = district.manifest;
  const first = m.irradiance.start_unix;
  const calendarYear = new Date(first * 1000).getUTCFullYear();
  const days = Math.round((Date.UTC(calendarYear + 1, 0, 1) - Date.UTC(calendarYear, 0, 1)) / 86400000);

  // One column per local day, one row per local hour, built at that native
  // resolution and stretched so no hour is dropped by scaling. The local hour
  // skipped in spring stays paper; the one repeated in autumn is drawn once.
  const image = ctx.createImageData(days, 24);
  for (let px = 0; px < image.data.length; px += 4) {
    image.data.set([...NIGHT, 255], px);
  }
  for (let i = 0; i < m.irradiance.hours; i++) {
    const t = first + i * m.irradiance.step_seconds;
    const { day, hour } = zagrebDayHour(t, calendarYear);
    if (day < 0 || day >= days || !year.daylight[i]) continue;
    const colour =
      year.taken[i] > 0
        ? rampAt(TAKEN, year.taken[i] / CARPET_SCALE_W_M2)
        : rampAt(REACHING, year.reaching[i] / CARPET_SCALE_W_M2);
    image.data.set([...colour, 255], ((23 - hour) * days + day) * 4);
  }

  const bitmap = document.createElement("canvas");
  bitmap.width = days;
  bitmap.height = 24;
  bitmap.getContext("2d")!.putImageData(image, 0, 0);

  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);

  // A building modelled with one roof plane carries the same hatch it has in
  // the drawing, so the caveat travels with the numbers.
  if ((district.planeFlags[plane] & FLAG_SIMPLIFIED) !== 0) {
    ctx.save();
    ctx.globalAlpha = 0.4;
    ctx.strokeStyle = "#0d1013";
    ctx.lineWidth = 3;
    for (let x = -canvas.height; x < canvas.width; x += 11) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x + canvas.height, canvas.height);
      ctx.stroke();
    }
    ctx.restore();
  }

  return year;
}

/** CSS gradients for the legend, from the same stops the carpet paints with. */
export function legendGradient(which: "reaching" | "taken"): string {
  const ramp = which === "reaching" ? REACHING : TAKEN;
  const stops = ramp.map(
    (c, i) => `rgb(${c.join(",")}) ${((100 * i) / (ramp.length - 1)).toFixed(0)}%`,
  );
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

export const NIGHT_CSS = `rgb(${NIGHT.join(",")})`;
