"""What inter-building shading costs to compute, and how much beam it takes.

Three questions:

    1. Cost per roof and per district, over four Zagreb neighbourhoods that span
       the range of built form.
    2. How much direct beam a roof loses to its neighbours.
    3. What the two approximations cost: the height-field resolution the blocker
       cloud is reduced to, and the radius beyond which neighbours are ignored.

Every roof is scored by ``suncokret.irradiance.shade_plane``, the same function
the district bundle uses: beam weighted by direct normal irradiance from the
committed PVGIS series, daylight hours only. Flat and pitched planes are
reported apart, because a flat roof and a pitched one lose beam differently.

    python scripts/benchmark_shading.py

Writes data/survey/shading_benchmark.json.
"""

from __future__ import annotations

import argparse
import glob
import json
import platform
import time
import warnings
from pathlib import Path

import numpy as np
import pyogrio
from pyproj import Transformer

from suncokret.irradiance import shade_plane
from suncokret.pvgis import load_components
from suncokret.roofs import (
    MIN_BUILDING_ROOF_AREA_M2,
    MIN_SEGMENT_AREA_M2,
    faces_from_geometries,
)
from suncokret.shading import (
    DEFAULT_DENSIFY_M,
    DEFAULT_HEIGHT_FIELD_M,
    DEFAULT_RADIUS_M,
    PointGrid,
    SkyGrid,
    densify_segments,
    horizon_profile,
    reduce_to_height_field,
)
from suncokret.wkb import from_wkb

warnings.filterwarnings("ignore", message=".*Measured.*")

# Four neighbourhoods spanning the range of built form in Zagreb.
DISTRICTS = {
    "donji-grad": (45.8085, 15.9760, "dense 19th century perimeter blocks"),
    "tresnjevka": (45.8020, 15.9450, "mixed low-rise and slab housing"),
    "novi-zagreb-travno": (45.7760, 15.9840, "tall socialist-era slabs, widely spaced"),
    "sesvete": (45.8320, 16.1130, "near-suburban detached housing"),
}

DISTRICT_RADIUS_M = 400.0
# Blockers are gathered beyond the district edge, otherwise buildings on the rim
# are shaded by nothing and the district average is optimistic.
BLOCKER_MARGIN_M = DEFAULT_RADIUS_M

SAMPLES_PER_PLANE = 5
SENSITIVITY_DISTRICTS = ("donji-grad", "tresnjevka")
SENSITIVITY_BUILDINGS = 500


def load_area(gdb, transformer, lat, lon, radius_m):
    """Buildings within a radius of a point, as faces."""
    # Degrees per metre at this latitude, used only to size the query box.
    dlat = radius_m / 110_540.0
    dlon = radius_m / (111_320.0 * np.cos(np.radians(lat)))
    bbox = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)

    _, fids, wkbs, _ = pyogrio.raw.read(gdb, bbox=bbox, return_fids=True)
    geoms, _ = from_wkb(wkbs)
    geoms = np.asarray(geoms, dtype=object)
    idx = np.flatnonzero([g is not None for g in geoms])
    n = len(geoms)
    faces, _, _ = faces_from_geometries(geoms[idx], transformer, idx, n_buildings=n)
    return faces, n


def blocker_cloud(faces, n_buildings, height_field_m, flat_top=False):
    """Densified roof-edge points, reduced to a building height field.

    Only roof faces are used. A wall's top edge is a roof edge already, and a
    floor slab sits below its own walls, so neither can be the highest
    obstruction in any direction.

    With ``flat_top`` every point is lifted to its own building's highest face
    centroid, which turns each building into a box for comparison.
    """
    mask = faces.is_roof & (faces.area >= 1.0)
    segments, owners = faces.edges(mask)
    if len(segments) == 0:
        return np.empty((0, 3))

    points, from_segment = densify_segments(segments, DEFAULT_DENSIFY_M)

    if flat_top:
        top = np.zeros(n_buildings)
        np.maximum.at(top, faces.building, faces.centroid[:, 2])
        points = points.copy()
        points[:, 2] = top[owners[from_segment]]

    return reduce_to_height_field(points, height_field_m)


