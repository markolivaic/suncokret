"""E0.2 - what inter-building shading costs to compute, and whether it matters.

Three questions, and the first decides the shape of the product. If a district
takes hours, the product is a precomputed atlas. If it takes seconds, part of it
can be live.

    1. Cost per roof and per district, over real Zagreb neighbourhoods chosen to
       span the density range rather than to flatter the number.
    2. Whether the effect is large enough to be worth computing at all.
    3. What the two approximations cost: the height-field resolution the blocker
       cloud is reduced to, and the radius beyond which neighbours are ignored.
       Both are reported as sensitivities rather than asserted.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyogrio
from pyproj import Transformer

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
    beam_visible,
    densify_segments,
    horizon_profile,
    reduce_to_height_field,
    sky_view_factor,
)
from suncokret.sun import incidence_cosine, solar_position
from suncokret.wkb import from_wkb

warnings.filterwarnings("ignore", message=".*Measured.*")

# Four real neighbourhoods spanning the range of built form, picked before
# anything was measured so the set is not chosen to produce a number. Travno
# went in as a guess at the worst case, on the grounds that it holds the tallest
# residential slabs in the city. The guess was wrong and the measurement is left
# in: a roof on top of the tallest thing around has nothing to shade it, and
# what actually costs a roof its winter is a uniform street wall at its own
# height, which is Donji grad.
DISTRICTS = {
    "donji-grad": (45.8085, 15.9760, "dense 19th century perimeter blocks"),
    "tresnjevka": (45.8020, 15.9450, "mixed low-rise and slab housing"),
    "novi-zagreb-travno": (45.7760, 15.9840, "tall socialist-era slabs, widely spaced"),
    "sesvete": (45.8320, 16.1130, "near-suburban detached housing"),
}

DISTRICT_RADIUS_M = 400.0
# Blockers are gathered beyond the district edge, otherwise buildings on the rim
# are shaded by nothing and the district average is quietly optimistic.
BLOCKER_MARGIN_M = DEFAULT_RADIUS_M

SAMPLES_PER_PLANE = 5
YEAR = 2020
ZAGREB_LAT, ZAGREB_LON = 45.8150, 15.9819


def hourly_timestamps(year: int) -> np.ndarray:
    """Mid-hour instants for a whole year, UTC."""
    start = datetime(year, 1, 1, 0, 30, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    n = int((end - start).total_seconds() // 3600) + 1
    return np.array([(start + timedelta(hours=i)).timestamp() for i in range(n)])


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
    """Densified silhouette points, reduced to a building height field.

    Only roof faces are used. A wall's top edge is a roof edge already, and a
    floor slab always sits below its own walls, so neither can ever be the
    highest obstruction in any direction.

    With ``flat_top`` every point is lifted to its own building's ridge, which
    is the box approximation the modelled roof shape is tested against.
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
    """A few points spread over one roof plane, not just its centre.

    A plane sampled only at its centroid is called fully lit while one of its
    corners sits in shadow all winter.
    """
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


def evaluate(faces, targets, best, grid, sun_el, sun_az, sky, k, radius):
    """Beam kept and sky view kept per building, plus work counters."""
    kept, view = [], []
    n_samples = n_candidates = 0
    for b in targets:
        fi = best[b]
        if fi < 0:
            continue
        tilt = float(faces.tilt[fi])
        azi = float(faces.azimuth[fi])
        cos_inc = np.clip(incidence_cosine(sun_el, sun_az, tilt, azi), 0.0, None)

        samples = plane_samples(faces, fi, k)
        lit = np.zeros(len(sun_el))
        svf = 0.0
        for s in samples:
            candidates = grid.near(s, radius)
            n_candidates += len(candidates)
            horizon = horizon_profile(s, candidates, radius=radius)
            lit += beam_visible(horizon, sun_el, sun_az)
            obstructed, open_total = sky_view_factor(horizon, tilt, azi, sky)
            svf += obstructed / max(open_total, 1e-9)
        lit /= len(samples)
        n_samples += len(samples)

        weight = cos_inc.sum()
        kept.append(float((cos_inc * lit).sum() / weight) if weight > 0 else 1.0)
        view.append(svf / len(samples))
    return np.array(kept), np.array(view), n_samples, n_candidates


