/**
 * The browser recomputes a roof's year from the bundle; the build computed it
 * in Python. Both are shown on the page, so they are held together here.
 */

import { describe, expect, it } from "vitest";

import { planeYear } from "../src/carpet";
import { committedDistrict } from "./district";

const district = committedDistrict();
const TOLERANCE = 1e-3;

describe("the carpet's year agrees with the build", () => {
  it("on kept and direct sun kept, for every selectable plane", () => {
    let worst = 0;
    district.selectablePlane.forEach((plane, slot) => {
      const year = planeYear(district, plane)!;
      worst = Math.max(
        worst,
        Math.abs(year.kept - district.selectableKept[slot]),
        Math.abs(year.beamKept - district.selectableBeamKept[slot]),
      );
    });
    expect(worst).toBeLessThan(TOLERANCE);
  });

  it("and only counts hours as taken while the sun is up", () => {
    const year = planeYear(district, district.selectablePlane[0])!;
    for (let i = 0; i < year.taken.length; i++) {
      if (year.taken[i] > 0) expect(year.daylight[i]).toBe(1);
    }
  });
});
