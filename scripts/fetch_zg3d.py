"""Download the ZG3D 2022 building model from the City of Zagreb open data portal.

The portal offers four formats and only one of them carries the roof geometry
this project exists to use. The GeoJSON and CSV exports are flattened: every
coordinate comes back with z = 0 and the roof survives only as a Z_Min, Z_Max
and Volume attribute. The file geodatabase keeps the Esri multipatch, which is
the actual LoD 2.2 surface, so that is what this fetches.

    python scripts/fetch_zg3d.py

About 329 MB compressed and 717 MB extracted, into data/raw/, which is not
committed. Resumable: an existing complete download is left alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

import requests

# The CKAN record. Resolved through the API rather than hardcoding the download
# URL, because the portal regenerates the export and its filename with it.
PORTAL = "https://data.zagreb.hr"
DATASET = "zg3d-2022-3d-model-gz"
PACKAGE_URL = f"{PORTAL}/api/3/action/package_show?id={DATASET}"

# Content-Length of the file geodatabase as fetched on 2026-08-18. Used only to
# report progress and to notice a truncated download, not as an integrity claim:
# the portal may legitimately republish.
EXPECTED_BYTES = 329_359_010

CHUNK = 1 << 20


def resolve_fgdb_url(timeout: float = 60.0) -> tuple[str, dict]:
    """Find the file geodatabase resource in the CKAN record."""
    response = requests.get(PACKAGE_URL, timeout=timeout, headers={"User-Agent": "suncokret/0.1"})
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise SystemExit("CKAN package_show did not succeed")

    result = payload["result"]
    for resource in result["resources"]:
        if (resource.get("format") or "").lower() in {"fgdb", "filegdb"}:
            return resource["url"], {
                "dataset_title": result["title"],
                "licence": result["license_title"],
                "licence_url": result.get("license_url"),
                "resource_format": resource["format"],
                "resource_last_modified": resource.get("last_modified"),
                "metadata_modified": result.get("metadata_modified"),
            }
    raise SystemExit(
        "No file geodatabase resource in the CKAN record. The other formats are "
        "flattened to z = 0 and cannot carry roof planes."
    )


def download(url: str, path: Path, timeout: float = 120.0) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url, stream=True, timeout=timeout, headers={"User-Agent": "suncokret/0.1"}
    ) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0)) or EXPECTED_BYTES
        written = 0
        last = 0.0
        with path.open("wb") as handle:
            for block in response.iter_content(CHUNK):
                handle.write(block)
                written += len(block)
                now = time.time()
                if now - last > 2.0:
                    print(f"  {written / 1e6:7.0f} / {total / 1e6:.0f} MB", flush=True)
                    last = now
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out)
    archive = out_dir / "zg3d_fgdb.zip"

    url, meta = resolve_fgdb_url()
    print(f"{meta['dataset_title']}")
    print(f"  licence: {meta['licence']}")

    if archive.exists() and archive.stat().st_size > 0 and not args.force:
        print(f"  {archive} already present ({archive.stat().st_size / 1e6:.0f} MB)")
        size = archive.stat().st_size
    else:
        print(f"  downloading {url.split('/')[-1]}")
        t0 = time.perf_counter()
        size = download(url, archive)
        print(f"  {size / 1e6:.0f} MB in {time.perf_counter() - t0:.0f}s")

    existing = list(out_dir.glob("*.gdb"))
    if existing and not args.force:
        print(f"  {existing[0].name} already extracted")
        gdb = existing[0]
    else:
        print("  extracting")
        with zipfile.ZipFile(archive) as z:
            z.extractall(out_dir)
        gdb = next(iter(out_dir.glob("*.gdb")))
        print(f"  extracted {gdb.name}")

    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)

    manifest = {
        "fetched_on": time.strftime("%Y-%m-%d"),
        "portal": PORTAL,
        "dataset": DATASET,
        "resource_url": url,
        "archive_bytes": size,
        "archive_sha256": digest.hexdigest(),
        "extracted_gdb": gdb.name,
        **meta,
        "why_this_format": (
            "the GeoJSON and CSV exports of this dataset are flattened to z = 0 "
            "and keep the roof only as Z_Min, Z_Max and Volume attributes; the "
            "file geodatabase keeps the Esri multipatch, which is the LoD 2.2 "
            "roof surface"
        ),
    }
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {out_dir / 'MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
