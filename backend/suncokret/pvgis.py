"""Read the committed PVGIS hourly series into irradiance components."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .irradiance import Components
from .sun import solar_position

PVGIS_TIME_FORMAT = "%Y%m%d:%H%M"


def load_components(path: Path) -> tuple[Components, dict, int]:
    """Components, the raw payload and the step in seconds.

    PVGIS stamps each hour at :10 UTC and the irradiance is the value at that
    instant, so the sun position is computed for the same instant. The series
    has to be evenly spaced, because the browser rebuilds a roof's year from it
    by index.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    hourly = payload["outputs"]["hourly"]
    stamps = np.array(
        [
            datetime.strptime(r["time"], PVGIS_TIME_FORMAT).replace(tzinfo=UTC).timestamp()
            for r in hourly
        ]
    )
    gaps = np.unique(np.round(np.diff(stamps)).astype(int))
    if len(gaps) != 1:
        raise ValueError(f"PVGIS series is not evenly spaced: steps {gaps}")

    lat = payload["inputs"]["location"]["latitude"]
    lon = payload["inputs"]["location"]["longitude"]
    elevation, azimuth = solar_position(stamps, lat, lon)
    components = Components(
        timestamps=stamps,
        beam_horizontal=np.array([r["Gb(i)"] for r in hourly], dtype=float),
        diffuse_horizontal=np.array([r["Gd(i)"] for r in hourly], dtype=float),
        sun_elevation=elevation,
        sun_azimuth=azimuth,
    )
    return components, payload, int(gaps[0])
