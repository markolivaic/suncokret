"""Geometry tests against shapes whose answers are known by hand.

Every case here is one where the right answer can be written down without
running the code, which is the only way a geometry test says anything.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from suncokret.roofs import (
    faces_from_geometries,
    newell_rings,
    outward_sign,
    signed_volumes,
)


class IdentityTransformer:
    """Stands in for pyproj when the input is already in metres."""

    @staticmethod
    def transform(x, y):
        return np.asarray(x), np.asarray(y)


def ring(*points):
    """Close a ring the way the reader hands them over."""
    pts = list(points) + [points[0]]
    return np.array(pts, dtype=float)


def pack(*rings):
    coords = np.vstack(rings)
    offsets = np.concatenate([[0], np.cumsum([len(r) for r in rings])])
    return coords, offsets


def test_horizontal_unit_square_has_unit_area_and_vertical_normal():
    coords, offsets = pack(ring((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)))
    normal, area, centroid = newell_rings(coords, offsets)

    assert area[0] == pytest.approx(1.0)
    assert normal[0] == pytest.approx([0.0, 0.0, 1.0])
    assert centroid[0] == pytest.approx([0.5, 0.5, 0.0])


def test_winding_reverses_the_normal_but_not_the_area():
    ccw, o1 = pack(ring((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)))
    cw, o2 = pack(ring((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)))

    n1, a1, _ = newell_rings(ccw, o1)
    n2, a2, _ = newell_rings(cw, o2)

    assert a1[0] == pytest.approx(a2[0])
    assert n1[0] == pytest.approx(-n2[0])


def test_area_of_a_tilted_plane_grows_as_one_over_cosine():
    # A 1 x 1 footprint raised on one edge: true area is 1 / cos(pitch).
    pitch = math.radians(30.0)
    rise = math.tan(pitch)
    coords, offsets = pack(ring((0, 0, 0), (1, 0, 0), (1, 1, rise), (0, 1, rise)))
    _, area, _ = newell_rings(coords, offsets)
    assert area[0] == pytest.approx(1.0 / math.cos(pitch))


def unit_cube_rings(side=1.0):
    """Six faces of a cube, all wound so the normals point outward."""
    s = side
    return [
        ring((0, 0, 0), (0, s, 0), (s, s, 0), (s, 0, 0)),  # bottom, -z
        ring((0, 0, s), (s, 0, s), (s, s, s), (0, s, s)),  # top, +z
        ring((0, 0, 0), (s, 0, 0), (s, 0, s), (0, 0, s)),  # -y
        ring((0, s, 0), (0, s, s), (s, s, s), (s, s, 0)),  # +y
        ring((0, 0, 0), (0, 0, s), (0, s, s), (0, s, 0)),  # -x
        ring((s, 0, 0), (s, s, 0), (s, s, s), (s, 0, s)),  # +x
    ]


def test_signed_volume_of_a_closed_cube_is_its_volume():
    coords, offsets = pack(*unit_cube_rings(2.0))
    normal, area, centroid = newell_rings(coords, offsets)
    building = np.zeros(len(area), dtype=np.int64)

    volume = signed_volumes(normal, area, centroid, building, 1)
    assert volume[0] == pytest.approx(8.0)


def test_signed_volume_is_independent_of_where_the_origin_sits():
    """True for a closed surface, and the reason it fails on open shells."""
    coords, offsets = pack(*unit_cube_rings(2.0))
    shifted = coords + np.array([1000.0, -500.0, 120.0])
    building = np.zeros(6, dtype=np.int64)

    here = signed_volumes(*newell_rings(coords, offsets), building, 1)
    there = signed_volumes(*newell_rings(shifted, offsets), building, 1)
    assert here[0] == pytest.approx(there[0])


def test_open_shell_volume_does_depend_on_the_origin():
    """The failure that rules the volume sign out as an orientation rule."""
    rings_without_floor = unit_cube_rings(2.0)[1:]
    coords, offsets = pack(*rings_without_floor)
    shifted = coords + np.array([0.0, 0.0, 120.0])

    n1, a1, c1 = newell_rings(coords, offsets)
    n2, a2, c2 = newell_rings(shifted, offsets)
    b = np.zeros(len(a1), dtype=np.int64)

    near = signed_volumes(n1, a1, c1, b, 1)[0]
    far = signed_volumes(n2, a2, c2, b, 1)[0]
    assert abs(far - near) > 100.0


def test_outward_sign_uses_the_highest_non_vertical_face():
    coords, offsets = pack(*unit_cube_rings(2.0))
    normal, area, centroid = newell_rings(coords, offsets)
    building = np.zeros(len(area), dtype=np.int64)

    sign, decided = outward_sign(normal, area, centroid, building, 1)
    assert decided[0]
    assert sign[0] == pytest.approx(1.0)

    flipped = -normal
    sign, decided = outward_sign(flipped, area, centroid, building, 1)
    assert sign[0] == pytest.approx(-1.0)


def test_outward_sign_declines_to_guess_when_every_face_is_vertical():
    walls = unit_cube_rings(2.0)[2:]
    coords, offsets = pack(*walls)
    normal, area, centroid = newell_rings(coords, offsets)
    building = np.zeros(len(area), dtype=np.int64)

    _, decided = outward_sign(normal, area, centroid, building, 1)
    assert not decided[0]


def build_faces(rings_by_building):
    """Run the real extraction over hand-built shapely-free geometry."""
    import shapely

    geoms = []
    for rings in rings_by_building:
        geoms.append(shapely.MultiPolygon([shapely.Polygon(r) for r in rings]))
    return faces_from_geometries(np.array(geoms, dtype=object), IdentityTransformer())


def test_cube_yields_one_flat_roof_and_one_floor_not_two_roofs():
    """The bug that made a floor slab double a building's usable roof."""
    faces, volume, decided = build_faces([unit_cube_rings(2.0)])

    assert len(faces) == 6
    assert volume[0] == pytest.approx(8.0)
    assert decided[0]

    roofs = faces.is_roof & (faces.area > 0)
    floors = faces.is_downward & (faces.area > 0)
    assert roofs.sum() == 1
    assert floors.sum() == 1
    assert faces.area[roofs].sum() == pytest.approx(4.0)
    assert faces.tilt[roofs][0] == pytest.approx(0.0, abs=1e-9)
    assert faces.tilt[floors][0] == pytest.approx(180.0, abs=1e-9)


