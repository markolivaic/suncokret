"""Check this project's solar position against PVGIS, hour by hour.

PVGIS reports the sun elevation it used for every hour of the year. That makes
it an independent check on backend/suncokret/sun.py rather than a source of
irradiance only, and it costs nothing extra because the series is already
fetched.

PVGIS timestamps the hourly averages at ten past the hour and does not document
which instant H_sun refers to, so the comparison is run across a range of
offsets and the best one is reported alongside the raw-timestamp result. That is
a calibration of a documentation gap, not a fit: one scalar, chosen once, and
the residual is what gets quoted.

    python scripts/validate_sun.py

Writes data/survey/sun_validation.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from suncokret.sun import solar_position

# PVGIS hourly stamps look like 20200101:0010.
PVGIS_TIME_FORMAT = "%Y%m%d:%H%M"

# Offsets in minutes to try when locating the instant H_sun describes.
CANDIDATE_OFFSETS = tuple(range(-40, 41, 5))


def load_series(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    hourly = payload["outputs"]["hourly"]
    stamps = np.array(
        [
            datetime.strptime(row["time"], PVGIS_TIME_FORMAT).replace(tzinfo=UTC).timestamp()
            for row in hourly
        ]
    )
    elevation = np.array([row["H_sun"] for row in hourly], dtype=float)
    lat = payload["inputs"]["location"]["latitude"]
    lon = payload["inputs"]["location"]["longitude"]
    return stamps, elevation, lat, lon, payload


def compare(stamps, reference, lat, lon, offset_minutes):
    mine, _ = solar_position(stamps + offset_minutes * 60.0, lat, lon)
    # PVGIS clamps night to zero, so only daylight hours carry information.
    day = reference > 0
    residual = mine[day] - reference[day]
    return {
        "offset_minutes": offset_minutes,
        "hours_compared": int(day.sum()),
        "rms_deg": round(float(np.sqrt(np.mean(residual**2))), 6),
        "max_abs_deg": round(float(np.max(np.abs(residual))), 6),
        "mean_bias_deg": round(float(np.mean(residual)), 6),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default=None)
    ap.add_argument("--out", default="data/survey/sun_validation.json")
    args = ap.parse_args()

    path = Path(args.series) if args.series else None
    if path is None:
        candidates = sorted(Path("data/pvgis").glob("zagreb-*.json"))
        if not candidates:
            raise SystemExit("No PVGIS series found. Run scripts/fetch_pvgis.py first.")
        path = candidates[0]

    stamps, reference, lat, lon, payload = load_series(path)
    print(f"{path.name}: {len(stamps)} hours at {lat}, {lon}")

    trials = [compare(stamps, reference, lat, lon, off) for off in CANDIDATE_OFFSETS]
    best = min(trials, key=lambda t: t["rms_deg"])
    raw = next(t for t in trials if t["offset_minutes"] == 0)

    summary = {
        "generated_by": "scripts/validate_sun.py",
        "generated_on": time.strftime("%Y-%m-%d"),
        "machine": (
            f"{platform.system()} {platform.machine()}, Python {platform.python_version()}"
        ),
        "reference": {
            "source": "PVGIS H_sun, the sun elevation PVGIS itself used",
            "file": path.name,
            "radiation_db": payload["inputs"]["meteo_data"]["radiation_db"],
            "latitude": lat,
            "longitude": lon,
        },
        "note": (
            "daylight hours only, since PVGIS reports zero rather than a "
            "negative elevation at night"
        ),
        "at_pvgis_timestamp": raw,
        "best_offset": best,
        "all_offsets": trials,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"  at the raw timestamp : rms {raw['rms_deg']:.4f} deg, max {raw['max_abs_deg']:.4f}")
    print(
        f"  best offset {best['offset_minutes']:+d} min: rms {best['rms_deg']:.4f} deg, "
        f"max {best['max_abs_deg']:.4f}, bias {best['mean_bias_deg']:+.4f}"
    )
    print(f"  over {best['hours_compared']} daylight hours")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
