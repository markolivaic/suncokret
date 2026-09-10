"""Generate the nine shadow-study cells for the suncokret layout mockup.

The massing is invented, Donji-grad shaped: perimeter blocks around courtyards,
gabled bars, one post-war slab, the cathedral spires on the skyline. The SUN is
real, computed by backend/suncokret/sun.py for Zagreb on the dates and hours the
cell headers name, and the shadows are real projections of that geometry under
that sun. So the study behaves correctly even though the buildings are stand-ins.
"""

from __future__ import annotations

import math
import sys
from datetime import UTC, datetime

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, r"C:\Users\makif\NEXT\suncokret\backend")
from suncokret.sun import solar_position  # noqa: E402

LAT, LON = 45.8150, 15.9819

# columns: local clock hour. rows: date. CET in winter, CEST in summer.
HOURS = [9, 12, 15]
DATES = [
    ("21 December", 2020, 12, 21, 1),
    ("21 March", 2020, 3, 21, 1),
    ("21 June", 2020, 6, 21, 2),
]

# --- axonometric projection ---------------------------------------------------
COS30, SIN30 = math.cos(math.radians(30)), math.sin(math.radians(30))
_FIT = {"scale": 1.0, "ox": 0.0, "oy": 0.0}


def raw_project(x, y, z):
    return ((x - y) * COS30, (x + y) * SIN30 - z)


def project(x, y, z):
    rx, ry = raw_project(x, y, z)
    return (_FIT["ox"] + rx * _FIT["scale"], _FIT["oy"] + ry * _FIT["scale"])


def fit_scene(points, width, height, pad=16.0):
    """Set the projection so the whole scene lands inside the frame."""
    pts = [raw_project(*p) for p in points]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
    _FIT["scale"] = scale
    _FIT["ox"] = pad - min(xs) * scale + (width - 2 * pad - span_x * scale) / 2
    _FIT["oy"] = pad - min(ys) * scale + (height - 2 * pad - span_y * scale) / 2


def ppath(pts3):
    return " ".join(
        f"{'M' if i == 0 else 'L'}{px:.1f},{py:.1f}"
        for i, (px, py) in enumerate(project(*p) for p in pts3)
    ) + " Z"


# --- the scene ----------------------------------------------------------------
def bar(x0, y0, w, d, eaves, ridge_h, ridge_axis, simplified=False):
    """A gabled bar building. ridge_axis 'x' or 'y'."""
    return dict(
        x0=x0, y0=y0, w=w, d=d, eaves=eaves, ridge=ridge_h,
        axis=ridge_axis, simplified=simplified,
    )


def perimeter_block(bx, by, bw, bd, depth, h, simplified=(), seed=0):
    """Four bars around a courtyard, the Donji grad signature."""
    rng = np.random.default_rng(seed)
    out = []
    specs = [
        (bx, by, bw, depth, "x"),                       # north bar
        (bx, by + bd - depth, bw, depth, "x"),          # south bar
        (bx, by + depth, depth, bd - 2 * depth, "y"),   # west bar
        (bx + bw - depth, by + depth, depth, bd - 2 * depth, "y"),
    ]
    for i, (x0, y0, w, d, axis) in enumerate(specs):
        e = h + float(rng.uniform(-1.6, 1.6))
        out.append(bar(x0, y0, w, d, e, e + 5.0, axis, simplified=(i in simplified)))
    return out


BUILDINGS = []
BUILDINGS += perimeter_block(0, 0, 74, 56, 13, 18.0, simplified=(1, 2), seed=1)
BUILDINGS += perimeter_block(92, 0, 74, 56, 13, 20.0, simplified=(0,), seed=2)
BUILDINGS += perimeter_block(0, 74, 74, 56, 13, 17.0, simplified=(3,), seed=3)
BUILDINGS += perimeter_block(92, 74, 74, 56, 13, 19.0, simplified=(1, 3), seed=4)
# a post-war slab dropped into the block structure, the thing that casts far
BUILDINGS.append(bar(184, 18, 16, 92, 34.0, 35.0, "y"))

SPIRES = [(150, -26, 66.0), (162, -26, 66.0)]  # cathedral, on the skyline

