/**
 * Load the district bundle written by scripts/build_district.py.
 *
 * The manifest describes the binary rather than the binary being implied by the
 * loader, so if the builder changes a dtype the loader fails loudly here
 * instead of quietly reading nonsense.
 */

export interface BufferSpec {
  offset: number;
  length: number;
  dtype: string;
  count: number;
}

export interface SweepSun {
  date: number;
  step: number;
  elevation: number;
  azimuth: number;
}

export interface Manifest {
  district: string;
  title: string;
  note: string;
  centre: { lat: number; lon: number };
  radius_m: number;
  context_m: number;
  crs: string;
  quantisation: { extent_m: number; scale: number };
  counts: {
    buildings_loaded: number;
    faces_rendered: number;
    triangles: number;
    selectable_planes: number;
    simplified_buildings: number;
    wkb_repaired: number;
  };
  sweep: {
    dates: string[];
    start_hour: number;
    end_hour: number;
    step_minutes: number;
    steps: number;
    bytes_per_state: number;
    encoding: string;
    sun: SweepSun[];
  };
  ground: {
    cells: number;
    cell_m: number;
    size_m: number;
    x0_m: number;
    y0_m: number;
    z_m: number;
    march_m: number;
    bytes_per_state: number;
    encoding: string;
    note: string;
  };
  horizon: { bins: number; encoding: string; radius_m: number; height_field_m: number };
  irradiance: {
    source: string;
    radiation_db: string;
    hours: number;
    start_unix: number;
    step_seconds: number;
    latitude: number;
    longitude: number;
    ground_albedo: number;
    note: string;
  };
  buffers: Record<string, BufferSpec>;
  bin: string;
  bin_bytes: number;
}

export interface District {
  manifest: Manifest;
  /** Metres, east-north-up about the district centre. */
  positions: Float32Array;
  planeOfTriangle: Int32Array;
  planeTilt: Float32Array;
  planeAzimuth: Float32Array;
  planeArea: Float32Array;
  planeBuilding: Int32Array;
  planeFlags: Uint8Array;
  selectablePlane: Int32Array;
  selectableKept: Float32Array;
  selectableBeamKept: Float32Array;
  selectableSkyKept: Float32Array;
  selectablePoa: Float32Array;
  horizons: Uint8Array;
  litStates: Uint8Array;
  groundStates: Uint8Array;
  hourlyBeamHorizontal: Float32Array;
  hourlyDiffuseHorizontal: Float32Array;
  /** plane index to slot in the selectable arrays, or -1. */
  slotOfPlane: Int32Array;
}

export const FLAG_ROOF = 1;
export const FLAG_FLAT = 2;
export const FLAG_SIMPLIFIED = 4;
export const FLAG_SELECTABLE = 8;

const CTORS: Record<string, { new (b: ArrayBuffer, o: number, n: number): unknown }> = {
  int16: Int16Array,
  int32: Int32Array,
  float32: Float32Array,
  uint8: Uint8Array,
};

function view<T>(bin: ArrayBuffer, spec: BufferSpec, expected: string): T {
  if (spec.dtype !== expected) {
    throw new Error(`bundle buffer dtype is ${spec.dtype}, loader expects ${expected}`);
  }
  const Ctor = CTORS[spec.dtype];
  if (!Ctor) throw new Error(`unsupported dtype ${spec.dtype}`);
  return new Ctor(bin, spec.offset, spec.count) as T;
}

export async function loadDistrict(name: string): Promise<District> {
  const manifest: Manifest = await fetch(`district/${name}.json`).then((r) => {
    if (!r.ok) throw new Error(`district manifest ${name}: ${r.status}`);
    return r.json();
  });
  const bin = await fetch(`district/${manifest.bin}`).then((r) => {
    if (!r.ok) throw new Error(`district binary ${manifest.bin}: ${r.status}`);
    return r.arrayBuffer();
  });

  const b = manifest.buffers;
  const quantised = view<Int16Array>(bin, b.positions, "int16");

  // Back to metres. The builder scaled by the largest absolute coordinate so
  // the whole district uses the full 16-bit range.
  const scale = manifest.quantisation.extent_m / manifest.quantisation.scale;
  const positions = new Float32Array(quantised.length);
  for (let i = 0; i < quantised.length; i++) positions[i] = quantised[i] * scale;

  const planeFlags = view<Uint8Array>(bin, b.planeFlags, "uint8");
  const selectablePlane = view<Int32Array>(bin, b.selectablePlane, "int32");
  const slotOfPlane = new Int32Array(planeFlags.length).fill(-1);
  for (let s = 0; s < selectablePlane.length; s++) slotOfPlane[selectablePlane[s]] = s;

  return {
    manifest,
    positions,
    planeOfTriangle: view<Int32Array>(bin, b.planeOfTriangle, "int32"),
    planeTilt: view<Float32Array>(bin, b.planeTilt, "float32"),
    planeAzimuth: view<Float32Array>(bin, b.planeAzimuth, "float32"),
    planeArea: view<Float32Array>(bin, b.planeArea, "float32"),
    planeBuilding: view<Int32Array>(bin, b.planeBuilding, "int32"),
    planeFlags,
    selectablePlane,
    selectableKept: view<Float32Array>(bin, b.selectableKept, "float32"),
    selectableBeamKept: view<Float32Array>(bin, b.selectableBeamKept, "float32"),
    selectableSkyKept: view<Float32Array>(bin, b.selectableSkyKept, "float32"),
    selectablePoa: view<Float32Array>(bin, b.selectablePoa, "float32"),
    horizons: view<Uint8Array>(bin, b.horizons, "uint8"),
    litStates: view<Uint8Array>(bin, b.litStates, "uint8"),
    groundStates: view<Uint8Array>(bin, b.groundStates, "uint8"),
    hourlyBeamHorizontal: view<Float32Array>(
      bin, b.hourlyBeamHorizontal, "float32"),
    hourlyDiffuseHorizontal: view<Float32Array>(
      bin, b.hourlyDiffuseHorizontal, "float32"),
    slotOfPlane,
  };
}

/** Is face `plane` lit at sweep step `step` on date `date`? */
export function isLit(
  d: District,
  date: number,
  step: number,
  plane: number,
): boolean {
  const words = d.manifest.sweep.bytes_per_state;
  const base = (date * d.manifest.sweep.steps + step) * words;
  return (d.litStates[base + (plane >> 3)] & (1 << (plane & 7))) !== 0;
}

/** Does the sun reach ground cell `index` at sweep step `step` on `date`? */
export function isGroundSunlit(
  d: District,
  date: number,
  step: number,
  index: number,
): boolean {
  const words = d.manifest.ground.bytes_per_state;
  const base = (date * d.manifest.sweep.steps + step) * words;
  return (d.groundStates[base + (index >> 3)] & (1 << (index & 7))) !== 0;
}

/** The horizon profile of a selectable plane, in degrees. */
export function horizonOf(d: District, slot: number): Float32Array {
  const bins = d.manifest.horizon.bins;
  const out = new Float32Array(bins);
  const base = slot * bins;
  for (let i = 0; i < bins; i++) out[i] = d.horizons[base + i] / 2;
  return out;
}