def stats(a):
    if len(a) == 0:
        return None
    return {
        "mean": round(float(a.mean()), 4),
        "p10": round(float(np.percentile(a, 10)), 4),
        "median": round(float(np.median(a)), 4),
        "p90": round(float(np.percentile(a, 90)), 4),
        "worst": round(float(a.min()), 4),
    }


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


def run_district(ctx, sun_el, sun_az, sky, k):
    faces, n = ctx["faces"], ctx["n_buildings"]

    t = time.perf_counter()
    points = blocker_cloud(faces, n, DEFAULT_HEIGHT_FIELD_M)
    grid = PointGrid(points)
    blocker_s = time.perf_counter() - t

    t = time.perf_counter()
    kept, view, n_samples, n_cand = evaluate(
        faces,
        ctx["targets"],
        ctx["best"],
        grid,
        sun_el,
        sun_az,
        sky,
        k,
        DEFAULT_RADIUS_M,
    )
    shading_s = time.perf_counter() - t

    # Does the modelled roof shape matter, or would a box at ridge height do?
    flat_points = blocker_cloud(faces, n, DEFAULT_HEIGHT_FIELD_M, flat_top=True)
    kept_box, _, _, _ = evaluate(
        faces,
        ctx["targets"],
        ctx["best"],
        PointGrid(flat_points),
        sun_el,
        sun_az,
        sky,
        k,
        DEFAULT_RADIUS_M,
    )
    delta = kept_box - kept

    return {
        "district": ctx["name"],
        "description": ctx["description"],
        "centre": {"lat": ctx["lat"], "lon": ctx["lon"]},
        "radius_m": DISTRICT_RADIUS_M,
        "buildings_loaded_including_margin": int(n),
        "buildings_evaluated": int(len(kept)),
        "blocker_points": int(len(points)),
        "samples_per_plane": k,
        "timing_s": {
            "load": round(ctx["load_s"], 3),
            "build_blockers": round(blocker_s, 3),
            "shading": round(shading_s, 3),
            "per_building_ms": round(1000 * shading_s / max(len(kept), 1), 3),
            "per_sample_ms": round(1000 * shading_s / max(n_samples, 1), 3),
        },
        "mean_blocker_candidates_per_sample": int(n_cand / max(n_samples, 1)),
        "beam_kept_fraction": stats(kept),
        "sky_view_kept_fraction": stats(view),
        "box_at_ridge_height_error": {
            "note": (
                "beam kept with a box-at-ridge-height blocker minus beam kept "
                "with the modelled roof shape; positive means the box shades "
                "less than the real roofs do"
            ),
            "mean_delta": round(float(delta.mean()), 5) if len(delta) else None,
            "max_abs_delta": round(float(np.abs(delta).max()), 5) if len(delta) else None,
            "buildings_over_1pct": int((np.abs(delta) > 0.01).sum()) if len(delta) else None,
        },
    }


