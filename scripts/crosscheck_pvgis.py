"""Check the shading against PVGIS's own horizon handling, on 20 roof planes.

PVGIS accepts a user horizon. Each plane's horizon profile from the bundle is
sent with the plane's tilt and aspect, once with the horizon and once without,
and PVGIS's share of beam kept is compared with this project's. Planes are
picked at evenly spaced ranks of beam kept, from the most shaded to the least.

PVGIS takes one horizon value per degree and interpolates linearly between
them, while the bundle stores half-degree bins as steps. So the comparison runs
this project's model on the horizon PVGIS actually receives, and reports
separately how much that resampling moves beam kept against the bundle.

PVGIS uses its own diffuse sky model, which is anisotropic, while this project
uses an isotropic sky, so the plane-of-array totals are expected to differ more
than beam kept does.

    python scripts/crosscheck_pvgis.py

Needs network access to re.jrc.ec.europa.eu. Writes data/survey/pvgis_crosscheck.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import requests

from suncokret.irradiance import shade_plane
from suncokret.pvgis import load_components

API = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
PLANES = 20
# PVGIS spaces horizon values evenly from north; one value per degree.
HORIZON_VALUES = 360


def read_bundle(directory: Path, name: str):
    manifest = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    blob = (directory / manifest["bin"]).read_bytes()

    def buffer(key, dtype):
        spec = manifest["buffers"][key]
        return np.frombuffer(blob, dtype=dtype, count=spec["count"], offset=spec["offset"])

    return manifest, buffer


def pvgis_horizon(shipped: np.ndarray) -> np.ndarray:
    """720 half-degree bins to one whole-degree value per degree of bearing.

    Value j sits at bearing j and takes the higher of the two bins either side.
    Values are rounded to whole degrees because the endpoint refuses requests
    much longer than about 2,000 characters.
    """
    bins = len(shipped)
    per = bins // HORIZON_VALUES
    rolled = np.roll(shipped, per // 2)
    return np.floor(rolled.reshape(HORIZON_VALUES, per).max(axis=1) + 0.5)


def as_bins(values: np.ndarray, bins: int) -> np.ndarray:
    """The per-degree list back on half-degree bins, interpolated linearly as PVGIS does."""
    centres = (np.arange(bins) + 0.5) * (360.0 / bins)
    return np.interp(centres, np.arange(len(values) + 1), np.append(values, values[0]))


def fetch(lat, lon, year, tilt, aspect, horizon=None, timeout=120.0, tries=4):
    params = {
        "lat": lat,
        "lon": lon,
        "startyear": year,
        "endyear": year,
        "outputformat": "json",
        "components": 1,
        "angle": round(tilt, 2),
        "aspect": round(aspect, 2),
        "usehorizon": 1 if horizon else 0,
    }
    if horizon:
        params["userhorizon"] = horizon
    # The public endpoint answers 403 now and then under a run of requests, and
    # a long response is sometimes cut off; both clear on a retry.
    for attempt in range(tries):
        try:
            response = requests.get(API, params=params, timeout=timeout)
            response.raise_for_status()
            hourly = response.json()["outputs"]["hourly"]
            break
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(30.0 * (attempt + 1))
    beam = sum(r["Gb(i)"] for r in hourly)
    total = sum(r["Gb(i)"] + r["Gd(i)"] + r["Gr(i)"] for r in hourly)
    return beam, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="web/public/district")
    ap.add_argument("--district", default="kaptol-donji-grad")
    ap.add_argument("--pvgis", default="data/pvgis/zagreb-2020.json")
    ap.add_argument("--out", default="data/survey/pvgis_crosscheck.json")
    args = ap.parse_args()

    manifest, buffer = read_bundle(Path(args.bundle), args.district)
    components, payload, _ = load_components(Path(args.pvgis))
    lat = payload["inputs"]["location"]["latitude"]
    lon = payload["inputs"]["location"]["longitude"]
    year = int(payload["inputs"]["meteo_data"]["year_min"])

    planes = buffer("selectablePlane", "<i4")
    tilt = buffer("planeTilt", "<f4")[planes]
    azimuth = buffer("planeAzimuth", "<f4")[planes]
    beam_kept = buffer("selectableBeamKept", "<f4")
    bins = manifest["horizon"]["bins"]
    horizons = buffer("horizons", "uint8").reshape(-1, bins).astype(np.float64) / 2.0

    order = np.argsort(beam_kept, kind="stable")
    picks = order[np.round(np.linspace(0, len(order) - 1, PLANES)).astype(int)]

    rows = []
    for slot in picks:
        t, a = float(tilt[slot]), float(azimuth[slot])
        # PVGIS aspect: 0 south, -90 east, 90 west.
        aspect = (a % 360.0) - 180.0
        sent = pvgis_horizon(horizons[slot])
        shaded_beam, shaded_total = fetch(
            lat, lon, year, t, aspect, ",".join(f"{v:.0f}" for v in sent)
        )
        time.sleep(3.0)
        open_beam, open_total = fetch(lat, lon, year, t, aspect)
        time.sleep(3.0)

        ours = shade_plane(components, t, a, [as_bins(sent, bins)])
        rows.append(
            {
                "slot": int(slot),
                "tilt_deg": round(t, 2),
                "pvgis_aspect_deg": round(aspect, 2),
                "beam_kept_bundle": round(float(beam_kept[slot]), 4),
                "beam_kept_here": round(ours.beam_kept(), 4),
                "beam_kept_pvgis": round(shaded_beam / open_beam, 4),
                "poa_open_kwh_m2_here": round(float(ours.total_open.sum()) / 1000, 1),
                "poa_open_kwh_m2_pvgis": round(open_total / 1000, 1),
                "poa_shaded_kwh_m2_here": round(float(ours.total.sum()) / 1000, 1),
                "poa_shaded_kwh_m2_pvgis": round(shaded_total / 1000, 1),
            }
        )
        print(
            f"  slot {slot:4d} tilt {t:5.1f} beam kept {rows[-1]['beam_kept_here']:.3f} "
            f"here, {rows[-1]['beam_kept_pvgis']:.3f} PVGIS",
            flush=True,
        )

    d_beam = np.array([r["beam_kept_here"] - r["beam_kept_pvgis"] for r in rows])
    d_resample = np.array([r["beam_kept_here"] - r["beam_kept_bundle"] for r in rows])
    rel_open = np.array([r["poa_open_kwh_m2_here"] / r["poa_open_kwh_m2_pvgis"] - 1 for r in rows])
    rel_shaded = np.array(
        [r["poa_shaded_kwh_m2_here"] / r["poa_shaded_kwh_m2_pvgis"] - 1 for r in rows]
    )
    summary = {
        "generated_by": "scripts/crosscheck_pvgis.py",
        "generated_on": time.strftime("%Y-%m-%d"),
        "endpoint": API,
        "planes": len(rows),
        "selection": "evenly spaced ranks of beam kept, most shaded to least",
        "horizon_sent": (
            f"{HORIZON_VALUES} values in whole degrees, the higher of each pair of half-degree bins"
        ),
        "note": (
            "the series this project uses was fetched with PVGIS's terrain "
            "horizon applied; the open-sky PVGIS runs here use no horizon at all"
        ),
        "beam_kept_rms_difference": round(float(np.sqrt((d_beam**2).mean())), 4),
        "beam_kept_max_abs_difference": round(float(np.abs(d_beam).max()), 4),
        "resampling_effect_note": (
            "this project's beam kept on the per-degree horizon minus the "
            "bundle's value on half-degree bins"
        ),
        "resampling_effect_rms": round(float(np.sqrt((d_resample**2).mean())), 4),
        "resampling_effect_max_abs": round(float(np.abs(d_resample).max()), 4),
        "poa_open_mean_relative_difference": round(float(rel_open.mean()), 4),
        "poa_shaded_mean_relative_difference": round(float(rel_shaded.mean()), 4),
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
