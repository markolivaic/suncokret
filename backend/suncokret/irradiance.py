"""Irradiance on a roof plane, from PVGIS components and a horizon profile.

PVGIS is asked once, for a horizontal plane at the city centre, with the beam
and diffuse components reported separately. Everything after that is done here:
the beam is turned back into a direct normal value, both components are
transposed onto each roof plane's own tilt and azimuth, and the shading from
neighbouring buildings is applied to each of them differently.

Beam and diffuse have to be shaded differently and this is the part cheap
calculators skip. A neighbouring block either blocks the sun or it does not, so
beam is switched off hour by hour. Diffuse arrives from the whole sky, so it is
reduced by however much of the sky that block covers, and that reduction applies
in every hour including overcast ones. Treating diffuse as unobstructed flatters
a shaded roof badly in a Zagreb winter, when most of the light is diffuse.

What this deliberately does not produce is an annual figure in kilowatt hours of
electricity. There is no ground truth for any roof in Zagreb, so a generation
number would be a guess wearing a unit. What it produces is the fraction of the
irradiance a roof keeps against the same roof with the city removed, which is a
comparison between two runs of the same pipeline and is defensible.

No model is trained and none runs.
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


def _ratio(got: float, open_: float) -> float:
    """Fraction kept, refusing to turn a NaN into a perfect score.

    The obvious spelling of this, ``got / open if open > 0 else 1.0``, treats a
    NaN denominator as "nothing was lost", because NaN > 0 is false. That is the
    worst possible failure mode for this project: it silently reported every
    horizontal roof as unshaded, including ones that had lost a third of their
    sky, and put them at the top of the ranking. It raises now.
    """
    if not np.isfinite(got) or not np.isfinite(open_):
        raise ValueError(
            "irradiance sums are not finite; a NaN reached the total. "
            "The usual cause is a plane azimuth of NaN, which roofs.py uses to "
            "mean a horizontal plane has no facing direction."
        )
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

    The reflected term is not shade-corrected. It is a few per cent of the total
    and correcting it would need a model of what the neighbours are made of,
    which this project does not have.
    """
    from .sun import incidence_cosine

    # roofs.py records a horizontal plane's azimuth as NaN, because a plane
    # facing straight up does not face anywhere and reporting a direction would
    # be inventing a measurement. The transposition still needs a number: the
    # azimuth term is multiplied by sin(tilt), which is zero for such a plane,
    # so any finite value gives the same answer and NaN gives none.
    azimuth = azimuth_deg if np.isfinite(azimuth_deg) else 0.0

    cos_inc = np.clip(
        incidence_cosine(components.sun_elevation, components.sun_azimuth, tilt_deg, azimuth),
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


def annual_kwh_per_m2(hourly_w_per_m2: np.ndarray) -> float:
    """Sum hourly W/m2 into kWh/m2 for the year.

    This is irradiance arriving on a surface. It is not electricity, and this
    project never converts it into electricity, because doing so would need a
    module efficiency, a system loss factor and an inverter model, none of which
    could be checked against anything real in Zagreb.
    """
    return float(hourly_w_per_m2.sum() / 1000.0)
