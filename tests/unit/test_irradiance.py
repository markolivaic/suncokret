"""Irradiance transposition, and the failure that nearly shipped.

A horizontal roof has no facing direction, so roofs.py records its azimuth as
NaN rather than inventing one. Everything downstream has to cope with that, and
the first version of this module did not: the NaN propagated into the totals and
a guard written as ``x / y if y > 0 else 1.0`` turned it into a perfect score,
because NaN > 0 is false. Forty two per cent of the district's planes were
reported as losing nothing and ranked above every roof that really was open.

The tests below are mostly about that.
"""

from __future__ import annotations

import numpy as np
import pytest

from suncokret.irradiance import (
    Components,
    annual_kwh_per_m2,
    ground_view,
    isotropic_sky_view,
    plane_irradiance,
)


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


def test_a_flat_plane_with_no_azimuth_does_not_score_perfectly_when_shaded():
    """The bug. A horizontal plane's azimuth is NaN and used to poison the sum."""
    components = clear_day()
    lit = np.ones(len(components), dtype=bool)
    lit[6:12] = False  # a neighbour takes every morning

    result = plane_irradiance(
        components, tilt_deg=0.0, azimuth_deg=float("nan"), beam_lit=lit, sky_kept=0.6
    )

    assert np.isfinite(result.kept())
    assert result.kept() < 0.95, "a shaded flat roof cannot keep almost everything"
    assert result.beam_kept() < 1.0


def test_a_flat_plane_gives_the_same_answer_whatever_azimuth_is_passed():
    """Which is why substituting a value for NaN is safe rather than a fudge."""
    components = clear_day()
    reference = plane_irradiance(components, 0.0, float("nan")).total.sum()
    for azimuth in (0.0, 90.0, 180.0, 270.0):
        assert plane_irradiance(components, 0.0, azimuth).total.sum() == pytest.approx(reference)


def test_a_nan_in_the_input_raises_instead_of_scoring_perfectly():
    """The guard that hid the bug now refuses to guess."""
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
