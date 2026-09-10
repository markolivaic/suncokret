"""Build the committed district bundle the web app reads.

Everything expensive happens here, once, offline. The browser gets geometry, one
horizon profile per roof plane, and a per-plane summary. That split comes
straight out of E0: a district costs tens of seconds to shade and a single roof
costs milliseconds, so the district is precomputed and everything the sweep does
afterwards is cheap enough to be live.

    python scripts/build_district.py --district kaptol-donji-grad

Writes web/public/district/<name>.json and .bin, plus a build report next to the
other survey artefacts.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import platform
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyogrio
from pyproj import Transformer

from suncokret.irradiance import (
    GROUND_ALBEDO,
    Components,
    annual_kwh_per_m2,
    plane_irradiance,
)
from suncokret.roofs import (
    MIN_SEGMENT_AREA_M2,
    faces_from_geometries,
)
from suncokret.shading import (
    DEFAULT_AZIMUTH_BINS,
    DEFAULT_DENSIFY_M,
    DEFAULT_HEIGHT_FIELD_M,
    DEFAULT_RADIUS_M,
    PointGrid,
    SkyGrid,
    beam_visible,
    densify_segments,
    ground_sunlit,
    height_raster,
    horizon_profile,
    reduce_to_height_field,
    sky_view_factor,
)
from suncokret.sun import solar_position
from suncokret.wkb import from_wkb

warnings.filterwarnings("ignore", message=".*Measured.*")

# Centred on Jurisiceva so the site runs from Trg bana Jelacica down to
# Zrinjevac. A viewer has to be able to see that this is Zagreb and not a
# generic city, and what does that here is the perimeter block with its
# courtyard, repeated, which is the form Donji grad was laid out in.
#
# The cathedral is not in it. Its south wall is about 45 m north of the top
# edge of the square, and nothing inside the site reaches even 40 m above the
# ground. Moving the centre north would bring a 105 m spire in and it would
# then set the vertical extent of all nine frames on its own.
DISTRICTS = {
    "kaptol-donji-grad": {
        "lat": 45.8122,
        "lon": 15.9787,
        "radius_m": 130.0,
        "title": "Kaptol and Donji grad",
        "note": "Trg bana Jelacica, Jurisiceva, and the blocks down to Zrinjevac",
    },
}

# Rendered beyond the selectable core so buildings at the edge are shaded by
# something rather than standing at the edge of an empty world.
#
# The site is deliberately small. At 700 m across a roof plane is three or
# four pixels in one of the nine frames and the study says nothing; at this
# size it is a handful of Donji grad blocks and the shadows are legible,
# which is the whole point of drawing them.
CONTEXT_M = 50.0

# A face smaller than this is a chimney or a modelling sliver. Keeping them
# triples the vertex count and changes no shadow a viewer can see.
MIN_RENDER_AREA_M2 = 1.0

# Positions are quantised to 16-bit integers about the district centre. At the
# scale of a district that is a resolution of roughly a centimetre, far finer
# than LoD 2.2 geometry is accurate to.
QUANT = 32767.0

# Resolution of the ground shadow, across the site. At 192 a cell is about two
# metres, which is a pixel or two in one of the nine frames, so a shadow edge
# reads as an edge rather than as a staircase.
GROUND_CELLS = 192

PVGIS_TIME_FORMAT = "%Y%m%d:%H%M"

# The three dates the study is drawn for, and the local-clock window the sweep
# runs through. The window is wider than any of them has daylight, so the ends
# of a December sweep are genuinely dark rather than clipped.
STUDY_DATES = [
    ("21 December", 2020, 12, 21, 1),
    ("21 March", 2020, 3, 21, 1),
    ("21 June", 2020, 6, 21, 2),
]
SWEEP_START_H, SWEEP_END_H, SWEEP_STEP_MIN = 4.0, 21.0, 20


def load_area(gdb, transformer, lat, lon, radius_m):
    dlat = radius_m / 110_540.0
    dlon = radius_m / (111_320.0 * np.cos(np.radians(lat)))
    bbox = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)

    meta, fids, wkbs, fields = pyogrio.raw.read(gdb, bbox=bbox, return_fids=True)
    geoms, repaired = from_wkb(wkbs)
    geoms = np.asarray(geoms, dtype=object)
    idx = np.flatnonzero([g is not None for g in geoms])
    n = len(geoms)
    faces, volume, decided = faces_from_geometries(geoms[idx], transformer, idx, n_buildings=n)
    attrs = dict(zip(meta["fields"], fields, strict=True))
    return faces, np.asarray(fids), attrs, n, len(repaired)


def load_components(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    hourly = payload["outputs"]["hourly"]
    stamps = np.array(
        [
            datetime.strptime(r["time"], PVGIS_TIME_FORMAT).replace(tzinfo=UTC).timestamp()
            for r in hourly
        ]
    )
    lat = payload["inputs"]["location"]["latitude"]
    lon = payload["inputs"]["location"]["longitude"]
    elevation, azimuth = solar_position(stamps, lat, lon)
    # The browser recomputes the year for a selected roof from these, and it
    # assumes a regular step, so an irregular series has to stop the build
    # rather than quietly shift every hour of the carpet.
    gaps = np.unique(np.round(np.diff(stamps)).astype(int))
    if len(gaps) != 1:
        raise SystemExit(f"PVGIS series is not evenly spaced: steps {gaps}")

    return (
        Components(
            timestamps=stamps,
            beam_horizontal=np.array([r["Gb(i)"] for r in hourly], dtype=float),
            diffuse_horizontal=np.array([r["Gd(i)"] for r in hourly], dtype=float),
            sun_elevation=elevation,
            sun_azimuth=azimuth,
        ),
        payload,
        int(gaps[0]),
    )


def triangulate(faces, keep):
    """Fan-triangulate the kept faces. ZG3D rings are quads and small polygons."""
    positions, plane_of_triangle = [], []
    for new_id, fi in enumerate(np.flatnonzero(keep)):
        ring = faces.ring(fi)[:-1]  # drop the repeated closing vertex
        if len(ring) < 3:
            continue
        for k in range(1, len(ring) - 1):
            positions.append(ring[0])
            positions.append(ring[k])
            positions.append(ring[k + 1])
            plane_of_triangle.append(new_id)
    return np.asarray(positions, dtype=np.float64), np.asarray(plane_of_triangle, dtype=np.int32)


SAMPLE_CHECK_PLANES = 200
SAMPLES_PER_PLANE = 5


def plane_samples(faces, fi, k):
    """Centroid plus points pulled in from the ring, as the E0 benchmark used."""
    ring = faces.ring(fi)
    v = ring[:-1] if len(ring) > 3 else ring
    centre = faces.centroid[fi]
    if k <= 1 or len(v) == 0:
        return centre[None, :]
    picks = v[np.linspace(0, len(v) - 1, min(k - 1, len(v))).astype(int)]
    return np.vstack([centre[None, :], centre + 0.6 * (picks - centre)])


def sampling_penalty(faces, sel_positions, grid, components, sky, kept, beam_kept):
    """How much the one-point-per-plane bundle differs from five points."""
    rng = np.random.default_rng(0)
    n = min(SAMPLE_CHECK_PLANES, len(sel_positions))
    slots = rng.choice(len(sel_positions), n, replace=False)

    d_kept, d_beam = [], []
    for slot in slots:
        fi = sel_positions[slot]
        tilt = float(faces.tilt[fi])
        azi = float(faces.azimuth[fi])
        samples = plane_samples(faces, fi, SAMPLES_PER_PLANE)

        lit = np.zeros(len(components))
        ratio = 0.0
        for point in samples:
            profile = horizon_profile(point, grid.near(point, DEFAULT_RADIUS_M))
            lit += beam_visible(profile, components.sun_elevation, components.sun_azimuth)
            obstructed, open_total = sky_view_factor(profile, tilt, azi, sky)
            ratio += obstructed / max(open_total, 1e-9)
        lit /= len(samples)
        ratio /= len(samples)

        multi = plane_irradiance(components, tilt, azi, beam_lit=lit, sky_kept=ratio)
        d_kept.append(float(kept[slot]) - multi.kept())
        d_beam.append(float(beam_kept[slot]) - multi.beam_kept())

    d_kept = np.asarray(d_kept)
    d_beam = np.asarray(d_beam)
    return {
        "planes_checked": int(n),
        "note": (
            "bundle value minus five-sample value; positive means the single "
            "centroid sample reports a roof as less shaded than it is"
        ),
        "kept_mean_difference": round(float(d_kept.mean()), 5),
        "kept_max_difference": round(float(np.abs(d_kept).max()), 5),
        "beam_kept_mean_difference": round(float(d_beam.mean()), 5),
        "beam_kept_max_difference": round(float(np.abs(d_beam).max()), 5),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--district", default="kaptol-donji-grad")
    ap.add_argument("--gdb", default=None)
    ap.add_argument("--pvgis", default="data/pvgis/zagreb-2020.json")
    ap.add_argument("--out", default="web/public/district")
    ap.add_argument("--report", default="data/survey/district_build.json")
    args = ap.parse_args()

    spec = DISTRICTS[args.district]
    gdb = args.gdb or next(iter(glob.glob("data/raw/*.gdb")), None)
    if not gdb or not Path(gdb).exists():
        raise SystemExit("No .gdb found. Run scripts/fetch_zg3d.py first.")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3765", always_xy=True)
    components, series, step_seconds = load_components(Path(args.pvgis))
    radiation_db = series["inputs"]["meteo_data"]["radiation_db"]
    series_lat = series["inputs"]["location"]["latitude"]
    series_lon = series["inputs"]["location"]["longitude"]
    print(f"PVGIS {radiation_db}, {len(components)} hours")

    t0 = time.perf_counter()
    load_radius = spec["radius_m"] + CONTEXT_M + DEFAULT_RADIUS_M
    faces, fids, attrs, n_buildings, repaired = load_area(
        gdb, transformer, spec["lat"], spec["lon"], load_radius
    )
    load_s = time.perf_counter() - t0
    print(f"loaded {n_buildings} buildings, {len(faces)} faces in {load_s:.1f}s")

    centre = np.array(transformer.transform(spec["lon"], spec["lat"]))
    # Chebyshev, not Euclidean: a square site reads as a drawing sheet, and
    # a circular one reads as a bug.
    offset = np.abs(faces.centroid[:, :2] - centre)
    distance = np.maximum(offset[:, 0], offset[:, 1])

    # Provenance travels with every plane, because it varies building by
    # building and the interface has to show it per building.
    godina = np.asarray(attrs["Godina_izv"], dtype="U10")
    roof_faces_per_building = np.zeros(n_buildings, dtype=np.int32)
    roof_mask = faces.is_roof & (faces.area >= MIN_SEGMENT_AREA_M2)
    np.add.at(roof_faces_per_building, faces.building[roof_mask], 1)
    simplified_building = (godina == "2008") & (roof_faces_per_building <= 1)

    # What gets drawn: everything upward-or-vertical inside the render radius.
    render = (
        (faces.area >= MIN_RENDER_AREA_M2)
        & (~faces.is_downward)
        & (distance <= spec["radius_m"] + CONTEXT_M)
    )
    # What can be selected and carries a horizon: usable roof planes in the core.
    selectable = (
        render
        & faces.is_roof
        & (faces.area >= MIN_SEGMENT_AREA_M2)
        & (distance <= spec["radius_m"])
    )

    print(f"rendering {render.sum()} faces, {selectable.sum()} selectable roof planes")

    # Blockers come from the whole loaded area, including the margin, so a roof
    # on the rim is shaded by its real neighbours rather than by nothing.
    t = time.perf_counter()
    blocker_mask = faces.is_roof & (faces.area >= MIN_RENDER_AREA_M2)
    segments, _ = faces.edges(blocker_mask)
    points, _ = densify_segments(segments, DEFAULT_DENSIFY_M)
    height_field = reduce_to_height_field(points, DEFAULT_HEIGHT_FIELD_M)
    grid = PointGrid(height_field)
    blocker_s = time.perf_counter() - t
    print(f"blocker height field {len(height_field)} points in {blocker_s:.1f}s")

    render_idx = np.flatnonzero(render)
    render_rank = -np.ones(len(faces), dtype=np.int64)
    render_rank[render_idx] = np.arange(len(render_idx))

    sky = SkyGrid()
    t = time.perf_counter()

    # A horizon is only ever consulted for a plane a reader can select, so only
    # those carry one. Storing a profile per rendered face instead made the
    # bundle sixty megabytes of mostly zeros.
    # A horizon is computed for every face that gets drawn, not only for the ones
    # a reader can select. Without that, the context buildings around the edge
    # would render permanently in sunlight and the study would be a lie about
    # exactly the thing it exists to show.
    t_horizon = time.perf_counter()
    all_horizons = np.zeros((len(render_idx), DEFAULT_AZIMUTH_BINS), dtype=np.float32)
    for count, fi in enumerate(render_idx):
        origin = faces.centroid[fi]
        all_horizons[count] = horizon_profile(origin, grid.near(origin, DEFAULT_RADIUS_M))
        if count % 5000 == 0:
            print(f"  horizon {count}/{len(render_idx)}", flush=True)

    horizon_s = time.perf_counter() - t_horizon
    print(f"horizons for {len(render_idx)} drawn faces in {horizon_s:.1f}s")

    t = time.perf_counter()
    sel_positions = np.flatnonzero(selectable)
    horizons = np.zeros((len(sel_positions), DEFAULT_AZIMUTH_BINS), dtype=np.uint8)
    kept = np.ones(len(sel_positions), dtype=np.float32)
    beam_kept = np.ones(len(sel_positions), dtype=np.float32)
    sky_kept = np.ones(len(sel_positions), dtype=np.float32)
    poa = np.zeros(len(sel_positions), dtype=np.float32)
    selectable_plane = render_rank[sel_positions].astype(np.int32)

    for count, fi in enumerate(sel_positions):
        r = count
        profile = all_horizons[render_rank[fi]]
        # Store as half-degree steps above the horizon; below it is open sky.
        horizons[r] = np.clip(np.round(np.maximum(profile, 0.0) * 2.0), 0, 255).astype(np.uint8)

        tilt = float(faces.tilt[fi])
        azi = float(faces.azimuth[fi])
        lit = beam_visible(profile, components.sun_elevation, components.sun_azimuth)
        obstructed, open_total = sky_view_factor(profile, tilt, azi, sky)
        ratio = obstructed / max(open_total, 1e-9)

        result = plane_irradiance(components, tilt, azi, beam_lit=lit, sky_kept=ratio)
        kept[r] = result.kept()
        beam_kept[r] = result.beam_kept()
        sky_kept[r] = ratio
        poa[r] = annual_kwh_per_m2(result.total)

        if count % 250 == 0:
            print(f"  {count}/{len(sel_positions)} planes", flush=True)
    irradiance_s = time.perf_counter() - t
    print(f"irradiance for {len(sel_positions)} planes in {irradiance_s:.1f}s")

    # The bundle carries one horizon per plane, taken at its centroid, because a
    # horizon per corner would multiply the download by five. That is a choice
    # with a cost, so the cost is measured rather than asserted: the same planes
    # are redone with five sample points and the difference is reported.
    sampling = sampling_penalty(faces, sel_positions, grid, components, sky, kept, beam_kept)
    print(json.dumps(sampling, indent=2))

    # ---- geometry -----------------------------------------------------------
    positions, plane_of_triangle = triangulate(faces, render)
    local = positions - np.array([centre[0], centre[1], 0.0])
    extent = float(np.abs(local).max())
    quantised = np.round(local / extent * QUANT).astype(np.int16)

    plane_tilt = faces.tilt[render_idx].astype(np.float32)
    plane_azimuth = np.nan_to_num(faces.azimuth[render_idx], nan=-1.0).astype(np.float32)
    plane_area = faces.area[render_idx].astype(np.float32)
    plane_building = faces.building[render_idx].astype(np.int32)
    plane_flags = np.zeros(len(render_idx), dtype=np.uint8)
    plane_flags |= (faces.is_roof[render_idx]).astype(np.uint8) * 1
    plane_flags |= (faces.is_flat[render_idx]).astype(np.uint8) * 2
    plane_flags |= (simplified_building[plane_building]).astype(np.uint8) * 4
    plane_flags |= (selectable[render_idx]).astype(np.uint8) * 8

    # ---- the ground the shadows fall on -------------------------------------
    # ZG3D models buildings and not terrain, so the study stands them on one
    # level plane at the median base height of the buildings it draws. Donji
    # grad is flat enough for that to cost little and it is still an
    # assumption, so it is written into the manifest and into the colophon
    # rather than left for a reader to discover.
    z_min = np.asarray(attrs["Z_Min"], dtype=float)
    ground_z = float(np.nanmedian(z_min[np.unique(faces.building[render_idx])]))

    # A square of ground exactly under the drawing. Square because the site cut
    # is square and a sheet with a rectangle of city on it is the drawing this
    # study is imitating.
    drawn_lo = positions[:, :2].min(axis=0)
    drawn_hi = positions[:, :2].max(axis=0)
    ground_size = float((drawn_hi - drawn_lo).max())
    gx0, gy0 = (drawn_lo + drawn_hi) / 2 - ground_size / 2
    ground_cell = ground_size / GROUND_CELLS

    # The raster reaches a march radius beyond the ground on every side, so a
    # shadow thrown from outside the drawn site still lands inside it.
    t = time.perf_counter()
    margin = int(np.ceil(DEFAULT_RADIUS_M / ground_cell))
    hx0, hy0 = gx0 - margin * ground_cell, gy0 - margin * ground_cell
    hn = GROUND_CELLS + 2 * margin
    blocker_tri, _ = triangulate(faces, blocker_mask)
    ground_raster = height_raster(blocker_tri, hx0, hy0, ground_cell, hn, hn)
    raster_s = time.perf_counter() - t
    print(
        f"ground raster {hn}x{hn} at {ground_cell:.2f} m from "
        f"{len(blocker_tri) // 3} triangles in {raster_s:.1f}s"
    )

    axis = gx0 + (np.arange(GROUND_CELLS) + 0.5) * ground_cell
    ground_x, ground_y = np.meshgrid(axis, gy0 + (np.arange(GROUND_CELLS) + 0.5) * ground_cell)

    # ---- sweep states -------------------------------------------------------
    # Lit or not, per drawn face, at every step of the sweep for every date.
    # One bit each, so the whole animation is a lookup in the browser and no
    # geometry work happens while it plays.
    t = time.perf_counter()
    steps = int(round((SWEEP_END_H - SWEEP_START_H) * 60 / SWEEP_STEP_MIN)) + 1
    sweep_hours = SWEEP_START_H + np.arange(steps) * (SWEEP_STEP_MIN / 60.0)

    normals = faces.normal[render_idx]
    words = (len(render_idx) + 7) // 8
    lit_states = np.zeros((len(STUDY_DATES), steps, words), dtype=np.uint8)
    ground_words = (GROUND_CELLS * GROUND_CELLS + 7) // 8
    ground_states = np.zeros((len(STUDY_DATES), steps, ground_words), dtype=np.uint8)
    sweep_sun = []

    for di, (_, yy, mm, dd, tz) in enumerate(STUDY_DATES):
        for si, hour in enumerate(sweep_hours):
            stamp = datetime(yy, mm, dd, tzinfo=UTC) + timedelta(hours=float(hour) - tz)
            el, az = solar_position(np.array([stamp.timestamp()]), spec["lat"], spec["lon"])
            el, az = float(el[0]), float(az[0])
            sweep_sun.append(
                {"date": di, "step": si, "elevation": round(el, 3), "azimuth": round(az, 3)}
            )
            if el <= 0.0:
                continue
            bins = min(int(az / 360.0 * DEFAULT_AZIMUTH_BINS), DEFAULT_AZIMUTH_BINS - 1)
            above = el > all_horizons[:, bins]
            facing = (
                normals[:, 0] * np.cos(np.radians(el)) * np.sin(np.radians(az))
                + normals[:, 1] * np.cos(np.radians(el)) * np.cos(np.radians(az))
                + normals[:, 2] * np.sin(np.radians(el))
            ) > 0
            lit_states[di, si] = np.packbits(above & facing, bitorder="little")

            sunlit = ground_sunlit(
                ground_raster,
                hx0,
                hy0,
                ground_cell,
                ground_x,
                ground_y,
                ground_z,
                el,
                az,
                DEFAULT_RADIUS_M,
            )
            ground_states[di, si] = np.packbits(sunlit.reshape(-1), bitorder="little")
    sweep_s = time.perf_counter() - t
    print(f"sweep: {len(STUDY_DATES)} dates x {steps} steps in {sweep_s:.1f}s")

    buffers = [
        ("positions", quantised.astype("<i2")),
        ("planeOfTriangle", plane_of_triangle.astype("<i4")),
        ("planeTilt", plane_tilt.astype("<f4")),
        ("planeAzimuth", plane_azimuth.astype("<f4")),
        ("planeArea", plane_area.astype("<f4")),
        ("planeBuilding", plane_building.astype("<i4")),
        ("planeFlags", plane_flags),
        ("selectablePlane", selectable_plane.astype("<i4")),
        ("selectableKept", kept.astype("<f4")),
        ("selectableBeamKept", beam_kept.astype("<f4")),
        ("selectableSkyKept", sky_kept.astype("<f4")),
        ("selectablePoa", poa.astype("<f4")),
        ("horizons", horizons),
        ("litStates", lit_states.reshape(-1)),
        ("groundStates", ground_states.reshape(-1)),
        ("hourlyBeamHorizontal", components.beam_horizontal.astype("<f4")),
        ("hourlyDiffuseHorizontal", components.diffuse_horizontal.astype("<f4")),
    ]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    blob = bytearray()
    layout = {}
    for name, array in buffers:
        data = array.tobytes()
        layout[name] = {
            "offset": len(blob),
            "length": len(data),
            "dtype": str(array.dtype),
            "count": int(array.size),
        }
        blob.extend(data)
        # keep every buffer 4-byte aligned so typed array views are cheap
        while len(blob) % 4:
            blob.append(0)

    bin_path = out_dir / f"{args.district}.bin"
    bin_path.write_bytes(bytes(blob))

    manifest = {
        "generated_by": "scripts/build_district.py",
        "generated_on": time.strftime("%Y-%m-%d"),
        "district": args.district,
        "title": spec["title"],
        "note": spec["note"],
        "centre": {"lat": spec["lat"], "lon": spec["lon"]},
        "radius_m": spec["radius_m"],
        "context_m": CONTEXT_M,
        "crs": "EPSG:3765 (HTRS96 / Croatia TM)",
        "quantisation": {"extent_m": extent, "scale": QUANT},
        "counts": {
            "buildings_loaded": int(n_buildings),
            "faces_rendered": int(render.sum()),
            "triangles": int(len(plane_of_triangle)),
            "selectable_planes": int(selectable.sum()),
            "simplified_buildings": int(simplified_building.sum()),
            "wkb_repaired": int(repaired),
        },
        "sweep": {
            "dates": [d[0] for d in STUDY_DATES],
            "start_hour": SWEEP_START_H,
            "end_hour": SWEEP_END_H,
            "step_minutes": SWEEP_STEP_MIN,
            "steps": steps,
            "bytes_per_state": words,
            "encoding": "one bit per rendered face, little-endian, 1 means lit",
            "sun": sweep_sun,
        },
        "ground": {
            "cells": GROUND_CELLS,
            "cell_m": ground_cell,
            "size_m": ground_size,
            "x0_m": float(gx0 - centre[0]),
            "y0_m": float(gy0 - centre[1]),
            "z_m": ground_z,
            "march_m": DEFAULT_RADIUS_M,
            "raster_cell_m": ground_cell,
            "bytes_per_state": ground_words,
            "encoding": "one bit per ground cell, little-endian, 1 means the sun reaches it",
            "note": (
                "one level plane at the median building base. ZG3D models "
                "buildings, not terrain, so the ground here is an assumption "
                "and not a measurement."
            ),
        },
        "horizon": {
            "bins": DEFAULT_AZIMUTH_BINS,
            "encoding": "uint8, half a degree per step, 0 means open sky",
            "radius_m": DEFAULT_RADIUS_M,
            "height_field_m": DEFAULT_HEIGHT_FIELD_M,
        },
        "irradiance": {
            "source": "PVGIS seriescalc, horizontal plane, components split",
            "radiation_db": radiation_db,
            "hours": int(len(components)),
            "start_unix": float(components.timestamps[0]),
            "step_seconds": step_seconds,
            "latitude": series_lat,
            "longitude": series_lon,
            "ground_albedo": GROUND_ALBEDO,
            "note": (
                "planePoa is plane-of-array irradiance in kWh/m2/year, which is "
                "sunlight arriving on a surface. It is not electricity and this "
                "project never converts it into electricity."
            ),
        },
        "buffers": layout,
        "bin": bin_path.name,
        "bin_bytes": len(blob),
        "bin_sha256": hashlib.sha256(bytes(blob)).hexdigest(),
    }
    (out_dir / f"{args.district}.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    report = {
        "generated_by": "scripts/build_district.py",
        "generated_on": time.strftime("%Y-%m-%d"),
        "machine": (
            f"{platform.system()} {platform.machine()}, Python {platform.python_version()}"
        ),
        "district": args.district,
        "timing_s": {
            "note": (
                "the horizon pass covers every drawn face because the drawing "
                "shades all of them; the irradiance pass covers only the planes "
                "a reader can select, so the two are reported per their own "
                "population rather than divided into each other"
            ),
            "load": round(load_s, 2),
            "blockers": round(blocker_s, 2),
            "horizons": round(horizon_s, 2),
            "ground_raster": round(raster_s, 2),
            "horizon_per_face_ms": round(1000 * horizon_s / max(len(render_idx), 1), 3),
            "irradiance": round(irradiance_s, 2),
            "irradiance_per_plane_ms": round(1000 * irradiance_s / max(len(sel_positions), 1), 3),
        },
        "counts": manifest["counts"],
        "bundle_bytes": {"bin": len(blob)},
        "ground": {
            "cells": GROUND_CELLS,
            "cell_m": round(ground_cell, 3),
            "size_m": round(ground_size, 1),
            "z_m": round(ground_z, 2),
        },
        "kept_distribution": {
            "planes": int(len(sel_positions)),
            "mean": round(float(kept.mean()), 4),
            "p10": round(float(np.percentile(kept, 10)), 4),
            "median": round(float(np.median(kept)), 4),
            "worst": round(float(kept.min()), 4),
        },
        "beam_kept_distribution": {
            "mean": round(float(beam_kept.mean()), 4),
            "worst": round(float(beam_kept.min()), 4),
        },
        "single_sample_penalty": sampling,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report["counts"], indent=2))
    print(json.dumps(report["kept_distribution"], indent=2))
    print(f"bundle {len(blob) / 1e6:.2f} MB -> {bin_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
