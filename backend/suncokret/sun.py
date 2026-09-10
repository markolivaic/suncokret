"""Solar position, vectorised over time.

The NOAA solar position equations, which are the abridged form of Meeus and are
good to well under a tenth of a degree over the years this project covers. That
is far finer than the roof geometry it gets multiplied against, where a LoD 2.2
plane is uncertain by degrees.

Accuracy is not asserted, it is checked: scripts/validate_sun.py compares every
hour of a PVGIS year against the sun elevation PVGIS itself reports, and the
agreement is written to data/survey/sun_validation.json.

No model runs here either. These are closed-form astronomical series.
"""

from __future__ import annotations

import numpy as np

# Unix epoch as a Julian day number.
JULIAN_UNIX_EPOCH = 2440587.5
SECONDS_PER_DAY = 86400.0


def julian_century(unix_seconds: np.ndarray) -> np.ndarray:
    """Julian centuries since J2000.0 from Unix timestamps in UTC."""
    jd = JULIAN_UNIX_EPOCH + np.asarray(unix_seconds, dtype=np.float64) / SECONDS_PER_DAY
    return (jd - 2451545.0) / 36525.0


def solar_position(
    unix_seconds: np.ndarray, latitude: float, longitude: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return (elevation, azimuth) in degrees for each timestamp.

    Elevation is measured from the horizon and is negative at night. Azimuth is
    a compass bearing: 0 north, 90 east, 180 south, 270 west, matching the
    convention roof azimuths use in this project.

    Elevation is the true geometric one. No refraction correction is applied:
    it only matters within about half a degree of the horizon, where a roof in
    Zagreb is already shaded by the city and the direct beam is negligible.
    """
    t = julian_century(unix_seconds)

    # Geometric mean longitude and anomaly of the sun.
    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    m_rad = np.radians(mean_anom)

    # Equation of centre, then the true and apparent longitudes.
    centre = (
        np.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + np.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + np.sin(3 * m_rad) * 0.000289
    )
    true_long = mean_long + centre
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * np.sin(np.radians(omega))

    # Obliquity of the ecliptic, with the nutation correction.
    mean_obliq = (
        23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    )
    obliq = mean_obliq + 0.00256 * np.cos(np.radians(omega))
    obliq_rad = np.radians(obliq)
    app_rad = np.radians(app_long)

    declination = np.arcsin(np.sin(obliq_rad) * np.sin(app_rad))

    # Equation of time, in minutes.
    y = np.tan(obliq_rad / 2.0) ** 2
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    l_rad = np.radians(mean_long)
    eq_time = 4.0 * np.degrees(
        y * np.sin(2 * l_rad)
        - 2 * eccentricity * np.sin(m_rad)
        + 4 * eccentricity * y * np.sin(m_rad) * np.cos(2 * l_rad)
        - 0.5 * y * y * np.sin(4 * l_rad)
        - 1.25 * eccentricity * eccentricity * np.sin(2 * m_rad)
    )

    # True solar time, then the hour angle west of the meridian.
    minutes_utc = (np.asarray(unix_seconds, dtype=np.float64) / 60.0) % 1440.0
    true_solar_minutes = (minutes_utc + eq_time + 4.0 * longitude) % 1440.0
    hour_angle = np.radians(true_solar_minutes / 4.0 - 180.0)

    lat_rad = np.radians(latitude)
    sin_elev = np.sin(lat_rad) * np.sin(declination) + np.cos(lat_rad) * np.cos(
        declination
    ) * np.cos(hour_angle)
    elevation = np.degrees(np.arcsin(np.clip(sin_elev, -1.0, 1.0)))

    # Azimuth measured from north, increasing eastward.
    azimuth = np.degrees(
        np.arctan2(
            np.sin(hour_angle),
            np.cos(hour_angle) * np.sin(lat_rad) - np.tan(declination) * np.cos(lat_rad),
        )
    )
    azimuth = (azimuth + 180.0) % 360.0

    return elevation, azimuth


def sun_vectors(elevation: np.ndarray, azimuth: np.ndarray) -> np.ndarray:
    """Unit vectors pointing at the sun, in the local east-north-up frame.

    The projected CRS this project works in has x east and y north, so these
    drop straight into the geometry without another rotation.
    """
    el = np.radians(elevation)
    az = np.radians(azimuth)
    cos_el = np.cos(el)
    return np.column_stack([cos_el * np.sin(az), cos_el * np.cos(az), np.sin(el)])


def incidence_cosine(
    sun_elevation: np.ndarray,
    sun_azimuth: np.ndarray,
    plane_tilt: float,
    plane_azimuth: float,
) -> np.ndarray:
    """Cosine of the angle between the beam and a plane's normal.

    Negative values mean the sun is behind the plane. Callers clamp at zero;
    it is returned signed so that a caller can tell "behind the roof" from
    "blocked by a neighbour", which are different physical situations.
    """
    el = np.radians(sun_elevation)
    az = np.radians(sun_azimuth)
    tilt = np.radians(plane_tilt)
    paz = np.radians(plane_azimuth)
    return np.sin(el) * np.cos(tilt) + np.cos(el) * np.sin(tilt) * np.cos(az - paz)