def plane_samples(faces, fi, k):
    """The centroid plus points pulled in from the ring, spread over the plane."""
    ring = faces.ring(fi)
    v = ring[:-1] if len(ring) > 3 else ring
    centre = faces.centroid[fi]
    if k <= 1 or len(v) == 0:
        return centre[None, :]
    picks = v[np.linspace(0, len(v) - 1, min(k - 1, len(v))).astype(int)]
    # Pulled in from the edge so a sample sits on the plane, not on its boundary.
    return np.vstack([centre[None, :], centre + 0.6 * (picks - centre)])


def largest_roof_face(faces, n_buildings):
    """Index of each building's biggest usable roof plane, or -1."""
    best = np.full(n_buildings, -1, dtype=np.int64)
    roof = faces.is_roof & (faces.area >= MIN_SEGMENT_AREA_M2)
    idx = np.flatnonzero(roof)
    if len(idx) == 0:
        return best
    order = idx[np.lexsort((faces.area[idx], faces.building[idx]))]
    owner = faces.building[order]
    last = np.ones(len(order), dtype=bool)
    last[:-1] = owner[:-1] != owner[1:]
    sel = order[last]
    best[faces.building[sel]] = sel
    return best


def evaluate(faces, targets, best, grid, components, sky, k, radius):
    """Beam kept, sky view kept and flatness of each building's largest plane.

    Returns the three arrays plus the sample and candidate counts for timing.
    """
    beam, view, flat = [], [], []
    n_samples = n_candidates = 0
    for b in targets:
        fi = best[b]
        if fi < 0:
            continue
        horizons = []
        for s in plane_samples(faces, fi, k):
            candidates = grid.near(s, radius)
            n_candidates += len(candidates)
            horizons.append(horizon_profile(s, candidates, radius=radius))
        n_samples += len(horizons)

        result = shade_plane(
            components, float(faces.tilt[fi]), float(faces.azimuth[fi]), horizons, sky
        )
        beam.append(result.beam_kept())
        view.append(result.sky_kept())
        flat.append(bool(faces.is_flat[fi]))
    return np.array(beam), np.array(view), np.array(flat, dtype=bool), n_samples, n_candidates


def stats(a):
    if len(a) == 0:
        return None
    return {
        "n": int(len(a)),
        "mean": round(float(a.mean()), 4),
        "p10": round(float(np.percentile(a, 10)), 4),
        "median": round(float(np.median(a)), 4),
        "p90": round(float(np.percentile(a, 90)), 4),
        "worst": round(float(a.min()), 4),
    }


def split(a, flat):
    """Summary over all planes, and over pitched and flat ones apart."""
    return {"all": stats(a), "pitched": stats(a[~flat]), "flat": stats(a[flat])}


def district_context(name, gdb, transformer, radius_m):
    """Everything about a district that does not depend on the settings."""
    lat, lon, description = DISTRICTS[name]
    t = time.perf_counter()
    faces, n_buildings = load_area(gdb, transformer, lat, lon, radius_m)
    load_s = time.perf_counter() - t
    if len(faces) == 0:
        return None

    centre_xy = np.array(transformer.transform(lon, lat))
    pv_area = np.zeros(n_buildings)
    pv = faces.is_roof & (faces.area >= MIN_SEGMENT_AREA_M2)
    np.add.at(pv_area, faces.building[pv], faces.area[pv])

    b_xy = np.zeros((n_buildings, 2))
    b_n = np.zeros(n_buildings)
    np.add.at(b_xy, faces.building, faces.centroid[:, :2])
    np.add.at(b_n, faces.building, 1)
    b_xy /= np.maximum(b_n, 1)[:, None]

    inside = (np.linalg.norm(b_xy - centre_xy, axis=1) <= DISTRICT_RADIUS_M) & (
        pv_area >= MIN_BUILDING_ROOF_AREA_M2
    )
    return {
        "name": name,
        "description": description,
        "lat": lat,
        "lon": lon,
        "faces": faces,
        "n_buildings": n_buildings,
        "targets": np.flatnonzero(inside),
        "best": largest_roof_face(faces, n_buildings),
        "load_s": load_s,
    }


