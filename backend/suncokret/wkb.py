"""Normalise the WKB flavours GDAL emits for ZG3D multipatch geometry.

Reading ZG3D through OpenFileGDB gives back three different things, and the
layer header advertises only the first:

    0x80000006  EWKB MultiPolygon Z    the overwhelming majority
    0x80000007  EWKB GeometryCollection Z
    1016        ISO TIN Z              rare, and shapely refuses it

A TIN is a collection of Triangles, and a Triangle is a Polygon whose ring
count is always one. The byte layout after the type code is identical, so
converting is a rewrite of the type code and nothing else: no coordinate is
touched and no geometry is approximated.

    ISO 15 PolyhedralSurface -> 6 MultiPolygon
    ISO 16 TIN               -> 6 MultiPolygon
    ISO 17 Triangle          -> 3 Polygon

The dimensionality offset (+1000 Z, +2000 M, +3000 ZM) is preserved, so 1016
becomes 1006 and 1017 becomes 1003.

This exists because dropping the records that fail to parse would measure a
population that had already cleaned itself.
"""

from __future__ import annotations

import struct

import numpy as np
import shapely

# ISO WKB codes that describe a surface shapely will not read, mapped to the
# structurally identical code it will.
_REWRITE = {15: 6, 16: 6, 17: 3}

# Types whose body is a count followed by that many nested geometries.
_CONTAINERS = {4, 5, 6, 7}


def _decode(type_code: int) -> tuple[int, int]:
    """Return (base geometry type, coordinates per vertex)."""
    if type_code & 0x80000000 or type_code & 0x40000000:
        # PostGIS-style EWKB: high bits flag Z and M.
        dims = 2 + bool(type_code & 0x80000000) + bool(type_code & 0x40000000)
        return type_code & 0x0FFFFFFF, dims
    quotient, base = divmod(type_code, 1000)
    return base, {0: 2, 1: 3, 2: 3, 3: 4}[quotient]


def _walk(buf: memoryview, out: bytearray, pos: int) -> int:
    """Rewrite unsupported type codes in place, returning the end offset."""
    order = "<" if buf[pos] == 1 else ">"
    pos += 1

    (type_code,) = struct.unpack_from(order + "I", buf, pos)
    base, dims = _decode(type_code)

    replacement = _REWRITE.get(base)
    if replacement is not None:
        struct.pack_into(order + "I", out, pos, type_code - base + replacement)
        base = replacement
    pos += 4

    if base == 1:  # Point
        return pos + dims * 8
    if base == 2:  # LineString
        (n,) = struct.unpack_from(order + "I", buf, pos)
        return pos + 4 + n * dims * 8
    if base == 3:  # Polygon
        (n_rings,) = struct.unpack_from(order + "I", buf, pos)
        pos += 4
        for _ in range(n_rings):
            (n_pts,) = struct.unpack_from(order + "I", buf, pos)
            pos += 4 + n_pts * dims * 8
        return pos
    if base in _CONTAINERS:
        (n,) = struct.unpack_from(order + "I", buf, pos)
        pos += 4
        for _ in range(n):
            pos = _walk(buf, out, pos)
        return pos

    raise ValueError(f"unhandled WKB base type {base} (code {type_code})")


def normalise(blob: bytes) -> bytes:
    """Return WKB shapely can parse, rewriting ISO surface codes if present."""
    out = bytearray(blob)
    _walk(memoryview(blob), out, 0)
    return bytes(out)


def from_wkb(blobs) -> tuple[np.ndarray, list[int]]:
    """Parse an array of WKB blobs, repairing the ones shapely rejects.

    Returns the geometry array and the indices that needed repair, so the survey
    can report it as a failure mode rather than hide it. The fast path is tried
    first because repair is needed for well under one blob in ten thousand.
    """
    try:
        return shapely.from_wkb(blobs), []
    except Exception:
        pass

    geoms = np.empty(len(blobs), dtype=object)
    repaired: list[int] = []
    for i, blob in enumerate(blobs):
        if blob is None:
            geoms[i] = None
            continue
        try:
            geoms[i] = shapely.from_wkb(blob)
        except Exception:
            geoms[i] = shapely.from_wkb(normalise(blob))
            repaired.append(i)
    return geoms, repaired
