"""E0.1 - how much of ZG3D actually carries usable roof-plane geometry.

LoD 2.2 is a claim about the format, not a guarantee about every record. This
sweeps all 357,683 buildings, classifies each one, and writes the survival rate
with its failure modes. It sweeps everything; it does not sample, and every
building gets a row even when its geometry is missing, degenerate or unreadable.

    python scripts/survey_roofs.py

Resumable: chunk results land in data/survey/_chunks/ and are skipped on rerun.
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

from suncokret.roofs import (
    FLAT_MAX_TILT_DEG,
    MIN_BUILDING_ROOF_AREA_M2,
    MIN_SEGMENT_AREA_M2,
    MIN_TRUSTED_AREA_M2,
    ROOF_MAX_TILT_DEG,
    VOLUME_CLOSURE_TOLERANCE,
    faces_from_geometries,
)
from suncokret.wkb import from_wkb

warnings.filterwarnings("ignore", message=".*Measured.*")

CHUNK = 5000
# Sensitivity of the headline to the one judgement call in the pipeline.
TILT_CUTS = (45.0, 60.0, 75.0)


def survey_chunk(geoms, fids, attrs, transformer, repaired):
    """Per-building aggregates for one chunk. One row per building, always."""
    n = len(geoms)
    out = {
        "fid": np.asarray(fids, dtype=np.int64),
        "n_faces": np.zeros(n, dtype=np.int32),
        "n_positive": np.zeros(n, dtype=np.int32),
        "n_roof_faces": np.zeros(n, dtype=np.int32),
        "face_area_sum": np.zeros(n),
        "roof_area": np.zeros(n),
        "flat_area": np.zeros(n),
        "pitched_area": np.zeros(n),
        "downward_area": np.zeros(n),
        "pv_area": np.zeros(n),
        "best_area": np.zeros(n),
        "best_tilt": np.full(n, np.nan),
        "best_azimuth": np.full(n, np.nan),
        "signed_volume": np.zeros(n),
        "repaired_wkb": np.zeros(n, dtype=bool),
        "orientation_decided": np.zeros(n, dtype=bool),
        "z_delta": np.asarray(attrs["Z_Delta"], dtype=np.float64),
        "s_area": np.asarray(attrs["SArea"], dtype=np.float64),
        "volume_attr": np.asarray(attrs["Volume"], dtype=np.float64),
        "z_min": np.asarray(attrs["Z_Min"], dtype=np.float64),
        "z_max": np.asarray(attrs["Z_Max"], dtype=np.float64),
        "izvor": np.asarray(attrs["Izvor"], dtype="U40"),
        "godina": np.asarray(attrs["Godina_izv"], dtype="U10"),
    }
    for cut in TILT_CUTS:
        out[f"roof_area_cut{int(cut)}"] = np.zeros(n)

    if repaired:
        out["repaired_wkb"][np.asarray(repaired, dtype=int)] = True

    present = np.array([g is not None for g in geoms], dtype=bool)
    idx = np.flatnonzero(present)
    if len(idx) == 0:
        return out

    f, volume, decided = faces_from_geometries(geoms[idx], transformer, idx, n_buildings=n)
    out["signed_volume"] = volume
    out["orientation_decided"] = decided
    if len(f) == 0:
        return out

    positive = f.area > 1e-9
    trusted = positive & (f.area >= MIN_TRUSTED_AREA_M2)
    roof = trusted & f.is_roof
    flat = roof & f.is_flat
    pitched = roof & ~f.is_flat
    down = trusted & f.is_downward
    pv = roof & (f.area >= MIN_SEGMENT_AREA_M2)

    def acc(target, mask):
        np.add.at(out[target], f.building[mask], f.area[mask])

    np.add.at(out["n_faces"], f.building, 1)
    np.add.at(out["n_positive"], f.building[positive], 1)
    np.add.at(out["n_roof_faces"], f.building[roof], 1)
    acc("face_area_sum", positive)
    acc("roof_area", roof)
    acc("flat_area", flat)
    acc("pitched_area", pitched)
    acc("downward_area", down)
    acc("pv_area", pv)

    for cut in TILT_CUTS:
        m = trusted & (f.tilt <= cut)
        np.add.at(out[f"roof_area_cut{int(cut)}"], f.building[m], f.area[m])

    # Largest roof plane per building: the one a homeowner would actually use.
    roof_idx = np.flatnonzero(roof)
    if len(roof_idx):
        order = roof_idx[np.lexsort((f.area[roof_idx], f.building[roof_idx]))]
        b_sorted = f.building[order]
        last = np.ones(len(order), dtype=bool)
        last[:-1] = b_sorted[:-1] != b_sorted[1:]
        sel = order[last]
        rows = f.building[sel]
        out["best_area"][rows] = f.area[sel]
        out["best_tilt"][rows] = f.tilt[sel]
        out["best_azimuth"][rows] = f.azimuth[sel]

    return out


def octants(az: np.ndarray) -> dict:
    """Compass octant histogram. Azimuth is NaN for flat planes and excluded."""
    az = az[np.isfinite(az)]
    if len(az) == 0:
        return {}
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    bins = (((az + 22.5) % 360) // 45).astype(int)
    counts = np.bincount(bins, minlength=8)
    return {names[i]: int(counts[i]) for i in range(8)}


def _agreement(rel: np.ndarray) -> dict:
    """Relative-error summary for one population of buildings."""
    if len(rel) == 0:
        return {"buildings_compared": 0}
    return {
        "buildings_compared": int(len(rel)),
        "median_relative_error": round(float(np.median(rel)), 9),
        "p99_relative_error": round(float(np.percentile(rel, 99)), 9),
        "within_1_percent": round(float((rel < 0.01).mean()), 6),
    }


def build_summary(d, info, total, elapsed, read_s):
    n = len(d["fid"])
    has_faces = d["n_faces"] > 0
    has_positive = d["n_positive"] > 0
    has_roof = d["n_roof_faces"] > 0
    recoverable = d["roof_area"] > 0
    pitched = d["pitched_area"] > 0
    pv_ready = d["pv_area"] >= MIN_BUILDING_ROOF_AREA_M2

    # Does the extraction agree with ZG3D's own attributes? These two say the
    # geometry is being read correctly, not merely that the script ran.
    ok_area = d["s_area"] > 0
    rel_area = np.abs(d["face_area_sum"][ok_area] - d["s_area"][ok_area]) / d["s_area"][ok_area]
    ok_vol = d["volume_attr"] > 0
    vol_ratio = np.abs(d["signed_volume"][ok_vol]) / d["volume_attr"][ok_vol]
    closed = np.abs(vol_ratio - 1.0) < VOLUME_CLOSURE_TOLERANCE

    # The same closure mask, aligned to the rows the area check compares.
    closed_all = np.zeros(len(d["fid"]), dtype=bool)
    closed_all[np.flatnonzero(ok_vol)] = closed
    closed_area = closed_all[ok_area]

    az = d["best_azimuth"][pv_ready]
    tl = d["best_tilt"][pv_ready]
    tl = tl[np.isfinite(tl)]
    pitched_tl = tl[tl > FLAT_MAX_TILT_DEG]

    def frac(mask):
        return {"count": int(mask.sum()), "fraction": round(float(mask.mean()), 6)}

    # Provenance is the explanation for almost every failure below, so it is
    # reported next to the failures rather than as a footnote.
    provenance = {}
    pairs = zip(d["izvor"].tolist(), d["godina"].tolist(), strict=True)
    for src in sorted(set(pairs)):
        m = (d["izvor"] == src[0]) & (d["godina"] == src[1])
        provenance[f"{src[0]} ({src[1]})"] = {
            "buildings": int(m.sum()),
            "share_of_dataset": round(float(m.mean()), 6),
            "median_faces_per_building": float(np.median(d["n_faces"][m])),
            "pitched_roof_fraction": round(float(pitched[m].mean()), 6),
            "pv_ready_fraction": round(float(pv_ready[m].mean()), 6),
        }

    return {
        "generated_by": "scripts/survey_roofs.py",
        "generated_on": time.strftime("%Y-%m-%d"),
        "machine": (
            f"{platform.system()} {platform.machine()}, Python {platform.python_version()}"
        ),
        "source": {
            "dataset": "ZG3D 2022 3D model Grada Zagreba, LoD 2.2",
            "layer": str(info["layer_name"]),
            "crs_source": str(info["crs"]),
            "crs_computed_in": "EPSG:3765 (HTRS96 / Croatia TM)",
            "buildings_in_layer": int(info["features"]),
        },
        "thresholds": {
            "roof_max_tilt_deg": ROOF_MAX_TILT_DEG,
            "flat_max_tilt_deg": FLAT_MAX_TILT_DEG,
            "min_trusted_area_m2": MIN_TRUSTED_AREA_M2,
            "min_segment_area_m2": MIN_SEGMENT_AREA_M2,
            "min_building_roof_area_m2": MIN_BUILDING_ROOF_AREA_M2,
        },
        "cascade": {
            "surveyed": n,
            "has_any_face": frac(has_faces),
            "has_positive_area_face": frac(has_positive),
            "has_roof_face": frac(has_roof),
            "pitch_and_azimuth_recoverable": frac(recoverable),
            "has_pitched_roof_modelling": frac(pitched),
            "pv_ready": frac(pv_ready),
        },
        "failure_modes": {
            "no_geometry": frac(~has_faces),
            "all_faces_degenerate": frac(has_faces & ~has_positive),
            "no_face_survives_tilt_cut": frac(has_positive & ~has_roof),
            "roof_below_trusted_area": frac(has_roof & ~recoverable),
            "flat_top_only_no_pitched_planes": frac(recoverable & ~pitched),
            "roof_too_small_for_pv": frac(recoverable & ~pv_ready),
            "wkb_needed_repair": frac(d["repaired_wkb"]),
            "orientation_undecidable": frac(~d["orientation_decided"]),
        },
        "provenance": provenance,
        "degenerate_faces": {
            "faces_total": int(d["n_faces"].sum()),
            "faces_positive_area": int(d["n_positive"].sum()),
            "fraction_zero_area": round(
                1.0 - float(d["n_positive"].sum()) / max(int(d["n_faces"].sum()), 1), 6
            ),
        },
        "area_check_vs_zg3d_sarea_attribute": {
            "note": (
                "split by closure, because the two populations are not "
                "comparable: an open shell is missing its floor, so the "
                "dataset's own SArea counts a face that is not in the geometry "
                "it ships. On the closed solids this is a straight check of the "
                "extraction against the dataset and it is exact"
            ),
            "closed_solids": _agreement(rel_area[closed_area]),
            "open_shells": _agreement(rel_area[~closed_area]),
        },
        "closure_check_vs_zg3d_volume_attribute": {
            "note": (
                "faces are oriented by the highest non-vertical face, which needs "
                "no closed solid; this compares the divergence-theorem volume "
                "against the dataset's own Volume purely to say which buildings "
                "are closed solids and which are open shells"
            ),
            "buildings_compared": int(ok_vol.sum()),
            "closed_solid_fraction": round(float(closed.mean()), 6),
            "open_shells": int((~closed).sum()),
            "median_abs_ratio_closed": round(
                float(np.median(vol_ratio[closed])) if closed.any() else float("nan"), 6
            ),
            "median_abs_ratio_open": round(
                float(np.median(vol_ratio[~closed])) if (~closed).any() else float("nan"),
                4,
            ),
        },
        "tilt_cut_sensitivity_pv_ready": {
            f"cut_{int(c)}deg": int(
                (d[f"roof_area_cut{int(c)}"] >= MIN_BUILDING_ROOF_AREA_M2).sum()
            )
            for c in TILT_CUTS
        },
        "best_plane_distribution_pv_ready": {
            "n": int(len(tl)),
            "flat_fraction": round(float((tl <= FLAT_MAX_TILT_DEG).mean()), 6),
            "median_tilt_of_pitched_deg": (
                round(float(np.median(pitched_tl)), 3) if len(pitched_tl) else None
            ),
            "tilt_percentiles_deg": {
                str(p): round(float(np.percentile(tl, p)), 3) for p in (10, 25, 50, 75, 90)
            },
            "azimuth_octants_of_pitched": octants(az),
        },
        "timing": {
            "wall_clock_s": round(elapsed, 2),
            "read_s": round(read_s, 2),
            "buildings_per_s": round(total / elapsed, 1) if elapsed > 0 else None,
            "note": ("cold run only; a resumed run skips completed chunks and is not comparable"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gdb", default=None, help="path to the extracted .gdb")
    ap.add_argument("--out", default="data/survey")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    gdb = args.gdb or next(iter(glob.glob("data/raw/*.gdb")), None)
    if not gdb or not Path(gdb).exists():
        raise SystemExit("No .gdb found. Run scripts/fetch_zg3d.py first (downloads ~329 MB).")

    out_dir = Path(args.out)
    chunk_dir = out_dir / "_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    info = pyogrio.read_info(gdb, layer=0)
    n_layer = int(info["features"])
    total = n_layer if args.limit is None else min(args.limit, n_layer)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3765", always_xy=True)

    print(f"ZG3D layer {info['layer_name']}: {n_layer} buildings, sweeping {total}")

    t0 = time.perf_counter()
    read_s = 0.0
    done = 0
    computed = 0
    for start in range(0, total, CHUNK):
        n = min(CHUNK, total - start)
        path = chunk_dir / f"chunk_{start:07d}.npz"
        if path.exists():
            done += n
            continue

        tr0 = time.perf_counter()
        meta, fids, wkbs, fields = pyogrio.raw.read(
            gdb, skip_features=start, max_features=n, return_fids=True
        )
        read_s += time.perf_counter() - tr0

        attrs = dict(zip(meta["fields"], fields, strict=True))
        geoms, repaired = from_wkb(wkbs)
        res = survey_chunk(geoms, fids, attrs, transformer, repaired)
        np.savez_compressed(path, **res)

        computed += n
        done += n
        el = time.perf_counter() - t0
        print(
            f"  {done:>7,}/{total:,}  {done / max(el, 1e-9):8.0f} bldg/s  {el:6.1f}s",
            flush=True,
        )

    elapsed = time.perf_counter() - t0

    parts = sorted(chunk_dir.glob("chunk_*.npz"))
    cols: dict[str, list] = {}
    for p in parts:
        with np.load(p) as z:
            for k in z.files:
                cols.setdefault(k, []).append(z[k])
    d = {k: np.concatenate(v) for k, v in cols.items()}

    summary = build_summary(d, info, total, elapsed, read_s)

    # Re-aggregating cached chunks times nothing. Keep the cold-run figure.
    previous = out_dir / "roof_survey.json"
    if computed == 0 and previous.exists():
        summary["timing"] = json.loads(previous.read_text(encoding="utf-8"))["timing"]
    summary["timing"]["buildings_swept_this_run"] = computed

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "roof_survey.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    np.savez_compressed(out_dir / "roof_index.npz", **d)

    for section in (
        "cascade",
        "failure_modes",
        "provenance",
        "area_check_vs_zg3d_sarea_attribute",
        "closure_check_vs_zg3d_volume_attribute",
        "best_plane_distribution_pv_ready",
    ):
        print(json.dumps({section: summary[section]}, indent=2))
    print(f"\nwrote {out_dir / 'roof_survey.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
