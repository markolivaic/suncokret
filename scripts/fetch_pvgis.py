"""Fetch one year of hourly irradiance for Zagreb from PVGIS and commit it.

PVGIS (JRC) is free and needs no registration. One call is enough for the whole
city: the request asks for a horizontal plane, which returns the beam and
diffuse components separately, and every roof plane is then transposed from
those components by this project's own code. Calling PVGIS once per roof plane
would be tens of thousands of requests against a public service for information
that is already in this one response.

    python scripts/fetch_pvgis.py

Writes data/pvgis/zagreb-<year>.json plus a MANIFEST.json recording what was
asked for and when, so the number in the README is traceable to a fetch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import requests

API = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"

# Zagreb city centre, near Trg bana Jelacica. PVGIS resolves its own elevation
# from a DEM; the roof heights come from ZG3D, not from here.
ZAGREB_LAT = 45.8150
ZAGREB_LON = 15.9819

# SARAH3 covers 2005-2023. 2020 is inside it and is a full leap year, so the
# series is 8784 hours and no month is short.
DEFAULT_YEAR = 2020


def fetch(lat: float, lon: float, year: int, timeout: float = 120.0) -> dict:
    params = {
        "lat": lat,
        "lon": lon,
        "startyear": year,
        "endyear": year,
        "outputformat": "json",
        # Beam, diffuse and reflected reported separately rather than as one
        # plane-of-array total. The shading model blocks beam and diffuse
        # differently, so they cannot arrive pre-summed.
        "components": 1,
        # Horizontal plane. Every tilt and azimuth in this project is derived
        # from these components, not requested from PVGIS.
        "angle": 0,
        "aspect": 0,
    }
    response = requests.get(API, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=DEFAULT_YEAR)
    ap.add_argument("--lat", type=float, default=ZAGREB_LAT)
    ap.add_argument("--lon", type=float, default=ZAGREB_LON)
    ap.add_argument("--out", default="data/pvgis")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"PVGIS seriescalc lat={args.lat} lon={args.lon} year={args.year}")
    t0 = time.perf_counter()
    payload = fetch(args.lat, args.lon, args.year)
    elapsed = time.perf_counter() - t0

    hourly = payload["outputs"]["hourly"]
    body = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    path = out_dir / f"zagreb-{args.year}.json"
    path.write_text(body, encoding="utf-8")

    manifest = {
        "fetched_on": time.strftime("%Y-%m-%d"),
        "endpoint": API,
        "request": {
            "lat": args.lat,
            "lon": args.lon,
            "year": args.year,
            "angle": 0,
            "aspect": 0,
            "components": 1,
        },
        "response": {
            "hours": len(hourly),
            "radiation_db": payload["inputs"]["meteo_data"]["radiation_db"],
            "meteo_db": payload["inputs"]["meteo_data"]["meteo_db"],
            "elevation_m": payload["inputs"]["location"]["elevation"],
            "use_horizon": payload["inputs"]["meteo_data"]["use_horizon"],
            "horizon_data": payload["inputs"]["meteo_data"]["horizon_data"],
        },
        "note": (
            "use_horizon is PVGIS applying a DEM far horizon, which is terrain "
            "only. Buildings are not in it. Inter-building shading in this "
            "project is computed from ZG3D and is a separate thing."
        ),
        "file": path.name,
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "fetch_seconds": round(elapsed, 2),
    }
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"  {len(hourly)} hours, {payload['inputs']['meteo_data']['radiation_db']}")
    print(f"  wrote {path} ({len(body) / 1e6:.1f} MB) in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
