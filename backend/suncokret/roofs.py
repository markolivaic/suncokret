"""Roof-plane extraction from ZG3D LoD 2.2 multipatch geometry.

ZG3D ships each building as a set of planar rings (Esri multipatch, read by GDAL
as MultiPolygon Z). A ring is a wall, a roof plane, a floor slab, or a
degenerate sliver. This module turns those rings into oriented planes: area,
tilt, azimuth, centroid.

Everything here is geometry. No model is trained and none runs.

Orientation matters more than it looks. A roof plane and the floor slab under it
are both horizontal, and if normals are forced to point upward the floor becomes
a flat roof and doubles the building's usable area. The rings carry consistent
winding, so the direction is recoverable, but which winding means "out" has to
be decided per building.

The obvious rule, the sign of the divergence-theorem volume, is wrong here. It
is only origin-independent for a closed surface, and slightly over half of ZG3D
is not closed: the 2008 photogrammetric buildings are open shells with a roof
and walls and no floor, so their signed volume is dominated by the height of the
datum and misses the ``Volume`` attribute by a median factor of 16.

The rule used instead needs no closure: the highest meaningfully non-vertical
face of a building is a roof, so it points up. On the closed 2022 solids, where
the volume sign can be checked against the ``Volume`` attribute and is right,
the two rules agree on every building tested. On the open shells, where the
volume sign is unreliable, they disagree on about one building in eleven. The
signed volume is still computed, but as a closure diagnostic rather than as the
source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely

# Croatia's official projected CRS. Normals and areas are meaningless in degrees,
# so every ring is projected before anything is computed from it.
CRS_SOURCE = "EPSG:4326"
CRS_PROJECTED = "EPSG:3765"  # HTRS96 / Croatia TM

# A face whose outward normal leans further than this from straight up is a wall,
# not a roof. 60 deg keeps steep pitches and dormer cheeks while excluding
# facades. The survey reports how the survival rate moves with this cut.
ROOF_MAX_TILT_DEG = 60.0

# Below this the plane is flat for solar purposes: azimuth stops being meaningful
# because the normal is within measurement noise of straight up.
FLAT_MAX_TILT_DEG = 5.0

# A normal computed from a ring smaller than this is numerically unstable, so its
# tilt and azimuth are not trusted even though they exist.
MIN_TRUSTED_AREA_M2 = 1.0

# Roughly four modules. Smaller than this is not a PV surface, it is a dormer.
MIN_SEGMENT_AREA_M2 = 8.0

# A meaningful domestic install, about 4 kWp.
MIN_BUILDING_ROOF_AREA_M2 = 20.0

# How far |signed volume / Volume attribute| may sit from 1 before the building
# is called an unclosed solid whose face orientation is not trustworthy.
VOLUME_CLOSURE_TOLERANCE = 0.01

POLYGON_TYPE_ID = 3


@dataclass(frozen=True)
class Faces:
    """Oriented planar faces of one or more buildings, in EPSG:3765 metres.

    Arrays are parallel, one entry per face. ``building`` indexes back into the
    building array the faces were extracted from.

    ``tilt`` runs 0 to 180: 0 is a face pointing straight up, 90 is a wall, 180
    is a face pointing straight down. Keeping the full range is what lets a floor
    slab be told apart from the flat roof above it.
    """

    building: np.ndarray  # int64, index of the source building
    area: np.ndarray  # float64, true 3D area in m2
    tilt: np.ndarray  # float64, degrees from straight up
    azimuth: np.ndarray  # float64, degrees clockwise from north, 180 = south
    centroid: np.ndarray  # float64 (n, 3), x y z in EPSG:3765 metres
    normal: np.ndarray  # float64 (n, 3), unit, outward from the solid
    coords: np.ndarray  # float64 (m, 3), every ring vertex, closed rings
    offsets: np.ndarray  # int64 (n + 1,), slice of coords per face

    def __len__(self) -> int:
        return len(self.area)

    def ring(self, i: int) -> np.ndarray:
        """The closed vertex ring of face ``i``, in projected metres."""
        return self.coords[self.offsets[i] : self.offsets[i + 1]]

    def edges(self, mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Ring edges as an (n, 2, 3) segment array, with each one's building.

        Used to build the blocker cloud for shading: a building's silhouette is
        carried by its edges, and sampling only vertices leaves a long ridge
        filling two azimuth bins instead of the forty it really spans.
        """
        starts, ends = self.offsets[:-1], self.offsets[1:]
        if mask is not None:
            starts, ends = starts[mask], ends[mask]
        if len(starts) == 0:
            return np.empty((0, 2, 3)), np.empty(0, dtype=np.int64)

        # Mark every vertex that opens an edge: all of a ring except its last.
        keep = np.cumsum(
            np.bincount(starts, minlength=len(self.coords) + 1)
            - np.bincount(ends - 1, minlength=len(self.coords) + 1)
        )[: len(self.coords)].astype(bool)

        idx = np.flatnonzero(keep)
        if len(idx) == 0:
            return np.empty((0, 2, 3)), np.empty(0, dtype=np.int64)

        face = np.searchsorted(ends, idx, side="right")
        owner = self.building[mask][face] if mask is not None else self.building[face]
        return np.stack([self.coords[idx], self.coords[idx + 1]], axis=1), owner

    @property
    def is_roof(self) -> np.ndarray:
        return self.tilt <= ROOF_MAX_TILT_DEG

    @property
    def is_flat(self) -> np.ndarray:
        return self.tilt <= FLAT_MAX_TILT_DEG

    @property
    def is_downward(self) -> np.ndarray:
        """Floor slabs and soffits. Never a solar surface."""
        return self.tilt >= 180.0 - ROOF_MAX_TILT_DEG

    @property
    def is_trusted(self) -> np.ndarray:
        return self.area >= MIN_TRUSTED_AREA_M2


