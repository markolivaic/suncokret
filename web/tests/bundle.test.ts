import { describe, expect, it } from "vitest";

import { decodeDistrict, FLAG_SELECTABLE, isGroundSunlit, isLit } from "../src/bundle";
import { unpackStates } from "../src/study";
import { committedDistrict } from "./district";

const district = committedDistrict();
const m = district.manifest;

describe("the committed bundle decodes", () => {
  it("into arrays of the sizes the manifest declares", () => {
    expect(district.planeFlags.length).toBe(m.counts.faces_rendered);
    expect(district.planeOfTriangle.length).toBe(m.counts.triangles);
    expect(district.positions.length).toBe(m.counts.triangles * 9);
    expect(district.selectablePlane.length).toBe(m.counts.selectable_planes);
    expect(district.horizons.length).toBe(m.counts.selectable_planes * m.horizon.bins);
    expect(district.hourlyBeamHorizontal.length).toBe(m.irradiance.hours);
  });

  it("with every selectable plane flagged and mapped back to its slot", () => {
    district.selectablePlane.forEach((plane, slot) => {
      expect(district.planeFlags[plane] & FLAG_SELECTABLE).toBe(FLAG_SELECTABLE);
      expect(district.slotOfPlane[plane]).toBe(slot);
    });
  });

  it("with a named street within 60 m of every selectable plane", () => {
    district.selectableStreet.forEach((street, slot) => {
      expect(m.streets.names[street]).toBeTruthy();
      expect(district.selectableStreetDistance[slot]).toBeLessThan(60);
    });
  });

  it("and refuses a binary of the wrong length", () => {
    expect(() => decodeDistrict(m, new ArrayBuffer(m.bin_bytes - 4))).toThrow(/bytes/);
  });
});

describe("the textures the study uploads", () => {
  it("say the same as the bundle's own bit readers", () => {
    const planes = district.planeFlags.length;
    const groundCells = m.ground.cells * m.ground.cells;
    for (const [date, step] of [[0, 24], [1, 15], [2, 33]]) {
      const lit = new Uint8Array(planes);
      unpackStates(district.litStates, (date * m.sweep.steps + step) * m.sweep.bytes_per_state, planes, lit, 0);
      for (let p = 0; p < planes; p++) {
        expect(lit[p] === 255).toBe(isLit(district, date, step, p));
      }
      const ground = new Uint8Array(groundCells);
      unpackStates(district.groundStates, (date * m.sweep.steps + step) * m.ground.bytes_per_state, groundCells, ground, 0);
      for (let i = 0; i < groundCells; i += 97) {
        expect(ground[i] === 255).toBe(isGroundSunlit(district, date, step, i));
      }
    }
  });
});