def run_district(ctx, components, sky, k):
    faces, n = ctx["faces"], ctx["n_buildings"]

    t = time.perf_counter()
    points = blocker_cloud(faces, n, DEFAULT_HEIGHT_FIELD_M)
    grid = PointGrid(points)
    blocker_s = time.perf_counter() - t

    t = time.perf_counter()
    beam, view, flat, n_samples, n_cand = evaluate(
        faces, ctx["targets"], ctx["best"], grid, components, sky, k, DEFAULT_RADIUS_M
    )
    shading_s = time.perf_counter() - t

    # Does the modelled roof shape matter, or would a box at ridge height do?
    flat_points = blocker_cloud(faces, n, DEFAULT_HEIGHT_FIELD_M, flat_top=True)
    beam_box, _, _, _, _ = evaluate(
        faces,
        ctx["targets"],
        ctx["best"],
        PointGrid(flat_points),
        components,
        sky,
        k,
        DEFAULT_RADIUS_M,
    )
    delta = beam_box - beam

    return {
        "district": ctx["name"],
        "description": ctx["description"],
        "centre": {"lat": ctx["lat"], "lon": ctx["lon"]},
        "radius_m": DISTRICT_RADIUS_M,
        "buildings_loaded_including_margin": int(n),
        "buildings_evaluated": int(len(beam)),
        "flat_planes": int(flat.sum()),
        "blocker_points": int(len(points)),
        "samples_per_plane": k,
        "timing_s": {
            "load": round(ctx["load_s"], 3),
            "build_blockers": round(blocker_s, 3),
            "shading": round(shading_s, 3),
            "per_building_ms": round(1000 * shading_s / max(len(beam), 1), 3),
            "per_sample_ms": round(1000 * shading_s / max(n_samples, 1), 3),
        },
        "mean_blocker_candidates_per_sample": int(n_cand / max(n_samples, 1)),
        "beam_kept_fraction": split(beam, flat),
        "sky_view_kept_fraction": split(view, flat),
        "box_at_ridge_height_error": {
            "note": (
                "beam kept with every building lifted to a box at its highest "
                "face, minus beam kept with the modelled roof shape; negative "
                "means the box shades more than the real roofs do"
            ),
            "mean_delta": round(float(delta.mean()), 5) if len(delta) else None,
            "max_abs_delta": round(float(np.abs(delta).max()), 5) if len(delta) else None,
            "buildings_over_1pct": int((np.abs(delta) > 0.01).sum()) if len(delta) else None,
        },
    }


def _errors(got, ref, flat):
    d = np.abs(got - ref)
    out = {
        "mean_abs_error": round(float(d.mean()), 5),
        "p95_abs_error": round(float(np.percentile(d, 95)), 5),
        "max_abs_error": round(float(d.max()), 5),
    }
    for name, mask in (("pitched", ~flat), ("flat", flat)):
        out[f"mean_abs_error_{name}"] = round(float(d[mask].mean()), 5) if mask.any() else None
    return out


