"""WKB normalisation, against blobs assembled byte by byte.

ZG3D arrives as three different WKB flavours and only one of them is what the
layer header advertises. The rare ones are the whole point of this module, so
the tests build them explicitly rather than hoping the sample data contains one.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest
import shapely

from suncokret.wkb import from_wkb, normalise

ISO_Z = 1000
POLYGON, MULTIPOLYGON, COLLECTION = 3, 6, 7
POLYHEDRAL_SURFACE, TIN, TRIANGLE = 15, 16, 17


def ring_bytes(points):
    out = struct.pack("<I", len(points))
    for x, y, z in points:
        out += struct.pack("<3d", x, y, z)
    return out


def geom_bytes(type_code, body):
    return struct.pack("<BI", 1, type_code) + body


def triangle_z(a, b, c):
    return geom_bytes(TRIANGLE + ISO_Z, struct.pack("<I", 1) + ring_bytes([a, b, c, a]))


def polygon_z(points):
    closed = list(points) + [points[0]]
    return geom_bytes(POLYGON + ISO_Z, struct.pack("<I", 1) + ring_bytes(closed))


def collection_of(*blobs):
    return geom_bytes(COLLECTION + ISO_Z, struct.pack("<I", len(blobs)) + b"".join(blobs))


def tin_z(*triangles):
    return geom_bytes(TIN + ISO_Z, struct.pack("<I", len(triangles)) + b"".join(triangles))


def polyhedral_z(*polygons):
    return geom_bytes(
        POLYHEDRAL_SURFACE + ISO_Z, struct.pack("<I", len(polygons)) + b"".join(polygons)
    )


A = (0.0, 0.0, 10.0)
B = (1.0, 0.0, 10.0)
C = (1.0, 1.0, 12.0)
D = (0.0, 1.0, 12.0)


def test_shapely_really_does_reject_a_tin():
    """If this ever starts passing, the module can go."""
    with pytest.raises(shapely.errors.GEOSException):
        shapely.from_wkb(tin_z(triangle_z(A, B, C)))


def test_normalised_tin_parses_as_a_multipolygon():
    geom = shapely.from_wkb(normalise(tin_z(triangle_z(A, B, C), triangle_z(A, C, D))))

    assert geom.geom_type == "MultiPolygon"
    assert len(geom.geoms) == 2


def test_normalising_a_tin_moves_no_coordinate():
    geom = shapely.from_wkb(normalise(tin_z(triangle_z(A, B, C))))
    coords = np.asarray(geom.geoms[0].exterior.coords)

    assert coords.shape == (4, 3)
    assert coords[0] == pytest.approx(A)
    assert coords[1] == pytest.approx(B)
    assert coords[2] == pytest.approx(C)
    assert coords[3] == pytest.approx(A)


def test_normalised_polyhedral_surface_parses_too():
    blob = polyhedral_z(polygon_z([A, B, C, D]))
    geom = shapely.from_wkb(normalise(blob))

    assert geom.geom_type == "MultiPolygon"
    assert geom.geoms[0].area == pytest.approx(1.0)


def test_a_tin_nested_inside_a_collection_is_reached():
    """One level of walking would miss this, and ZG3D does ship it."""
    blob = collection_of(polygon_z([A, B, C, D]), tin_z(triangle_z(A, B, C)))
    geom = shapely.from_wkb(normalise(blob))

    assert geom.geom_type == "GeometryCollection"
    types = sorted(g.geom_type for g in geom.geoms)
    assert types == ["MultiPolygon", "Polygon"]


def test_a_blob_that_needs_nothing_is_returned_unchanged():
    blob = polygon_z([A, B, C, D])
    assert normalise(blob) == blob


def test_from_wkb_takes_the_fast_path_when_nothing_is_broken():
    blobs = [polygon_z([A, B, C, D]), polygon_z([A, B, C, D])]
    geoms, repaired = from_wkb(blobs)

    assert repaired == []
    assert all(g.geom_type == "Polygon" for g in geoms)


def test_from_wkb_repairs_only_what_needs_it_and_says_which():
    blobs = [
        polygon_z([A, B, C, D]),
        tin_z(triangle_z(A, B, C)),
        polygon_z([A, B, C, D]),
    ]
    geoms, repaired = from_wkb(blobs)

    assert repaired == [1]
    assert geoms[0].geom_type == "Polygon"
    assert geoms[1].geom_type == "MultiPolygon"
    assert geoms[2].geom_type == "Polygon"


def test_from_wkb_keeps_a_row_for_a_missing_geometry():
    geoms, repaired = from_wkb([polygon_z([A, B, C, D]), None, tin_z(triangle_z(A, B, C))])

    assert len(geoms) == 3
    assert geoms[1] is None
    assert repaired == [2]


def test_an_unknown_type_is_refused_rather_than_guessed_at():
    blob = geom_bytes(99, struct.pack("<I", 0))
    with pytest.raises(ValueError, match="unhandled WKB base type"):
        normalise(blob)
