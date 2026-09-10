"""Shading against geometry with a closed-form answer.

A horizontal plane ringed by an obstruction at a constant elevation keeps
cos^2 of that elevation of its isotropic diffuse. That integral is worth
checking against, because a sky view factor is easy to get subtly wrong and
impossible to eyeball.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from suncokret.shading import (
    DEFAULT_AZIMUTH_BINS,
    PointGrid,
    SkyGrid,
    beam_visible,
    densify_segments,
    ground_sunlit,
    height_raster,
    horizon_profile,
    reduce_to_height_field,
    sky_view_factor,
)


def wall_ring(radius, height, n=2000, z0=0.0):
    """Points on a circle of given radius at a constant height."""
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([radius * np.cos(a), radius * np.sin(a), np.full(n, z0 + height)])


def test_horizon_of_a_single_point_is_its_elevation_angle():
    origin = np.zeros(3)
    point = np.array([[0.0, 100.0, 100.0]])  # due north, 45 degrees up
    horizon = horizon_profile(origin, point)

    assert horizon.max() == pytest.approx(45.0, abs=1e-6)
    assert horizon[0] == pytest.approx(45.0, abs=1e-6)  # bin 0 is north
    assert horizon[DEFAULT_AZIMUTH_BINS // 2] == -90.0  # nothing to the south


def test_horizon_bins_follow_the_compass():
    origin = np.zeros(3)
    quarter = DEFAULT_AZIMUTH_BINS // 4
    cases = {
        0: [0.0, 10.0, 10.0],  # north
        quarter: [10.0, 0.0, 10.0],  # east
        2 * quarter: [0.0, -10.0, 10.0],  # south
        3 * quarter: [-10.0, 0.0, 10.0],  # west
    }
    for expected_bin, point in cases.items():
        horizon = horizon_profile(origin, np.array([point]))
        assert int(np.argmax(horizon)) == expected_bin


def test_blockers_at_or_below_the_observer_are_discarded():
    origin = np.array([0.0, 0.0, 10.0])
    below = np.array([[0.0, 20.0, 10.0], [0.0, 30.0, 2.0]])
    assert horizon_profile(origin, below).max() == -90.0


def test_blockers_beyond_the_radius_are_ignored():
    origin = np.zeros(3)
    far = np.array([[0.0, 300.0, 300.0]])
    assert horizon_profile(origin, far, radius=250.0).max() == -90.0
    assert horizon_profile(origin, far, radius=400.0).max() == pytest.approx(45.0)


def test_beam_is_blocked_below_the_horizon_and_free_above_it():
    horizon = np.full(DEFAULT_AZIMUTH_BINS, 30.0)
    elevation = np.array([-5.0, 10.0, 29.9, 30.1, 60.0])
    azimuth = np.full(5, 180.0)

    visible = beam_visible(horizon, elevation, azimuth)
    assert visible.tolist() == [False, False, False, True, True]


def test_open_sky_keeps_all_of_the_diffuse():
    horizon = np.full(DEFAULT_AZIMUTH_BINS, -90.0)
    obstructed, open_total = sky_view_factor(horizon, 0.0, 0.0)
    assert obstructed / open_total == pytest.approx(1.0)


@pytest.mark.parametrize("blocked_elevation", [15.0, 30.0, 45.0, 60.0])
def test_uniform_horizon_leaves_cos_squared_of_the_diffuse(blocked_elevation):
    """Closed form for a horizontal plane under an isotropic sky."""
    horizon = np.full(DEFAULT_AZIMUTH_BINS, blocked_elevation)
    obstructed, open_total = sky_view_factor(horizon, 0.0, 0.0, SkyGrid(n_elevation=900))

    expected = math.cos(math.radians(blocked_elevation)) ** 2
    assert obstructed / open_total == pytest.approx(expected, abs=0.01)


def test_a_tilted_plane_under_open_sky_still_keeps_all_of_it():
    """The ratio is against the same plane, so tilt must divide out."""
    horizon = np.full(DEFAULT_AZIMUTH_BINS, -90.0)
    for tilt, azimuth in ((30.0, 180.0), (45.0, 90.0), (60.0, 300.0)):
        obstructed, open_total = sky_view_factor(horizon, tilt, azimuth)
        assert obstructed / open_total == pytest.approx(1.0)


def test_a_wall_to_the_south_costs_a_south_facing_roof_more_than_a_north_one():
    horizon = np.full(DEFAULT_AZIMUTH_BINS, -90.0)
    half = DEFAULT_AZIMUTH_BINS // 4
    horizon[DEFAULT_AZIMUTH_BINS // 2 - half : DEFAULT_AZIMUTH_BINS // 2 + half] = 40.0

    south = np.divide(*sky_view_factor(horizon, 35.0, 180.0))
    north = np.divide(*sky_view_factor(horizon, 35.0, 0.0))
    assert south < north


def test_a_ring_wall_gives_the_same_horizon_all_the_way_round():
    origin = np.zeros(3)
    points = wall_ring(50.0, 50.0)
    horizon = horizon_profile(origin, points)
    filled = horizon[horizon > -90.0]

    assert len(filled) == DEFAULT_AZIMUTH_BINS
    assert filled.min() == pytest.approx(45.0, abs=0.5)
    assert filled.max() == pytest.approx(45.0, abs=0.5)


def test_densify_includes_both_endpoints():
    segments = np.array([[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]])
    points, owner = densify_segments(segments, spacing=2.0)

    assert points[0] == pytest.approx([0.0, 0.0, 0.0])
    assert points[-1] == pytest.approx([10.0, 0.0, 0.0])
    assert (owner == 0).all()
    gaps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    assert gaps.max() <= 2.0 + 1e-9


def test_densify_keeps_a_short_segment_whole():
    segments = np.array([[[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]]])
    points, _ = densify_segments(segments, spacing=2.0)
    assert len(points) == 2


def test_densify_maps_every_point_back_to_its_segment():
    segments = np.array(
        [
            [[0.0, 0.0, 0.0], [9.0, 0.0, 0.0]],
            [[0.0, 5.0, 1.0], [1.0, 5.0, 1.0]],
        ]
    )
    points, owner = densify_segments(segments, spacing=3.0)
    assert set(np.unique(owner).tolist()) == {0, 1}
    assert points[owner == 1][:, 1] == pytest.approx(5.0)


def test_height_field_keeps_the_tallest_point_in_each_cell():
    points = np.array(
        [
            [0.2, 0.2, 3.0],
            [0.8, 0.8, 9.0],  # same 1 m cell, taller
            [5.5, 0.3, 4.0],  # a different cell
        ]
    )
    reduced = reduce_to_height_field(points, cell=1.0)
    assert len(reduced) == 2
    assert sorted(reduced[:, 2].tolist()) == [4.0, 9.0]


def test_height_field_preserves_the_tallest_obstruction():
    rng = np.random.default_rng(0)
    points = np.column_stack(
        [
            rng.uniform(-100, 100, 4000),
            rng.uniform(-100, 100, 4000),
            rng.uniform(5, 40, 4000),
        ]
    )
    reduced = reduce_to_height_field(points, 1.0)
    assert reduced[:, 2].max() == pytest.approx(points[:, 2].max())
    assert len(reduced) < len(points)


def test_height_field_barely_moves_the_diffuse_a_roof_keeps():
    """The quantity that is actually used, rather than a per-bin comparison.

    Snapping a blocker to a cell centre changes its bearing, so bin-by-bin
    agreement is not the right claim to make about a raster. What has to hold is
    that the number the model consumes does not move.
    """
    rng = np.random.default_rng(0)
    points = np.column_stack(
        [
            rng.uniform(-150, 150, 20000),
            rng.uniform(-150, 150, 20000),
            rng.uniform(5, 35, 20000),
        ]
    )
    origin = np.zeros(3)
    full = horizon_profile(origin, points)
    reduced = horizon_profile(origin, reduce_to_height_field(points, 1.0))

    ratio_full = np.divide(*sky_view_factor(full, 30.0, 180.0))
    ratio_reduced = np.divide(*sky_view_factor(reduced, 30.0, 180.0))
    assert ratio_reduced == pytest.approx(ratio_full, abs=0.02)


def test_a_blocker_closer_than_the_raster_cell_is_not_trusted():
    """Only a roof's own building sits this close, and its bearing is noise."""
    origin = np.zeros(3)
    touching = np.array([[0.4, 0.0, 12.0]])
    assert horizon_profile(origin, touching).max() == -90.0

    a_little_further = np.array([[4.0, 0.0, 12.0]])
    assert horizon_profile(origin, a_little_further).max() > 60.0