GX0, GY0, GX1, GY1 = -34, -40, 216, 150
GROUND = [(GX0, GY0, 0.0), (GX1, GY0, 0.0), (GX1, GY1, 0.0), (GX0, GY1, 0.0)]

# Every point the projection has to accommodate, including the longest shadow
# the low December sun throws, so the frame does not crop it.
SCENE_POINTS = list(GROUND) + [
    (b["x0"] + dx * b["w"], b["y0"] + dy * b["d"], z)
    for b in BUILDINGS
    for dx in (0, 1)
    for dy in (0, 1)
    for z in (0.0, b["ridge"])
] + [(sx, sy, sh) for sx, sy, sh in SPIRES]


def footprint(b):
    return Polygon([
        (b["x0"], b["y0"]),
        (b["x0"] + b["w"], b["y0"]),
        (b["x0"] + b["w"], b["y0"] + b["d"]),
        (b["x0"], b["y0"] + b["d"]),
    ])


def roof_planes(b):
    """Two gabled slopes as (polygon3d, outward normal)."""
    x0, y0, w, d, e, r = b["x0"], b["y0"], b["w"], b["d"], b["eaves"], b["ridge"]
    if b["axis"] == "x":
        my = y0 + d / 2
        a = [(x0, y0, e), (x0 + w, y0, e), (x0 + w, my, r), (x0, my, r)]
        c = [(x0, my, r), (x0 + w, my, r), (x0 + w, y0 + d, e), (x0, y0 + d, e)]
        rise, run = r - e, d / 2
        n1 = (0.0, -run, 0.0)
        n2 = (0.0, run, 0.0)
    else:
        mx = x0 + w / 2
        a = [(x0, y0, e), (mx, y0, r), (mx, y0 + d, r), (x0, y0 + d, e)]
        c = [(mx, y0, r), (x0 + w, y0, e), (x0 + w, y0 + d, e), (mx, y0 + d, r)]
        rise, run = r - e, w / 2
        n1 = (-run, 0.0, 0.0)
        n2 = (run, 0.0, 0.0)
    out = []
    for poly, nx in ((a, n1), (c, n2)):
        n = np.array([nx[0], nx[1], run * 0 + rise * 0 + 0.0])
        n = np.array([nx[0], nx[1], run])
        n = n / np.linalg.norm(n)
        out.append((poly, n))
    return out


def sun_vector(el, az):
    e, a = math.radians(el), math.radians(az)
    return np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])


def ground_shadow(b, sv):
    """Prism shadow: hull of the footprint and the footprint slid by the sun."""
    if sv[2] <= 0.02:
        return None
    k = b["ridge"] / sv[2]
    off = (-sv[0] * k, -sv[1] * k)
    fp = footprint(b)
    slid = Polygon([(x + off[0], y + off[1]) for x, y in fp.exterior.coords])
    return unary_union([fp, slid]).convex_hull


