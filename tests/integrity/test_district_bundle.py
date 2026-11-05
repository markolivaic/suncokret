"""The district bundle the page reads holds believable data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DISTRICT = ROOT / "web" / "public" / "district"
MANIFEST = json.loads((DISTRICT / "kaptol-donji-grad.json").read_text(encoding="utf-8"))
BLOB = (DISTRICT / MANIFEST["bin"]).read_bytes()


def buffer(name: str, dtype: str) -> np.ndarray:
    spec = MANIFEST["buffers"][name]
    return np.frombuffer(BLOB, dtype=dtype, count=spec["count"], offset=spec["offset"])


def test_every_kept_share_is_finite_and_a_plane_that_loses_sky_loses_some_total():
    kept = buffer("selectableKept", "<f4")
    beam_kept = buffer("selectableBeamKept", "<f4")
    sky_kept = buffer("selectableSkyKept", "<f4")

    assert np.isfinite(kept).all(), "a non-finite kept fraction reached the bundle"
    assert np.isfinite(beam_kept).all()
    assert np.isfinite(sky_kept).all()
    assert ((kept > 0) & (kept <= 1)).all()

    losing_sky = sky_kept < 0.99
    assert losing_sky.any(), "no shaded planes at all"
    assert not (losing_sky & (kept >= 0.999)).any(), (
        "a plane loses part of its sky and still claims to keep everything"
    )


def test_the_ground_shadows_move_with_the_season():
    """Less of the ground reaches the noon sun in December than in March, and in March than June."""
    ground, sweep = MANIFEST["ground"], MANIFEST["sweep"]
    spec = MANIFEST["buffers"]["groundStates"]
    assert spec["count"] == len(sweep["dates"]) * sweep["steps"] * ground["bytes_per_state"]
    states = buffer("groundStates", "u1").reshape(
        len(sweep["dates"]), sweep["steps"], ground["bytes_per_state"]
    )

    cells = ground["cells"] * ground["cells"]
    noon = round((12.0 - sweep["start_hour"]) * 60 / sweep["step_minutes"])
    lit = [
        np.unpackbits(states[d, noon], bitorder="little")[:cells].mean()
        for d in range(len(sweep["dates"]))
    ]

    assert sweep["dates"] == ["21 December", "21 March", "21 June"]
    assert lit[0] < lit[1] < lit[2], f"noon lit fractions do not increase with the season: {lit}"
    # Neither a blank sheet nor a solid shadow: both would mean the march found nothing.
    assert 0.02 < lit[0] < 0.5
    assert 0.2 < lit[2] < 0.8


def test_the_ground_plane_sits_inside_the_city():
    ground = MANIFEST["ground"]
    xyz = buffer("positions", "<i2").reshape(-1, 3)
    scale = MANIFEST["quantisation"]["extent_m"] / MANIFEST["quantisation"]["scale"]
    z = xyz[:, 2] * scale
    assert z.min() <= ground["z_m"] <= z.max()
    # A flat plane through a sloping city would bury buildings. Donji grad is flat.
    assert ground["z_m"] - z.min() < 5.0