def test_flat_roof_reports_no_azimuth():
    faces, _, _ = build_faces([unit_cube_rings(2.0)])
    flat = faces.is_flat & faces.is_roof
    assert np.isnan(faces.azimuth[flat]).all()


@pytest.mark.parametrize(
    "corner_a, corner_b, expected_azimuth",
    [
        # Raise the north edge: the plane looks south.
        ((0, 1, 1), (1, 1, 1), 180.0),
        # Raise the south edge: it looks north.
        ((0, 0, 1), (1, 0, 1), 0.0),
        # Raise the east edge: it looks west.
        ((1, 0, 1), (1, 1, 1), 270.0),
    ],
)
def test_azimuth_is_a_compass_bearing_of_the_direction_faced(corner_a, corner_b, expected_azimuth):
    import shapely

    base = {(0, 0), (1, 0), (1, 1), (0, 1)}
    raised = {corner_a[:2], corner_b[:2]}
    pts = []
    for xy in [(0, 0), (1, 0), (1, 1), (0, 1)]:
        z = 1.0 if xy in raised else 0.0
        pts.append((xy[0], xy[1], z))
    assert base  # the footprint is the unit square either way

    geom = shapely.MultiPolygon([shapely.Polygon(pts + [pts[0]])])
    faces, _, _ = faces_from_geometries(np.array([geom], dtype=object), IdentityTransformer())
    # Orientation is undecided for a lone plane only if it is vertical; it is not.
    assert faces.azimuth[0] == pytest.approx(expected_azimuth, abs=1e-6)
    assert faces.tilt[0] == pytest.approx(45.0, abs=1e-6)


def test_faces_ring_round_trips_the_input_geometry():
    faces, _, _ = build_faces([unit_cube_rings(2.0)])
    for i in range(len(faces)):
        r = faces.ring(i)
        assert len(r) == 5
        assert r[0] == pytest.approx(r[-1])


def test_edges_returns_four_edges_per_quad_with_the_right_owner():
    faces, _, _ = build_faces([unit_cube_rings(2.0), unit_cube_rings(3.0)])
    segments, owners = faces.edges()
    assert len(segments) == 12 * 4
    assert set(np.unique(owners).tolist()) == {0, 1}
    lengths = np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1)
    assert lengths.min() > 0


def test_empty_input_is_survivable():
    faces, volume, decided = faces_from_geometries(
        np.array([], dtype=object), IdentityTransformer()
    )
    assert len(faces) == 0
    assert len(volume) == 0
    assert len(decided) == 0


def test_missing_geometry_still_gets_a_row():
    """A survey that drops unreadable rows measures a self-cleaned population."""
    import shapely

    geoms = np.array(
        [shapely.MultiPolygon([shapely.Polygon(r) for r in unit_cube_rings(2.0)])],
        dtype=object,
    )
    faces, volume, decided = faces_from_geometries(
        geoms, IdentityTransformer(), np.array([1]), n_buildings=3
    )
    assert len(volume) == 3
    assert len(decided) == 3
    assert not decided[0] and not decided[2]
    assert decided[1]
