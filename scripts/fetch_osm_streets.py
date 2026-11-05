"""Fetch the named streets around the district from OpenStreetMap and commit them.

The page names a selected roof by its nearest street. One Overpass query for
named roads and pedestrian ways around the district centre is enough, and the
result is committed so the build does not depend on the live service.

    python scripts/fetch_osm_streets.py

Writes data/osm/streets.json. The data is OpenStreetMap's, under the Open
Database License; see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import requests

API = "https://overpass-api.de/api/interpreter"
CENTRE = (45.8122, 15.9787)
HALF_SIDE_M = 250.0
# Roads a building can be addressed on. Footways, steps and cycleways are left
# out because they run through courtyards and parks as often as along fronts.
HIGHWAYS = "primary|secondary|tertiary|unclassified|residential|living_street|pedestrian|service"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/osm/streets.json")
    args = ap.parse_args()

    lat, lon = CENTRE
    dlat = HALF_SIDE_M / 110_540.0
    dlon = HALF_SIDE_M / (111_320.0 * math.cos(math.radians(lat)))
    bbox = (lat - dlat, lon - dlon, lat + dlat, lon + dlon)
    query = (
        "[out:json][timeout:60];"
        f'way["highway"~"^({HIGHWAYS})$"]["name"]'
        f"({bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f});"
        "out tags geom;"
    )
    response = requests.post(
        API, data={"data": query}, headers={"User-Agent": "suncokret/0.1"}, timeout=120
    )
    response.raise_for_status()
    payload = response.json()

    streets = [
        {
            "osm_way": int(el["id"]),
            "name": el["tags"]["name"],
            "highway": el["tags"]["highway"],
            "lonlat": [[round(p["lon"], 7), round(p["lat"], 7)] for p in el["geometry"]],
        }
        for el in payload["elements"]
        if el.get("type") == "way" and el.get("geometry")
    ]
    out = {
        "source": "OpenStreetMap contributors, Open Database License 1.0",
        "endpoint": API,
        "query": query,
        "fetched_on": time.strftime("%Y-%m-%d"),
        "osm_base": payload.get("osm3s", {}).get("timestamp_osm_base"),
        "streets": sorted(streets, key=lambda s: (s["name"], s["osm_way"])),
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    names = sorted({s["name"] for s in streets})
    print(f"{len(streets)} ways, {len(names)} names -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