def sensitivity(ctx, components, sky, k, n_sample_buildings):
    """Cost and answer against the two approximations, on one district."""
    faces, n = ctx["faces"], ctx["n_buildings"]
    rng = np.random.default_rng(0)
    targets = ctx["targets"][ctx["best"][ctx["targets"]] >= 0]
    if len(targets) > n_sample_buildings:
        targets = rng.choice(targets, n_sample_buildings, replace=False)

    # Reference: the undecimated cloud at the full radius.
    mask = faces.is_roof & (faces.area >= 1.0)
    segments, _ = faces.edges(mask)
    raw, _ = densify_segments(segments, DEFAULT_DENSIFY_M)
    ref_beam, ref_view, flat, _, _ = evaluate(
        faces, targets, ctx["best"], PointGrid(raw), components, sky, k, DEFAULT_RADIUS_M
    )

    resolution = []
    for hf in (1.0, 2.0, 3.0, 5.0):
        pts = blocker_cloud(faces, n, hf)
        t = time.perf_counter()
        beam, view, _, ns, _ = evaluate(
            faces, targets, ctx["best"], PointGrid(pts), components, sky, k, DEFAULT_RADIUS_M
        )
        ms = 1000 * (time.perf_counter() - t) / max(ns, 1)
        resolution.append(
            {
                "height_field_m": hf,
                "blocker_points": int(len(pts)),
                "per_sample_ms": round(ms, 3),
                "beam_kept": _errors(beam, ref_beam, flat),
                "sky_view_mean_abs_error": round(float(np.abs(view - ref_view).mean()), 5),
            }
        )

    grid = PointGrid(blocker_cloud(faces, n, DEFAULT_HEIGHT_FIELD_M))
    far_beam, _, _, _, _ = evaluate(faces, targets, ctx["best"], grid, components, sky, k, 500.0)
    radius = []
    for r in (50.0, 100.0, 150.0, 250.0):
        t = time.perf_counter()
        beam, _, _, ns, nc = evaluate(faces, targets, ctx["best"], grid, components, sky, k, r)
        ms = 1000 * (time.perf_counter() - t) / max(ns, 1)
        radius.append(
            {
                "radius_m": r,
                "per_sample_ms": round(ms, 3),
                "mean_candidates": int(nc / max(ns, 1)),
                "beam_kept_vs_500m": _errors(beam, far_beam, flat),
            }
        )

    return {
        "district": ctx["name"],
        "buildings_sampled": int(len(flat)),
        "flat_planes": int(flat.sum()),
        "note": (
            "errors are against the undecimated cloud for resolution, and "
            "against a 500 m search for radius"
        ),
        "height_field_resolution": resolution,
        "search_radius": radius,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gdb", default=None)
    ap.add_argument("--pvgis", default="data/pvgis/zagreb-2020.json")
    ap.add_argument("--out", default="data/survey/shading_benchmark.json")
    ap.add_argument("--samples", type=int, default=SAMPLES_PER_PLANE)
    ap.add_argument("--districts", nargs="*", default=list(DISTRICTS))
    ap.add_argument("--sensitivity-on", nargs="*", default=list(SENSITIVITY_DISTRICTS))
    ap.add_argument("--sensitivity-buildings", type=int, default=SENSITIVITY_BUILDINGS)
    args = ap.parse_args()

    gdb = args.gdb or next(iter(glob.glob("data/raw/*.gdb")), None)
    if not gdb or not Path(gdb).exists():
        raise SystemExit("No .gdb found. Run scripts/fetch_zg3d.py first.")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3765", always_xy=True)
    components, payload, _ = load_components(Path(args.pvgis))
    sky = SkyGrid()
    print(f"{len(components)} hours, {int((components.sun_elevation > 0).sum())} of them daylight")

    results = []
    sens = []
    t0 = time.perf_counter()
    for name in args.districts:
        print(f"  {name} ...", flush=True)
        ctx = district_context(name, gdb, transformer, DISTRICT_RADIUS_M + BLOCKER_MARGIN_M)
        if ctx is None:
            continue
        r = run_district(ctx, components, sky, args.samples)
        results.append(r)
        print(
            f"    {r['buildings_evaluated']} buildings, "
            f"{r['timing_s']['shading']:.1f}s, "
            f"{r['timing_s']['per_building_ms']:.1f} ms/building, "
            f"beam kept mean {r['beam_kept_fraction']['all']['mean']:.3f}",
            flush=True,
        )
        if name in args.sensitivity_on:
            print("    sensitivity ...", flush=True)
            sens.append(sensitivity(ctx, components, sky, args.samples, args.sensitivity_buildings))
    total_s = time.perf_counter() - t0

    per_building = [r["timing_s"]["per_building_ms"] for r in results]
    summary = {
        "generated_by": "scripts/benchmark_shading.py",
        "generated_on": time.strftime("%Y-%m-%d"),
        "machine": (
            f"{platform.system()} {platform.machine()}, Python {platform.python_version()}"
        ),
        "method": {
            "horizon_azimuth_bins": 720,
            "blocker_radius_m": DEFAULT_RADIUS_M,
            "edge_densify_m": DEFAULT_DENSIFY_M,
            "height_field_m": DEFAULT_HEIGHT_FIELD_M,
            "samples_per_plane": args.samples,
            "hours_per_year": int(len(components)),
            "irradiance": f"PVGIS {payload['inputs']['meteo_data']['radiation_db']}",
            "metric": (
                "beam kept is the direct-normal-weighted share of the plane's "
                "unobstructed beam that still reaches it, over daylight hours, "
                "from suncokret.irradiance.shade_plane"
            ),
        },
        "districts": results,
        "sensitivity": sens,
        "cost_envelope": {
            "per_building_ms_min": round(min(per_building), 3) if per_building else None,
            "per_building_ms_max": round(max(per_building), 3) if per_building else None,
            "total_wall_clock_s": round(total_s, 2),
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
