"""Inter-building shading: the part cheap solar calculators skip.

PVGIS already applies a far horizon from a terrain DEM. Buildings are not in it.
A neighbouring block that steals a winter morning is invisible to every estimate
that stops at tilt and azimuth, and in a city that is the difference that
decides whether a roof is worth five to ten thousand euros.

The method is a horizon profile. For a point on a roof, every neighbouring
building is projected into (azimuth, elevation) and reduced to the highest
obstruction in each azimuth bin. After that the year is cheap: an hour is
shaded when the sun sits below the horizon at its own azimuth, which is an
array lookup rather than a ray cast. Eight thousand seven hundred and eighty
four hours then cost about as much as one.

Two things are deliberately separate:

* Beam is blocked or not blocked, per hour.
* Diffuse is reduced by however much sky the neighbours cover, which is a
  single view factor for the point and does not vary through the year.

Treating diffuse as unblocked is the usual shortcut and it flatters a shaded
roof badly in winter, when most of Zagreb's light is diffuse.

This is geometry and radiative bookkeeping. No model is trained and none runs.
"""

from __future__ import annotations

import numpy as np

# Azimuth resolution of the horizon profile. Half a degree is finer than the
# angular width of a roof ridge one street away, and 720 bins stay in cache.
DEFAULT_AZIMUTH_BINS = 720

# Blockers further than this are ignored. At 250 m a building would have to
# stand 60 m above the observer to reach even 13 degrees of elevation, and
# Zagreb outside a handful of towers does not.
DEFAULT_RADIUS_M = 250.0

# Edges are sampled at least this finely before projection, so a long ridge
# fills the azimuth bins it actually spans instead of only its two endpoints.
DEFAULT_DENSIFY_M = 2.0

# Blockers closer than this are dropped. The bearing of something nearer than
# the ground resolution of the blocker raster is not a measurement: snapping it
# to a cell centre can swing its azimuth by tens of degrees, and a point half a
# metre away lands at a near-vertical elevation that would black out a bin. Only
# a roof's own building is ever this close to it.
MIN_BLOCKER_DISTANCE_M = 1.5


def densify_segments(
    segments: np.ndarray, spacing: float = DEFAULT_DENSIFY_M
) -> tuple[np.ndarray, np.ndarray]:
    """Sample points along 3D segments at roughly ``spacing`` metres.

    ``segments`` is (n, 2, 3). Returns the (m, 3) points and, for each point,
    the index of the segment it came from, so callers can carry per-segment
    attributes through without recomputing the same arithmetic.

    Endpoints are always included, so a segment shorter than the spacing still
    contributes both of its ends. Written without a Python loop because a
    district holds a few hundred thousand segments and looping over them costs
    more than everything the shading model does afterwards.
    """
    if len(segments) == 0:
        return np.empty((0, 3)), np.empty(0, dtype=np.int64)

    start = segments[:, 0, :]
    end = segments[:, 1, :]
    length = np.linalg.norm(end - start, axis=1)
    steps = np.maximum(np.ceil(length / max(spacing, 1e-6)).astype(np.int64), 1)
    counts = steps + 1

    total = int(counts.sum())
    owner = np.repeat(np.arange(len(segments)), counts)
    bounds = np.concatenate([[0], np.cumsum(counts)])
    local = np.arange(total) - bounds[owner]
    t = (local / steps[owner])[:, None]

    points = start[owner] + t * (end[owner] - start[owner])
    return points, owner


# Ground resolution of the blocker height field. Chosen by measurement, not by
# taste: scripts/benchmark_shading.py compares 1, 2, 3 and 5 m against the
# undecimated cloud. 1 m costs a quarter of the undecimated run and moves the
# beam kept by a roof by 0.003 on average, which is far inside the uncertainty
# of a LoD 2.2 plane. 2 m is twice as cheap again and moves some roofs by 0.10.
DEFAULT_HEIGHT_FIELD_M = 1.0


def reduce_to_height_field(points: np.ndarray, cell: float = DEFAULT_HEIGHT_FIELD_M) -> np.ndarray:
    """Collapse blocker points to the highest one in each ground cell.

    Densified roof edges pile up: a building contributes many points at nearly
    the same ground position, and only the tallest of them can ever be the
    horizon in that direction. Keeping the maximum per cell turns the cloud into
    a building height field, which is both the standard way to shade a city and
    roughly an order of magnitude smaller.

    The centre of the cell is returned rather than the original point, so the
    result is a raster and not a biased subsample of the edges.
    """
    if len(points) == 0:
        return points

    ij = np.floor(points[:, :2] / cell).astype(np.int64)
    key = np.unique(ij, axis=0, return_inverse=True)
    cells, inverse = key[0], key[1].ravel()

    top = np.full(len(cells), -np.inf)
    np.maximum.at(top, inverse, points[:, 2])

    xy = (cells + 0.5) * cell
    return np.column_stack([xy, top])


