/**
 * The year for one roof plane: day of year across, hour of day up.
 *
 * This is the summary the nine views are three columns out of. It is computed
 * in the browser rather than baked, because the horizon profile is only 720
 * bytes and the whole year is one pass over it, so a click can afford it.
 *
 * Colour is the only saturated thing on the page and it always means the same
 * quantity: how much of the irradiance this plane could have received in that
 * hour actually reached it.
 */

import { type District, horizonOf } from "./bundle";
import { incidenceCosine, solarPosition } from "./sun";

/** Perceptually ordered ramp, dark at none and pale at full. */
const RAMP: [number, number, number][] = [
  [29, 22, 48],
  [58, 47, 82],
  [125, 63, 106],
  [200, 100, 63],
  [239, 155, 23],
  [253, 239, 184],
];

function rampAt(t: number): [number, number, number] {
  const x = Math.max(0, Math.min(1, t)) * (RAMP.length - 1);
  const i = Math.min(Math.floor(x), RAMP.length - 2);
  const f = x - i;
  const a = RAMP[i];
  const b = RAMP[i + 1];
  return [
    a[0] + (b[0] - a[0]) * f,
    a[1] + (b[1] - a[1]) * f,
    a[2] + (b[2] - a[2]) * f,
  ];
}

export interface CarpetResult {
  /** Fraction of plane-of-array irradiance kept, over the year. */
  kept: number;
  /** Fraction of the direct beam kept. */
  beamKept: number;
  /** Fraction of the sky the neighbours leave. */
  skyKept: number;
  /** Plane-of-array irradiance, kWh/m2/year. Sunlight, not electricity. */
  poa: number;
  /** Hours the sun was up and the plane faced it but a building was in the way. */
  hoursStolen: number;
}

export function drawCarpet(
  canvas: HTMLCanvasElement,
  district: District,
  plane: number,
): CarpetResult | null {
  const slot = district.slotOfPlane[plane];
  if (slot < 0) return null;

  const m = district.manifest;
  const horizon = horizonOf(district, slot);
  const bins = m.horizon.bins;
  const tilt = district.planeTilt[plane];
  const azimuthRaw = district.planeAzimuth[plane];
  const azimuth = azimuthRaw < 0 ? 180 : azimuthRaw; // flat planes face nowhere
  const skyKept = district.selectableSkyKept[slot];

  const hours = m.irradiance.hours;
  const start = m.irradiance.start_unix;
  const step = m.irradiance.step_seconds;
  const lat = m.irradiance.latitude;
  const lon = m.irradiance.longitude;

  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  // One column per day, one row per hour. The image is built at that native
  // resolution and then stretched, so no hour is dropped by the scaling.
  const days = Math.ceil(hours / 24);
  const image = ctx.createImageData(days, 24);

  let beamGot = 0;
  let beamOpen = 0;
  let totalGot = 0;
  let totalOpen = 0;
  let stolen = 0;

  const cosTilt = Math.cos((tilt * Math.PI) / 180);
  const skyView = (1 + cosTilt) / 2;
  const groundView = (1 - cosTilt) / 2;
  const albedo = m.irradiance.ground_albedo;

  for (let i = 0; i < hours; i++) {
    const t = start + i * step;
    const sun = solarPosition(t, lat, lon);

    const gb = district.hourlyBeamHorizontal[i];
    const gd = district.hourlyDiffuseHorizontal[i];

    let openBeam = 0;
    let lit = true;
    if (sun.elevation > 2) {
      const cosInc = Math.max(0, incidenceCosine(sun, tilt, azimuth));
      const dni = gb / Math.sin((sun.elevation * Math.PI) / 180);
      openBeam = dni * cosInc;
      const bin = Math.min(Math.floor((sun.azimuth / 360) * bins), bins - 1);
      lit = sun.elevation > horizon[bin];
      if (!lit && openBeam > 0) stolen++;
    }

    const beam = lit ? openBeam : 0;
    const diffuseOpen = gd * skyView;
    const diffuse = diffuseOpen * skyKept;
    const reflected = (gb + gd) * albedo * groundView;

    beamGot += beam;
    beamOpen += openBeam;
    totalGot += beam + diffuse + reflected;
    totalOpen += openBeam + diffuseOpen + reflected;

    // Paint. The scale is fixed across roofs so two carpets can be compared.
    const day = Math.floor(i / 24);
    const hour = i % 24;
    const value = (beam + diffuse) / 900;
    const [r, g, b] = rampAt(value);
    const px = ((23 - hour) * days + day) * 4;
    image.data[px] = r;
    image.data[px + 1] = g;
    image.data[px + 2] = b;
    image.data[px + 3] = 255;
  }

  const bitmap = document.createElement("canvas");
  bitmap.width = days;
  bitmap.height = 24;
  bitmap.getContext("2d")!.putImageData(image, 0, 0);

  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);

  // A building modelled with one roof plane gets the same hatch it has in the
  // drawing, so the caveat travels with the number.
  if ((district.planeFlags[plane] & 4) !== 0) {
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

  return {
    kept: totalOpen > 0 ? totalGot / totalOpen : 1,
    beamKept: beamOpen > 0 ? beamGot / beamOpen : 1,
    skyKept,
    poa: totalGot / 1000,
    hoursStolen: stolen,
  };
}