def sensitivity(ctx, sun_el, sun_az, sky, k, n_sample_buildings=150):
    """Cost and answer against the two approximations, on one district."""
    faces, n = ctx["faces"], ctx["n_buildings"]
    rng = np.random.default_rng(0)
    targets = ctx["targets"]
    if len(targets) > n_sample_buildings:
        targets = rng.choice(targets, n_sample_buildings, replace=False)

    # Reference: the undecimated cloud at the full radius.
    mask = faces.is_roof & (faces.area >= 1.0)
    segments, _ = faces.edges(mask)
    raw, _ = densify_segments(segments, DEFAULT_DENSIFY_M)
    ref_kept, ref_view, _, _ = evaluate(
        faces,
        targets,
        ctx["best"],
        PointGrid(raw),
        sun_el,
        sun_az,
        sky,
        k,
        DEFAULT_RADIUS_M,
    )

    resolution = []
    for hf in (1.0, 2.0, 3.0, 5.0):
        pts = blocker_cloud(faces, n, hf)
        t = time.perf_counter()
        kept, view, ns, _ = evaluate(
            faces,
            targets,
            ctx["best"],
            PointGrid(pts),
            sun_el,
            sun_az,
            sky,
            k,
            DEFAULT_RADIUS_M,
        )
        ms = 1000 * (time.perf_counter() - t) / max(ns, 1)
        dk, dv = np.abs(kept - ref_kept), np.abs(view - ref_view)
        resolution.append(
            {
                "height_field_m": hf,
                "blocker_points": int(len(pts)),
                "per_sample_ms": round(ms, 3),
                "beam_kept_mean_abs_error": round(float(dk.mean()), 5),
                "beam_kept_p95_abs_error": round(float(np.percentile(dk, 95)), 5),
                "beam_kept_max_abs_error": round(float(dk.max()), 5),
                "sky_view_mean_abs_error": round(float(dv.mean()), 5),
            }
        )

    pts = blocker_cloud(faces, n, DEFAULT_HEIGHT_FIELD_M)
    grid = PointGrid(pts)
    far_kept, far_view, _, _ = evaluate(
        faces, targets, ctx["best"], grid, sun_el, sun_az, sky, k, 500.0
    )
    radius = []
    for r in (50.0, 100.0, 150.0, 250.0, 500.0):
        t = time.perf_counter()
        kept, view, ns, nc = evaluate(faces, targets, ctx["best"], grid, sun_el, sun_az, sky, k, r)
        ms = 1000 * (time.perf_counter() - t) / max(ns, 1)
        dk = np.abs(kept - far_kept)
        radius.append(
            {
                "radius_m": r,
                "per_sample_ms": round(ms, 3),
                "mean_candidates": int(nc / max(ns, 1)),
                "beam_kept_mean_abs_error_vs_500m": round(float(dk.mean()), 5),
                "beam_kept_max_abs_error_vs_500m": round(float(dk.max()), 5),
            }
        )

    return {
        "district": ctx["name"],
        "buildings_sampled": int(len(targets)),
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
    ap.add_argument("--out", default="data/survey/shading_benchmark.json")
    ap.add_argument("--samples", type=int, default=SAMPLES_PER_PLANE)
    ap.add_argument("--districts", nargs="*", default=list(DISTRICTS))
    ap.add_argument("--sensitivity-on", default="donji-grad")
    args = ap.parse_args()

    gdb = args.gdb or next(iter(glob.glob("data/raw/*.gdb")), None)
    if not gdb or not Path(gdb).exists():
        raise SystemExit("No .gdb found. Run scripts/fetch_zg3d.py first.")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3765", always_xy=True)
    stamps = hourly_timestamps(YEAR)
    sun_el, sun_az = solar_position(stamps, ZAGREB_LAT, ZAGREB_LON)
    sky = SkyGrid()
    print(f"{len(stamps)} hours of {YEAR}, {int((sun_el > 0).sum())} of them daylight")

    results = []
    sens = None
    t0 = time.perf_counter()
    for name in args.districts:
        print(f"  {name} ...", flush=True)
        ctx = district_context(name, gdb, transformer, DISTRICT_RADIUS_M + BLOCKER_MARGIN_M)
        if ctx is None:
            continue
        r = run_district(ctx, sun_el, sun_az, sky, args.samples)
        results.append(r)
        print(
            f"    {r['buildings_evaluated']} buildings, "
            f"{r['timing_s']['shading']:.1f}s, "
            f"{r['timing_s']['per_building_ms']:.1f} ms/building, "
            f"beam kept median {r['beam_kept_fraction']['median']:.3f}"
        )
        if name == args.sensitivity_on:
            print("    sensitivity ...", flush=True)
            sens = sensitivity(ctx, sun_el, sun_az, sky, args.samples)
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
            "hours_per_year": int(len(stamps)),
            "note": (
                "one horizon profile per sample point, after which the whole "
                "year is an array lookup against it"
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
