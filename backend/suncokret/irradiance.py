"""Irradiance on a roof plane, from PVGIS components and a horizon profile.

PVGIS is asked once, for a horizontal plane at the city centre, with the beam
and diffuse components reported separately. Everything after that is done here:
the beam is turned back into a direct normal value, both components are
transposed onto each roof plane's own tilt and azimuth, and the shading from
neighbouring buildings is applied to each of them differently.

A neighbouring block either blocks the sun or it does not, so beam is switched
off hour by hour. Diffuse arrives from the whole sky, so it is reduced by the
share of sky the block covers, in every hour including overcast ones.

The output is irradiance on the roof and the share of it the roof keeps against
the same roof with the city removed. Nothing here converts it into electricity.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Fraction of global irradiance the ground throws back up. 0.2 is the usual
# figure for a mixed urban surface and PVGIS assumes the same when it is asked
# for a tilted plane. It is an assumption, not a measurement for Zagreb.
GROUND_ALBEDO = 0.2

# Below this solar elevation the direct normal value is the ratio of two small
# numbers and the reconstruction is noise. Those hours carry almost no energy.
MIN_ELEVATION_FOR_DNI_DEG = 2.0


@dataclass(frozen=True)
class Components:
    """Hourly irradiance components for one place, on a horizontal plane.

    ``beam_horizontal`` and ``diffuse_horizontal`` are PVGIS Gb(i) and Gd(i) in
    W/m2 with the plane left flat. ``sun_elevation`` and ``sun_azimuth`` are this
    project's own, not PVGIS's, so that the geometry and the irradiance agree
    about where the sun is.
    """

    timestamps: np.ndarray
    beam_horizontal: np.ndarray
    diffuse_horizontal: np.ndarray
    sun_elevation: np.ndarray
    sun_azimuth: np.ndarray

    def __len__(self) -> int:
        return len(self.timestamps)

    @property
    def global_horizontal(self) -> np.ndarray:
        return self.beam_horizontal + self.diffuse_horizontal

    @property
    def direct_normal(self) -> np.ndarray:
        """Beam measured across the beam rather than across the ground."""
        sin_el = np.sin(np.radians(self.sun_elevation))
        usable = self.sun_elevation >= MIN_ELEVATION_FOR_DNI_DEG
        out = np.zeros_like(self.beam_horizontal)
        np.divide(self.beam_horizontal, sin_el, out=out, where=usable)
        return out


@dataclass(frozen=True)
class PlaneIrradiance:
    """Hourly irradiance on one roof plane, with and without the neighbours."""

    beam: np.ndarray
    diffuse: np.ndarray
    reflected: np.ndarray
    beam_open: np.ndarray
    diffuse_open: np.ndarray

    @property
    def total(self) -> np.ndarray:
        return self.beam + self.diffuse + self.reflected

    @property
    def total_open(self) -> np.ndarray:
        return self.beam_open + self.diffuse_open + self.reflected

    def kept(self) -> float:
        """Fraction of plane-of-array irradiance left after the neighbours."""
        return _ratio(self.total.sum(), self.total_open.sum())

    def beam_kept(self) -> float:
        return _ratio(self.beam.sum(), self.beam_open.sum())

    def sky_kept(self) -> float:
        return _ratio(self.diffuse.sum(), self.diffuse_open.sum())


def _ratio(got: float, open_: float) -> float:
    """``got / open_``, 1.0 when nothing could arrive, and an error on NaN.

    ``x / y if y > 0 else 1.0`` alone would read a NaN denominator as nothing
    lost, because NaN > 0 is false.
    """
    if not np.isfinite(got) or not np.isfinite(open_):
        raise ValueError("irradiance sums are not finite")
    return float(got / open_) if open_ > 0 else 1.0


def isotropic_sky_view(tilt_deg: float) -> float:
    """Unobstructed view factor from a tilted plane to an isotropic sky."""
    return (1.0 + np.cos(np.radians(tilt_deg))) / 2.0


def ground_view(tilt_deg: float) -> float:
    """Unobstructed view factor from a tilted plane to the ground."""
    return (1.0 - np.cos(np.radians(tilt_deg))) / 2.0


def plane_irradiance(
    components: Components,
    tilt_deg: float,
    azimuth_deg: float,
    beam_lit: np.ndarray | None = None,
    sky_kept: float = 1.0,
) -> PlaneIrradiance:
    """Transpose the horizontal components onto one roof plane.

    ``beam_lit`` is a per-hour boolean from the horizon profile: True when the
    sun actually reaches the plane. ``sky_kept`` is the fraction of the plane's
    sky view left by the neighbours, which does not change through the year.

    The reflected term is not shade-corrected.
    """
    from .sun import incidence_cosine

    if not (np.isfinite(tilt_deg) and np.isfinite(azimuth_deg)):
        raise ValueError(f"plane orientation must be finite, got {tilt_deg}, {azimuth_deg}")

    cos_inc = np.clip(
        incidence_cosine(components.sun_elevation, components.sun_azimuth, tilt_deg, azimuth_deg),
        0.0,
        None,
    )
    # Above the horizon and not behind the plane.
    up = components.sun_elevation > 0

    beam_open = components.direct_normal * cos_inc * up
    lit = np.ones(len(components), dtype=bool) if beam_lit is None else beam_lit
    beam = beam_open * lit

    diffuse_open = components.diffuse_horizontal * isotropic_sky_view(tilt_deg)
    diffuse = diffuse_open * sky_kept

    reflected = components.global_horizontal * GROUND_ALBEDO * ground_view(tilt_deg)

    return PlaneIrradiance(
        beam=beam,
        diffuse=diffuse,
        reflected=reflected,
        beam_open=beam_open,
        diffuse_open=diffuse_open,
    )


def shade_plane(
    components: Components,
    tilt_deg: float,
    azimuth_deg: float,
    horizons: list[np.ndarray],
    sky=None,
) -> PlaneIrradiance:
    """Irradiance on one plane, shaded by the horizon seen from each sample point.

    ``horizons`` holds one profile per point sampled on the plane. Beam is the
    fraction of those points that see the sun, hour by hour; diffuse is scaled
    by their mean sky view ratio. Every script that scores a roof goes through
    here, so there is one definition of "kept".
    """
    from .shading import beam_visible, sky_view_factor

    if not horizons:
        raise ValueError("at least one horizon profile is needed")
    lit = np.zeros(len(components))
    sky_ratio = 0.0
    for horizon in horizons:
        lit += beam_visible(horizon, components.sun_elevation, components.sun_azimuth)
        obstructed, open_total = sky_view_factor(horizon, tilt_deg, azimuth_deg, sky)
        sky_ratio += _ratio(obstructed, open_total)
    lit /= len(horizons)
    sky_ratio /= len(horizons)
    return plane_irradiance(components, tilt_deg, azimuth_deg, beam_lit=lit, sky_kept=sky_ratio)


def annual_kwh_per_m2(hourly_w_per_m2: np.ndarray) -> float:
    """Sum hourly W/m2 into kWh/m2 for the year: irradiance, not electricity."""
    return float(hourly_w_per_m2.sum() / 1000.0)