def test_point_grid_returns_every_candidate_the_brute_force_scan_would():
    rng = np.random.default_rng(1)
    points = np.column_stack(
        [rng.uniform(0, 900, 5000), rng.uniform(0, 900, 5000), rng.uniform(0, 50, 5000)]
    )
    grid = PointGrid(points, cell=40.0)
    origin = np.array([450.0, 450.0, 10.0])
    radius = 250.0

    got = grid.near(origin, radius)
    ground = np.hypot(points[:, 0] - origin[0], points[:, 1] - origin[1])
    expected = points[(ground <= radius) & (points[:, 2] > origin[2])]

    # The grid may return extra points from cells that straddle the radius, but
    # it must never miss one that matters.
    assert len(got) >= len(expected)
    assert horizon_profile(origin, got, radius=radius).max() == pytest.approx(
        horizon_profile(origin, points, radius=radius).max()
    )


def test_point_grid_survives_an_empty_cloud():
    grid = PointGrid(np.empty((0, 3)))
    assert len(grid.near(np.zeros(3), 250.0)) == 0


# ---- the ground the shadows fall on -----------------------------------------


def square_roof(x0, y0, side, height):
    """Two triangles covering a square at a constant height."""
    a = (x0, y0, height)
    b = (x0 + side, y0, height)
    c = (x0 + side, y0 + side, height)
    d = (x0, y0 + side, height)
    return np.array([a, b, c, a, c, d], dtype=float)