# Bucket size for the blocker index. Well under the search radius, so that a
# cell can be rejected on height before any of its points are touched.
DEFAULT_CELL_M = 40.0


class PointGrid:
    """Bucketed index over blocker points, culling on distance and on height.

    A district holds a few million blocker points and every sample point only
    cares about a handful of them. Two rejections happen per cell rather than
    per point: cells outside the radius are never visited, and cells whose
    tallest point sits at or below the observer are skipped outright, because a
    neighbour lower than your roof cannot shade it. In a dense district that
    second test removes most of the city before a single distance is computed.
    """

    def __init__(self, points: np.ndarray, cell: float = DEFAULT_CELL_M):
        self.points = points
        self.cell = cell
        if len(points) == 0:
            self.origin = np.zeros(2)
            self.shape = (1, 1)
            self.starts = np.zeros(2, dtype=np.int64)
            self.order = np.empty(0, dtype=np.int64)
            self.cell_top = np.full(1, -np.inf)
            return

        self.origin = points[:, :2].min(axis=0)
        ij = np.floor((points[:, :2] - self.origin) / cell).astype(np.int64)
        self.shape = (int(ij[:, 0].max()) + 1, int(ij[:, 1].max()) + 1)
        n_cells = self.shape[0] * self.shape[1]

        flat = ij[:, 0] * self.shape[1] + ij[:, 1]
        self.order = np.argsort(flat, kind="stable")
        counts = np.bincount(flat, minlength=n_cells)
        self.starts = np.concatenate([[0], np.cumsum(counts)])

        self.cell_top = np.full(n_cells, -np.inf)
        np.maximum.at(self.cell_top, flat, points[:, 2])

    def near(self, origin: np.ndarray, radius: float = DEFAULT_RADIUS_M) -> np.ndarray:
        """Blocker points that could obstruct the sky seen from ``origin``."""
        if len(self.points) == 0:
            return self.points

        span = int(np.ceil(radius / self.cell))
        i0, j0 = np.floor((origin[:2] - self.origin) / self.cell).astype(np.int64)
        i_lo, i_hi = max(i0 - span, 0), min(i0 + span + 1, self.shape[0])
        j_lo, j_hi = max(j0 - span, 0), min(j0 + span + 1, self.shape[1])
        if i_lo >= i_hi or j_lo >= j_hi:
            return np.empty((0, 3))

        rows = np.arange(i_lo, i_hi)[:, None]
        cols = np.arange(j_lo, j_hi)[None, :]
        flat = (rows * self.shape[1] + cols).ravel()

        lo = self.starts[flat]
        hi = self.starts[flat + 1]
        keep = (hi > lo) & (self.cell_top[flat] > origin[2])
        if not keep.any():
            return np.empty((0, 3))

        lo, hi = lo[keep], hi[keep]
        counts = hi - lo
        total = int(counts.sum())
        # Expand the surviving cell ranges into one index array without a
        # Python loop over cells.
        bounds = np.concatenate([[0], np.cumsum(counts)])
        which = np.repeat(np.arange(len(lo)), counts)
        picks = self.order[np.arange(total) - bounds[which] + lo[which]]
        return self.points[picks]