def newell_rings(
    coords: np.ndarray, offsets: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-ring unit normal, 3D area and centroid, vectorised over all rings.

    ``coords`` is (n, 3) with every ring stored closed (last vertex repeats the
    first). ``offsets`` has len(rings) + 1 entries giving each ring's slice.

    Newell's method is used rather than a cross product of two edges because it
    is exact for any planar polygon and degrades gracefully for the near-planar
    rings a photogrammetric pipeline actually produces. Winding is preserved:
    the caller decides which way is out.
    """
    starts, ends = offsets[:-1], offsets[1:]

    # Edge i runs from vertex i to vertex i + 1. The last vertex of each ring
    # duplicates the first, so every edge of every ring is covered by taking
    # consecutive pairs and dropping the pairs that straddle a ring boundary.
    a = coords[:-1]
    b = coords[1:]
    valid = np.ones(len(a), dtype=bool)
    valid[ends[:-1] - 1] = False

    contrib = np.empty((len(a), 3))
    contrib[:, 0] = (a[:, 1] - b[:, 1]) * (a[:, 2] + b[:, 2])
    contrib[:, 1] = (a[:, 2] - b[:, 2]) * (a[:, 0] + b[:, 0])
    contrib[:, 2] = (a[:, 0] - b[:, 0]) * (a[:, 1] + b[:, 1])
    contrib[~valid] = 0.0

    # reduceat indexes into the array it is given, so pad to full coord length.
    padded = np.zeros((len(coords), 3))
    padded[: len(contrib)] = contrib
    summed = np.add.reduceat(padded, starts, axis=0)

    norm = np.linalg.norm(summed, axis=1)
    area = norm / 2.0

    with np.errstate(invalid="ignore", divide="ignore"):
        unit = summed / norm[:, None]
    unit[~np.isfinite(unit).all(axis=1)] = 0.0

    # Mean of the ring's distinct vertices. Enough to place a plane in space and
    # to drive the divergence-theorem volume; it is not the area centroid.
    counts = np.maximum((ends - starts) - 1, 1)
    vsum = np.add.reduceat(np.where(valid[:, None], a, 0.0).astype(np.float64), starts, axis=0)
    centroid = vsum / counts[:, None]

    return unit, area, centroid


def signed_volumes(
    normal: np.ndarray,
    area: np.ndarray,
    centroid: np.ndarray,
    building: np.ndarray,
    n_buildings: int,
) -> np.ndarray:
    """Divergence-theorem volume per building, sign following ring winding.

    Only meaningful for a closed surface. Comparing its magnitude against the
    dataset's ``Volume`` attribute is how a building is told to be a closed
    solid or an open shell; it is a diagnostic, not the orientation rule.
    """
    contrib = np.einsum("ij,ij->i", centroid, normal) * area / 3.0
    out = np.zeros(n_buildings)
    np.add.at(out, building, contrib)
    return out


def outward_sign(
    normal: np.ndarray,
    area: np.ndarray,
    centroid: np.ndarray,
    building: np.ndarray,
    n_buildings: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-building multiplier that turns raw winding normals outward.

    The highest face that is not near-vertical is a roof, so it points up. This
    needs no closed solid, which is what makes it usable on the open 2008
    shells. Returns the sign and a mask of which buildings it could decide;
    a building with nothing but vertical faces decides nothing and is left
    alone rather than guessed at.
    """
    sign = np.ones(n_buildings)
    decided = np.zeros(n_buildings, dtype=bool)

    usable = (area > 0) & (np.abs(normal[:, 2]) > 0.5)
    idx = np.flatnonzero(usable)
    if len(idx) == 0:
        return sign, decided

    # Last entry per building after sorting by (building, height) is its highest
    # non-vertical face.
    order = idx[np.lexsort((centroid[idx, 2], building[idx]))]
    owner = building[order]
    last = np.ones(len(order), dtype=bool)
    last[:-1] = owner[:-1] != owner[1:]
    top = order[last]

    sign[building[top]] = np.sign(normal[top, 2])
    decided[building[top]] = True
    return sign, decided


def _explode_to_polygons(geometries: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flatten to polygons, keeping each one's index into ``geometries``.

    A handful of ZG3D records arrive as GeometryCollection rather than
    MultiPolygon, and a collection can nest. Expanding until nothing is left to
    expand, instead of assuming one level, keeps those records in the survey.
    """
    index = np.arange(len(geometries))
    current = geometries

    for _ in range(8):  # bounded; ZG3D nests once, this tolerates more
        type_ids = shapely.get_type_id(current)
        expandable = type_ids > POLYGON_TYPE_ID  # multi* and collections
        if not expandable.any():
            break
        parts, sub = shapely.get_parts(current[expandable], return_index=True)
        current = np.concatenate([current[~expandable], parts])
        index = np.concatenate([index[~expandable], index[expandable][sub]])

    is_polygon = shapely.get_type_id(current) == POLYGON_TYPE_ID
    return current[is_polygon], index[is_polygon]


def faces_from_geometries(
    geometries: np.ndarray,
    transformer,
    building_ids: np.ndarray | None = None,
    n_buildings: int | None = None,
) -> tuple[Faces, np.ndarray, np.ndarray]:
    """Extract outward-oriented faces from shapely MultiPolygon Z buildings.

    ``transformer`` is a pyproj Transformer from CRS_SOURCE to CRS_PROJECTED.

    ``building_ids`` are row indices into the returned per-building arrays, not
    dataset identifiers. Passing ZG3D fids here would index out of bounds, or
    worse, silently land on the wrong row. Callers that skip missing geometries
    pass the positions they kept, and set ``n_buildings`` to the full count so
    the skipped rows still exist in the output.

    Returns the faces, the per-building signed volume (a closure diagnostic) and
    a mask of the buildings whose orientation could be decided.
    """
    geometries = np.asarray(geometries, dtype=object)
    n_in = len(geometries)
    if building_ids is None:
        building_ids = np.arange(n_in)
    n_out = n_in if n_buildings is None else n_buildings

    parts, part_index = _explode_to_polygons(geometries)
    if len(parts) == 0:
        return _empty_faces(), np.zeros(n_out), np.zeros(n_out, dtype=bool)

    rings = shapely.get_exterior_ring(parts)
    counts = shapely.get_num_coordinates(rings)

    keep = counts >= 4  # a closed ring needs at least a triangle
    if not keep.any():
        return _empty_faces(), np.zeros(n_out), np.zeros(n_out, dtype=bool)
    rings, part_index, counts = rings[keep], part_index[keep], counts[keep]

    coords = shapely.get_coordinates(rings, include_z=True)
    offsets = np.concatenate([[0], np.cumsum(counts)])

    x, y = transformer.transform(coords[:, 0], coords[:, 1])
    projected = np.column_stack([x, y, coords[:, 2]])

    normal, area, centroid = newell_rings(projected, offsets)
    owner = np.asarray(building_ids)[part_index]

    volume = signed_volumes(normal, area, centroid, owner, n_out)
    sign, decided = outward_sign(normal, area, centroid, owner, n_out)

    # Flip whole buildings, never single faces. Per-face flipping is what
    # destroys the roof/floor distinction.
    normal = normal * sign[owner][:, None]

    tilt = np.degrees(np.arccos(np.clip(normal[:, 2], -1.0, 1.0)))
    # Compass bearing of the direction the plane faces: 0 N, 90 E, 180 S, 270 W.
    azimuth = np.degrees(np.arctan2(normal[:, 0], normal[:, 1])) % 360.0
    # A horizontal plane has no facing direction. Reporting one would be a
    # measurement the geometry does not contain.
    horizontal = (tilt <= FLAT_MAX_TILT_DEG) | (tilt >= 180.0 - FLAT_MAX_TILT_DEG)
    azimuth[horizontal] = np.nan

    return (
        Faces(
            building=owner,
            area=area,
            tilt=tilt,
            azimuth=azimuth,
            centroid=centroid,
            normal=normal,
            coords=projected,
            offsets=offsets.astype(np.int64),
        ),
        volume,
        decided,
    )


def _empty_faces() -> Faces:
    return Faces(
        building=np.empty(0, dtype=np.int64),
        area=np.empty(0),
        tilt=np.empty(0),
        azimuth=np.empty(0),
        centroid=np.empty((0, 3)),
        normal=np.empty((0, 3)),
        coords=np.empty((0, 3)),
        offsets=np.zeros(1, dtype=np.int64),
    )
