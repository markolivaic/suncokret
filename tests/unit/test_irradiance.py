"""Irradiance transposition and shading of one plane."""

from __future__ import annotations

import numpy as np
import pytest

from suncokret.irradiance import (
    Components,
    annual_kwh_per_m2,
    ground_view,
    isotropic_sky_view,
    plane_irradiance,
    shade_plane,
)
from suncokret.shading import DEFAULT_AZIMUTH_BINS


def clear_day(hours: int = 24, peak: float = 800.0) -> Components:
    """A single synthetic day with the sun tracking a plausible arc."""
    hour = np.arange(hours)
    elevation = np.where(
        (hour >= 6) & (hour <= 18),
        50.0 * np.sin(np.pi * (hour - 6) / 12),
        -10.0,
    )
    azimuth = 90.0 + (hour - 6) * 15.0
    beam = np.where(elevation > 0, peak * np.sin(np.radians(np.clip(elevation, 0, 90))), 0.0)
    diffuse = np.where(elevation > 0, 120.0, 0.0)
    return Components(
        timestamps=hour.astype(float) * 3600,
        beam_horizontal=beam,
        diffuse_horizontal=diffuse,
        sun_elevation=elevation,
        sun_azimuth=azimuth,
    )


def test_view_factors_are_the_textbook_ones():
    assert isotropic_sky_view(0.0) == pytest.approx(1.0)
    assert isotropic_sky_view(90.0) == pytest.approx(0.5)
    assert ground_view(0.0) == pytest.approx(0.0)
    assert ground_view(90.0) == pytest.approx(0.5)
    for tilt in (0.0, 17.0, 35.0, 90.0):
        assert isotropic_sky_view(tilt) + ground_view(tilt) == pytest.approx(1.0)


def test_an_unshaded_plane_keeps_everything():
    result = plane_irradiance(clear_day(), 30.0, 180.0)
    assert result.kept() == pytest.approx(1.0)
    assert result.beam_kept() == pytest.approx(1.0)


def test_a_shaded_flat_plane_does_not_score_perfectly():
    components = clear_day()
    lit = np.ones(len(components), dtype=bool)
    lit[6:12] = False  # a neighbour takes every morning

    result = plane_irradiance(components, 0.0, 180.0, beam_lit=lit, sky_kept=0.6)

    assert result.kept() < 0.95
    assert result.beam_kept() < 1.0


@pytest.mark.parametrize("tilt, azimuth", [(float("nan"), 180.0), (3.0, float("nan"))])
def test_a_non_finite_orientation_raises(tilt, azimuth):
    with pytest.raises(ValueError, match="finite"):
        plane_irradiance(clear_day(), tilt, azimuth)


def test_a_horizontal_plane_does_not_depend_on_azimuth():
    components = clear_day()
    reference = plane_irradiance(components, 0.0, 0.0).total.sum()
    for azimuth in (90.0, 180.0, 270.0):
        assert plane_irradiance(components, 0.0, azimuth).total.sum() == pytest.approx(reference)


def test_a_near_flat_plane_still_depends_on_its_azimuth():
    """At 5 degrees the azimuth term is weighted by sin(5) = 0.087, not zero."""
    components = clear_day()
    south = plane_irradiance(components, 5.0, 180.0).beam_open.sum()
    north = plane_irradiance(components, 5.0, 0.0).beam_open.sum()
    assert south > north * 1.05


OPEN = np.full(DEFAULT_AZIMUTH_BINS, -90.0)
WALLED = np.full(DEFAULT_AZIMUTH_BINS, 90.0)


@pytest.mark.parametrize("tilt", [0.0, 3.0, 30.0, 60.0])
@pytest.mark.parametrize("azimuth", [0.0, 90.0, 180.0, 270.0])
def test_open_sky_keeps_all_the_beam_at_any_orientation(tilt, azimuth):
    result = shade_plane(clear_day(), tilt, azimuth, [OPEN])
    assert result.beam_kept() == pytest.approx(1.0)
    assert result.sky_kept() == pytest.approx(1.0)


@pytest.mark.parametrize("tilt, azimuth", [(0.0, 0.0), (30.0, 180.0), (45.0, 90.0)])
def test_a_walled_in_plane_keeps_no_beam(tilt, azimuth):
    result = shade_plane(clear_day(), tilt, azimuth, [WALLED])
    assert result.beam_kept() == pytest.approx(0.0)
    assert result.sky_kept() == pytest.approx(0.0)


def test_sample_points_are_averaged():
    """Half the points walled in, half open: half the beam."""
    result = shade_plane(clear_day(), 30.0, 180.0, [OPEN, WALLED])
    assert result.beam_kept() == pytest.approx(0.5)


def test_a_nan_in_the_input_raises_instead_of_scoring_perfectly():
    """A NaN in the inputs raises instead of becoming a perfect score."""
    components = clear_day()
    poisoned = Components(
        timestamps=components.timestamps,
        beam_horizontal=components.beam_horizontal.copy(),
        diffuse_horizontal=components.diffuse_horizontal.copy(),
        sun_elevation=components.sun_elevation,
        sun_azimuth=components.sun_azimuth,
    )
    poisoned.beam_horizontal[8] = np.nan

    result = plane_irradiance(poisoned, 30.0, 180.0)
    with pytest.raises(ValueError, match="not finite"):
        result.kept()


def test_blocking_the_beam_costs_beam_but_not_diffuse():
    components = clear_day()
    lit = np.ones(len(components), dtype=bool)
    lit[6:12] = False

    result = plane_irradiance(components, 30.0, 180.0, beam_lit=lit)
    assert result.beam_kept() < 1.0
    assert result.diffuse.sum() == pytest.approx(result.diffuse_open.sum())


def test_covering_the_sky_costs_diffuse_but_not_beam():
    components = clear_day()
    result = plane_irradiance(components, 30.0, 180.0, sky_kept=0.5)

    assert result.beam_kept() == pytest.approx(1.0)
    assert result.diffuse.sum() == pytest.approx(0.5 * result.diffuse_open.sum())
    assert result.kept() < 1.0


def test_a_south_plane_beats_a_north_one_in_this_hemisphere():
    components = clear_day()
    south = plane_irradiance(components, 35.0, 180.0).total.sum()
    north = plane_irradiance(components, 35.0, 0.0).total.sum()
    assert south > north


def test_direct_normal_is_not_reconstructed_from_a_sun_on_the_horizon():
    """Dividing by sin(elevation) near zero is the ratio of two small numbers."""
    components = clear_day()
    dni = components.direct_normal
    assert np.isfinite(dni).all()
    assert (dni[components.sun_elevation < 2.0] == 0).all()


def test_annual_sum_is_kwh_per_square_metre():
    assert annual_kwh_per_m2(np.full(1000, 1000.0)) == pytest.approx(1000.0)
