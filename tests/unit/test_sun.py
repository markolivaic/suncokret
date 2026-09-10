"""Solar position against astronomy that can be worked out on paper.

At solar noon the sun's elevation is 90 - |latitude - declination|, and the
declination at the solstices and equinoxes is known. That gives real answers to
check against without trusting the implementation to check itself.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from suncokret.sun import incidence_cosine, solar_position, sun_vectors

ZAGREB_LAT = 45.8150
ZAGREB_LON = 15.9819
OBLIQUITY = 23.44

SERIES = Path(__file__).resolve().parents[2] / "data" / "pvgis" / "zagreb-2020.json"


def utc(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def day_of(year, month, day, step_minutes=1):
    start = datetime(year, month, day, tzinfo=UTC)
    n = 24 * 60 // step_minutes
    return np.array([(start + timedelta(minutes=i * step_minutes)).timestamp() for i in range(n)])


def peak_elevation(year, month, day):
    stamps = day_of(year, month, day)
    elevation, azimuth = solar_position(stamps, ZAGREB_LAT, ZAGREB_LON)
    i = int(np.argmax(elevation))
    return elevation[i], azimuth[i]


def test_summer_solstice_noon_elevation():
    elevation, azimuth = peak_elevation(2020, 6, 20)
    assert elevation == pytest.approx(90.0 - ZAGREB_LAT + OBLIQUITY, abs=0.2)
    assert azimuth == pytest.approx(180.0, abs=0.5)


def test_winter_solstice_noon_elevation():
    elevation, azimuth = peak_elevation(2020, 12, 21)
    assert elevation == pytest.approx(90.0 - ZAGREB_LAT - OBLIQUITY, abs=0.2)
    assert azimuth == pytest.approx(180.0, abs=0.5)


def test_equinox_noon_elevation_is_the_colatitude():
    elevation, _ = peak_elevation(2020, 3, 20)
    assert elevation == pytest.approx(90.0 - ZAGREB_LAT, abs=0.4)


def test_equinox_day_is_about_twelve_hours():
    stamps = day_of(2020, 3, 20)
    elevation, _ = solar_position(stamps, ZAGREB_LAT, ZAGREB_LON)
    daylight_minutes = int((elevation > 0).sum())
    assert daylight_minutes == pytest.approx(12 * 60, abs=15)


def test_sun_rises_in_the_east_and_sets_in_the_west():
    stamps = day_of(2020, 6, 20)
    elevation, azimuth = solar_position(stamps, ZAGREB_LAT, ZAGREB_LON)
    up = np.flatnonzero(elevation > 0)
    assert 45.0 < azimuth[up[0]] < 90.0
    assert 270.0 < azimuth[up[-1]] < 315.0


def test_elevation_is_negative_at_local_midnight():
    elevation, _ = solar_position(np.array([utc(2020, 6, 20, 23, 0)]), ZAGREB_LAT, ZAGREB_LON)
    assert elevation[0] < 0


def test_southern_hemisphere_noon_sun_sits_in_the_north():
    stamps = day_of(2020, 12, 21)
    elevation, azimuth = solar_position(stamps, -33.87, 151.21)  # Sydney
    i = int(np.argmax(elevation))
    assert azimuth[i] == pytest.approx(0.0, abs=2.0) or azimuth[i] == pytest.approx(360.0, abs=2.0)


def test_sun_vectors_are_unit_and_point_up_during_the_day():
    stamps = day_of(2020, 6, 20, step_minutes=30)
    elevation, azimuth = solar_position(stamps, ZAGREB_LAT, ZAGREB_LON)
    v = sun_vectors(elevation, azimuth)
    assert np.linalg.norm(v, axis=1) == pytest.approx(np.ones(len(v)))
    assert (v[elevation > 0][:, 2] > 0).all()


def test_incidence_cosine_matches_the_dot_product_of_the_normal():
    stamps = day_of(2020, 4, 15, step_minutes=20)
    elevation, azimuth = solar_position(stamps, ZAGREB_LAT, ZAGREB_LON)
    tilt, plane_azimuth = 35.0, 200.0

    t, p = math.radians(tilt), math.radians(plane_azimuth)
    normal = np.array([math.sin(t) * math.sin(p), math.sin(t) * math.cos(p), math.cos(t)])
    expected = sun_vectors(elevation, azimuth) @ normal

    assert incidence_cosine(elevation, azimuth, tilt, plane_azimuth) == pytest.approx(expected)


def test_a_horizontal_plane_sees_the_sine_of_the_elevation():
    stamps = day_of(2020, 4, 15, step_minutes=20)
    elevation, azimuth = solar_position(stamps, ZAGREB_LAT, ZAGREB_LON)
    assert incidence_cosine(elevation, azimuth, 0.0, 0.0) == pytest.approx(
        np.sin(np.radians(elevation))
    )


@pytest.mark.skipif(not SERIES.exists(), reason="PVGIS series not fetched")
def test_agrees_with_the_pvgis_sun_elevation_for_a_whole_year():
    """The independent check: PVGIS reports the elevation it used, hour by hour."""
    payload = json.loads(SERIES.read_text(encoding="utf-8"))
    hourly = payload["outputs"]["hourly"]
    stamps = np.array(
        [
            datetime.strptime(row["time"], "%Y%m%d:%H%M").replace(tzinfo=UTC).timestamp()
            for row in hourly
        ]
    )
    reference = np.array([row["H_sun"] for row in hourly], dtype=float)
    lat = payload["inputs"]["location"]["latitude"]
    lon = payload["inputs"]["location"]["longitude"]

    mine, _ = solar_position(stamps, lat, lon)
    day = reference > 0
    residual = mine[day] - reference[day]

    assert day.sum() > 4000
    assert float(np.sqrt(np.mean(residual**2))) < 0.5
    assert float(np.max(np.abs(residual))) < 1.0