def horizon_profile(
    origin: np.ndarray,
    points: np.ndarray,
    n_bins: int = DEFAULT_AZIMUTH_BINS,
    radius: float = DEFAULT_RADIUS_M,
) -> np.ndarray:
    """Highest obstruction elevation in each azimuth bin, seen from ``origin``.

    Returns degrees, length ``n_bins``, bin 0 centred on north and increasing
    clockwise. Bins with nothing in them come back at -90, meaning open sky.

    Blockers at or below the observer are discarded. That is exact rather than
    an approximation: the horizon is only ever consulted at positive elevations,
    by the beam test against a sun that is above the horizon and by a sky grid
    whose lowest patch sits above zero. A neighbour whose roof is below yours
    cannot take either from you. In a city most points fall to this test, and it
    is what makes the whole thing cheap.
    """
    horizon = np.full(n_bins, -90.0)
    if len(points) == 0:
        return horizon

    dz = points[:, 2] - origin[2]
    above = dz > 0.0
    if not above.any():
        return horizon
    points, dz = points[above], dz[above]

    dx = points[:, 0] - origin[0]
    dy = points[:, 1] - origin[1]
    ground = np.hypot(dx, dy)

    near = (ground <= radius) & (ground >= MIN_BLOCKER_DISTANCE_M)
    if not near.any():
        return horizon
    delta = np.column_stack([dx[near], dy[near], dz[near]])
    ground = ground[near]

    elevation = np.degrees(np.arctan2(delta[:, 2], ground))
    azimuth = np.degrees(np.arctan2(delta[:, 0], delta[:, 1])) % 360.0

    bins = np.minimum((azimuth / 360.0 * n_bins).astype(np.int64), n_bins - 1)
    np.maximum.at(horizon, bins, elevation)
    return horizon


def beam_visible(
    horizon: np.ndarray, sun_elevation: np.ndarray, sun_azimuth: np.ndarray
) -> np.ndarray:
    """True where the direct beam reaches the point, per hour."""
    n_bins = len(horizon)
    bins = np.minimum((sun_azimuth / 360.0 * n_bins).astype(np.int64), n_bins - 1)
    return (sun_elevation > 0.0) & (sun_elevation > horizon[bins])


class SkyGrid:
    """Fixed discretisation of the sky hemisphere, built once and reused.

    The patch directions do not depend on the roof or on the point, only on the
    resolution. Rebuilding them per roof made the sky view factor the dominant
    cost of a district and measured the allocator rather than the method.
    """

    def __init__(self, n_azimuth: int = DEFAULT_AZIMUTH_BINS, n_elevation: int = 90):
        self.n_azimuth = n_azimuth
        self.n_elevation = n_elevation
        self.elevation = (np.arange(n_elevation) + 0.5) * (90.0 / n_elevation)
        azimuth = (np.arange(n_azimuth) + 0.5) * (360.0 / n_azimuth)

        az = np.radians(azimuth)[:, None]
        el = np.radians(self.elevation)[None, :]

        # Direction cosines of each sky patch, east-north-up.
        self.dx = np.cos(el) * np.sin(az)
        self.dy = np.cos(el) * np.cos(az)
        self.dz = np.broadcast_to(np.sin(el), self.dx.shape).copy()
        # Solid angle weight of each patch on a lat-long grid.
        self.weight = np.broadcast_to(np.cos(el), self.dx.shape).copy()


_DEFAULT_SKY = SkyGrid()


def sky_view_factor(
    horizon: np.ndarray,
    plane_tilt: float,
    plane_azimuth: float,
    sky: SkyGrid | None = None,
) -> tuple[float, float]:
    """Diffuse view factors for a tilted plane, obstructed and open.

    Returns (obstructed, unobstructed). The ratio is what multiplies the
    diffuse component. Both are computed the same way over the same grid, so
    the ratio does not inherit the bias of the discretisation.

    Isotropic sky, integrating cos(incidence) over the visible hemisphere. An
    anisotropic model would put more weight near the sun and change the answer
    for a roof shaded to the south, which is noted rather than modelled.
    """
    sky = sky or _DEFAULT_SKY
    tilt = np.radians(plane_tilt)
    paz = np.radians(plane_azimuth if np.isfinite(plane_azimuth) else 0.0)
    normal = (
        np.sin(tilt) * np.sin(paz),
        np.sin(tilt) * np.cos(paz),
        np.cos(tilt),
    )

    cos_inc = sky.dx * normal[0] + sky.dy * normal[1] + sky.dz * normal[2]
    facing = np.clip(cos_inc, 0.0, None) * sky.weight
    open_total = float(facing.sum())

    visible = sky.elevation[None, :] > horizon[:, None]
    obstructed = float((facing * visible).sum())

    return obstructed, open_total