def test_the_height_raster_fills_a_face_and_not_only_its_edges():
    """The reason this exists at all.

    An edge cloud casts the outline of a building and leaves its own middle
    lit, which is the bug this rasteriser is here to avoid.
    """
    raster = height_raster(square_roof(10.0, 10.0, 20.0, 12.0), 0.0, 0.0, 1.0, 40, 40)

    assert raster[20, 20] == pytest.approx(12.0)  # dead centre of the square
    assert raster[11, 11] == pytest.approx(12.0)  # just inside a corner
    assert raster[5, 5] == -np.inf  # outside it


def test_the_raster_keeps_the_higher_of_two_overlapping_faces():
    both = np.vstack([square_roof(0.0, 0.0, 10.0, 5.0), square_roof(0.0, 0.0, 10.0, 9.0)])
    raster = height_raster(both, 0.0, 0.0, 1.0, 12, 12)
    assert raster[5, 5] == pytest.approx(9.0)


def test_a_shadow_is_as_long_as_the_trigonometry_says():
    """A 20 m box with the sun due south at 45 degrees throws 20 m north."""
    side, height = 20.0, 20.0
    cell = 1.0
    n = 160
    raster = height_raster(square_roof(60.0, 60.0, side, height), 0.0, 0.0, cell, n, n)

    xs = (np.arange(n) + 0.5) * cell
    gx, gy = np.meshgrid(xs, xs)
    lit = ground_sunlit(raster, 0.0, 0.0, cell, gx, gy, 0.0, 45.0, 180.0, radius=250.0)

    column = 70  # x through the middle of the box
    assert not lit[70, column]  # under the box
    # The box top is 20 m up and the sun is at 45 degrees, so the shadow ends
    # 20 m north of the box's north edge.
    assert not lit[int(85 / cell), column]
    assert lit[int(105 / cell), column]
    # Nothing is thrown toward the sun.
    assert lit[int(55 / cell), column]


def test_a_taller_sun_throws_a_shorter_shadow():
    cell = 1.0
    n = 200
    raster = height_raster(square_roof(80.0, 80.0, 20.0, 30.0), 0.0, 0.0, cell, n, n)
    xs = (np.arange(n) + 0.5) * cell
    gx, gy = np.meshgrid(xs, xs)

    low = ground_sunlit(raster, 0.0, 0.0, cell, gx, gy, 0.0, 20.0, 180.0)
    high = ground_sunlit(raster, 0.0, 0.0, cell, gx, gy, 0.0, 60.0, 180.0)

    assert (~low).sum() > (~high).sum()


def test_nothing_is_lit_when_the_sun_is_down():
    raster = height_raster(square_roof(10.0, 10.0, 10.0, 8.0), 0.0, 0.0, 1.0, 40, 40)
    xs = np.arange(40) + 0.5
    gx, gy = np.meshgrid(xs, xs)
    assert not ground_sunlit(raster, 0.0, 0.0, 1.0, gx, gy, 0.0, -0.5, 180.0).any()


def test_empty_ground_is_lit_everywhere():
    raster = height_raster(np.empty((0, 3)), 0.0, 0.0, 1.0, 20, 20)
    xs = np.arange(20) + 0.5
    gx, gy = np.meshgrid(xs, xs)
    assert ground_sunlit(raster, 0.0, 0.0, 1.0, gx, gy, 0.0, 30.0, 180.0).all()