def render_cell(date_label, y, m, dd, tz, hour, uid="c", width=600, height=300):
    stamp = datetime(y, m, dd, hour - tz, 0, tzinfo=UTC).timestamp()
    el, az = solar_position(np.array([stamp]), LAT, LON)
    el, az = float(el[0]), float(az[0])
    sv = sun_vector(el, az)

    fit_scene(SCENE_POINTS, width, height)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        f'class="cell-svg">',
        f'<rect width="{width}" height="{height}" fill="#e3d9c2"/>',
        f'<defs><pattern id="simp{uid}" width="7" height="7" '
        f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="2.6" height="7" fill="#463f2f" opacity=".45"/>'
        f'</pattern></defs>',
    ]

    if el <= 0.5:
        parts.append(
            f'<rect width="{width}" height="{height}" fill="#3b4252" opacity=".82"/>'
        )
        parts.append(
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
            f'fill="#cfd6e2" font-size="19" font-family="monospace">sun is down</text>'
        )
        parts.append("</svg>")
        return "".join(parts), el, az

    # the ground the shadows fall on, so they read as cast rather than as fill
    parts.append(
        f'<path d="{ppath(GROUND)}" fill="#dccfb4" stroke="#c2b494" stroke-width="0.8"/>'
    )

    # ground shadows, merged so overlaps do not darken twice
    shadows = [s for s in (ground_shadow(b, sv) for b in BUILDINGS) if s is not None]
    for sx, sy, sh in SPIRES:
        k = sh / sv[2]
        base = Polygon([(sx - 4, sy - 4), (sx + 4, sy - 4), (sx + 4, sy + 4), (sx - 4, sy + 4)])
        slid = Polygon([(x - sv[0] * k, y - sv[1] * k) for x, y in base.exterior.coords])
        shadows.append(unary_union([base, slid]).convex_hull)
    merged = unary_union(shadows)
    geoms = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]
    for g in geoms:
        pts = [(x, yy, 0.0) for x, yy in g.exterior.coords]
        parts.append(f'<path d="{ppath(pts)}" fill="#b3a382" opacity=".95"/>')

    # spires, drawn behind the blocks
    for sx, sy, sh in SPIRES:
        bx, by = project(sx, sy, 0)
        tx, ty = project(sx, sy, sh)
        parts.append(
            f'<path d="M{bx - 7:.1f},{by:.1f} L{tx:.1f},{ty:.1f} L{bx + 7:.1f},{by:.1f} Z" '
            f'fill="#d2c7ae" stroke="#8a7f66" stroke-width="0.7"/>'
        )

    # painter's algorithm: far to near
    for b in sorted(BUILDINGS, key=lambda b: b["x0"] + b["y0"]):
        x0, y0, w, d, e = b["x0"], b["y0"], b["w"], b["d"], b["eaves"]
        walls = [
            [(x0, y0 + d, 0), (x0 + w, y0 + d, 0), (x0 + w, y0 + d, e), (x0, y0 + d, e)],
            [(x0 + w, y0, 0), (x0 + w, y0 + d, 0), (x0 + w, y0 + d, e), (x0 + w, y0, e)],
        ]
        for wall in walls:
            parts.append(
                f'<path d="{ppath(wall)}" fill="#d6cab0" stroke="#8a7f66" stroke-width="0.6"/>'
            )
        for poly, n in roof_planes(b):
            lit = float(np.dot(n, sv))
            centre = np.mean([p[:2] for p in poly], axis=0)
            top = max(p[2] for p in poly)
            blocked = False
            if lit > 0:
                step = 3.0
                for t in np.arange(step, 190.0, step):
                    px, py = centre[0] + sv[0] * t, centre[1] + sv[1] * t
                    pz = top + sv[2] * t
                    for other in BUILDINGS:
                        if other is b:
                            continue
                        if (
                            other["x0"] <= px <= other["x0"] + other["w"]
                            and other["y0"] <= py <= other["y0"] + other["d"]
                            and pz < other["ridge"]
                        ):
                            blocked = True
                            break
                    if blocked:
                        break
            if lit <= 0 or blocked:
                fill = "#8f98a8"  # in shadow
            else:
                t = min(lit, 1.0)
                fill = ["#f0bb3c", "#f6d268", "#fce69a"][min(int(t * 3), 2)]
            hatch = ' fill-opacity="1"'
            parts.append(
                f'<path d="{ppath(poly)}" fill="{fill}" stroke="#7a6f58" '
                f'stroke-width="0.6"{hatch}/>'
            )
            if b["simplified"]:
                parts.append(
                    f'<path d="{ppath(poly)}" fill="url(#simp' + uid + ')" stroke="none"/>'
                )

    parts.append("</svg>")
    return "".join(parts), el, az


def main():
    cells = []
    for label, y, m, dd, tz in DATES:
        row = []
        for hour in HOURS:
            uid = f"{m:02d}{hour:02d}"
            svg, el, az = render_cell(label, y, m, dd, tz, hour, uid)
            row.append((hour, svg, el, az))
        cells.append((label, row))

    out = []
    for label, row in cells:
        out.append(f"ROW::{label}")
        for hour, svg, el, az in row:
            out.append(f"CELL::{hour:02d}:00::{el:.1f}::{az:.1f}::{svg}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