def height_raster(
    triangles: np.ndarray, x0: float, y0: float, cell: float, nx: int, ny: int
) -> np.ndarray:
    """Highest surface over each ground cell, from a triangle soup.

    ``triangles`` is (3t, 3): three consecutive rows per triangle. Returns an
    (ny, nx) array of heights, with -inf where nothing covers the cell. Row j is
    the band starting at ``y0 + j * cell``, so y increases with the row index.

    The horizon method samples roof edges, which is right for a point that
    wants a skyline. The ground wants filled roofs instead: an edge cloud casts
    the outline of a building and leaves a lit hole in the middle of its own
    shadow. So the roof faces are rasterised rather than sampled, one triangle
    at a time, taking the plane's height at each cell centre it covers.

    A triangle that covers no cell centre contributes nothing. At two metres
    that loses chimney-sized faces, and the roof they sit on covers the cell
    anyway.
    """
    raster = np.full((ny, nx), -np.inf)
    if len(triangles) == 0:
        return raster

    a, b, c = triangles[0::3], triangles[1::3], triangles[2::3]
    lo = np.minimum(np.minimum(a[:, :2], b[:, :2]), c[:, :2])
    hi = np.maximum(np.maximum(a[:, :2], b[:, :2]), c[:, :2])
    i0 = np.clip(np.floor((lo[:, 0] - x0) / cell).astype(np.int64), 0, nx - 1)
    i1 = np.clip(np.ceil((hi[:, 0] - x0) / cell).astype(np.int64), 0, nx - 1)
    j0 = np.clip(np.floor((lo[:, 1] - y0) / cell).astype(np.int64), 0, ny - 1)
    j1 = np.clip(np.ceil((hi[:, 1] - y0) / cell).astype(np.int64), 0, ny - 1)

    # Twice the signed area. A degenerate triangle has no interior to fill and
    # its edges belong to its neighbours.
    det = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    live = np.flatnonzero(np.abs(det) > 1e-12)

    for t in live:
        ax, ay, az = a[t]
        bx, by, bz = b[t]
        cx, cy, cz = c[t]
        xs = x0 + (np.arange(i0[t], i1[t] + 1) + 0.5) * cell
        ys = y0 + (np.arange(j0[t], j1[t] + 1) + 0.5) * cell
        gx = xs[None, :]
        gy = ys[:, None]

        w1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / det[t]
        w2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / det[t]
        w3 = 1.0 - w1 - w2
        inside = (w1 >= -1e-9) & (w2 >= -1e-9) & (w3 >= -1e-9)
        if not inside.any():
            continue

        z = np.where(inside, w1 * az + w2 * bz + w3 * cz, -np.inf)
        window = raster[j0[t] : j1[t] + 1, i0[t] : i1[t] + 1]
        np.maximum(window, z, out=window)

    return raster


def ground_sunlit(
    raster: np.ndarray,
    x0: float,
    y0: float,
    cell: float,
    points_x: np.ndarray,
    points_y: np.ndarray,
    ground_z: float,
    sun_elevation: float,
    sun_azimuth: float,
    radius: float = DEFAULT_RADIUS_M,
) -> np.ndarray:
    """Where the direct beam reaches a level ground plane, marching the raster.

    Same question the horizon profile answers for a roof, asked the cheap way
    round. A roof is one point wanting every hour of the year, so it pays for a
    profile once and looks the sun up in it. The ground is tens of thousands of
    points wanting a hundred or so instants, and a point at ground level keeps
    every blocker in the district above it, so a profile per point would cost
    more than the rest of the build put together. Marching the ray toward the
    sun until it clears the tallest thing in the raster costs a few dozen
    lookups instead.

    The march starts at zero, so a cell under a building comes back shadowed,
    which it is.
    """
    lit = np.zeros(points_x.shape, dtype=bool)
    if sun_elevation <= 0.0:
        return lit

    tan_el = np.tan(np.radians(sun_elevation))
    ux = np.sin(np.radians(sun_azimuth))
    uy = np.cos(np.radians(sun_azimuth))

    # Nothing in the raster can block a ray that has already climbed above the
    # highest point in it. In June that ends the march after a few metres.
    tallest = raster.max()
    if not np.isfinite(tallest) or tallest <= ground_z:
        return ~lit
    reach = min(radius, (tallest - ground_z) / max(tan_el, 1e-9))

    ny, nx = raster.shape
    blocked = np.zeros(points_x.shape, dtype=bool)
    for step in np.arange(0.0, reach + cell, cell):
        ix = np.clip(((points_x + step * ux - x0) / cell).astype(np.int64), 0, nx - 1)
        iy = np.clip(((points_y + step * uy - y0) / cell).astype(np.int64), 0, ny - 1)
        blocked |= raster[iy, ix] > ground_z + step * tan_el

    return ~blocked
