/**
 * Solar position, ported from backend/suncokret/sun.py.
 *
 * The same NOAA series, in the same order, with the same constants. It is a
 * port and not a reimplementation on purpose: tests/unit/sun.test.ts checks it
 * against values the Python module produced, so the two cannot drift apart
 * without a build failing.
 *
 * No model runs here. These are closed-form astronomical series.
 */

const JULIAN_UNIX_EPOCH = 2440587.5;
const SECONDS_PER_DAY = 86400;

const rad = (d: number) => (d * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

export interface SunPosition {
  /** Degrees above the horizon, negative at night. */
  elevation: number;
  /** Compass bearing: 0 north, 90 east, 180 south, 270 west. */
  azimuth: number;
}

export function julianCentury(unixSeconds: number): number {
  const jd = JULIAN_UNIX_EPOCH + unixSeconds / SECONDS_PER_DAY;
  return (jd - 2451545.0) / 36525.0;
}

export function solarPosition(
  unixSeconds: number,
  latitude: number,
  longitude: number,
): SunPosition {
  const t = julianCentury(unixSeconds);

  const meanLong = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360;
  const meanAnom = 357.52911 + t * (35999.05029 - 0.0001537 * t);
  const m = rad(meanAnom);

  const centre =
    Math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t)) +
    Math.sin(2 * m) * (0.019993 - 0.000101 * t) +
    Math.sin(3 * m) * 0.000289;

  const trueLong = meanLong + centre;
  const omega = 125.04 - 1934.136 * t;
  const appLong = trueLong - 0.00569 - 0.00478 * Math.sin(rad(omega));

  const meanObliq =
    23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60;
  const obliq = meanObliq + 0.00256 * Math.cos(rad(omega));

  const declination = Math.asin(Math.sin(rad(obliq)) * Math.sin(rad(appLong)));

  const y = Math.tan(rad(obliq) / 2) ** 2;
  const ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
  const l = rad(meanLong);
  const eqTime =
    4 *
    deg(
      y * Math.sin(2 * l) -
        2 * ecc * Math.sin(m) +
        4 * ecc * y * Math.sin(m) * Math.cos(2 * l) -
        0.5 * y * y * Math.sin(4 * l) -
        1.25 * ecc * ecc * Math.sin(2 * m),
    );

  const minutesUtc = ((unixSeconds / 60) % 1440 + 1440) % 1440;
  const trueSolarMinutes = ((minutesUtc + eqTime + 4 * longitude) % 1440 + 1440) % 1440;
  const hourAngle = rad(trueSolarMinutes / 4 - 180);

  const lat = rad(latitude);
  const sinElev =
    Math.sin(lat) * Math.sin(declination) +
    Math.cos(lat) * Math.cos(declination) * Math.cos(hourAngle);
  const elevation = deg(Math.asin(Math.max(-1, Math.min(1, sinElev))));

  let azimuth = deg(
    Math.atan2(
      Math.sin(hourAngle),
      Math.cos(hourAngle) * Math.sin(lat) - Math.tan(declination) * Math.cos(lat),
    ),
  );
  azimuth = (azimuth + 180) % 360;
  if (azimuth < 0) azimuth += 360;

  return { elevation, azimuth };
}

/** Unit vector pointing at the sun, in an east-north-up frame. */
export function sunVector(p: SunPosition): [number, number, number] {
  const e = rad(p.elevation);
  const a = rad(p.azimuth);
  const cosE = Math.cos(e);
  return [cosE * Math.sin(a), cosE * Math.cos(a), Math.sin(e)];
}

/**
 * Cosine of the angle between the beam and a plane's normal.
 * Negative means the sun is behind the plane, which is a different thing from
 * being blocked by a neighbour, so the sign is kept.
 */
export function incidenceCosine(
  p: SunPosition,
  planeTiltDeg: number,
  planeAzimuthDeg: number,
): number {
  const e = rad(p.elevation);
  const a = rad(p.azimuth);
  const tilt = rad(planeTiltDeg);
  const paz = rad(planeAzimuthDeg);
  return (
    Math.sin(e) * Math.cos(tilt) +
    Math.cos(e) * Math.sin(tilt) * Math.cos(a - paz)
  );
}
