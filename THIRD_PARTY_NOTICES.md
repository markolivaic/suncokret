# Third party data and software

Who made each dataset and package this project uses, under what licence, and
what was done to it.

## Data

### ZG3D 2022, 3D model of the City of Zagreb

- **Creator:** Grad Zagreb (City of Zagreb)
- **Source:** https://data.zagreb.hr/dataset/zg3d-2022-3d-model-gz
- **Licence:** Otvorena dozvola (Open Licence), http://data.gov.hr/otvorena-dozvola
- **Local path:** `data/raw/*.gdb`, not committed. `scripts/fetch_zg3d.py`
  rebuilds it, about 329 MB compressed.
- **Retrieved:** see `data/raw/MANIFEST.json`, written by the fetch script with
  the resource URL and a SHA-256 of the archive.

**Format.** The portal offers the dataset as GeoJSON, CSV, XLSX and a file
geodatabase. The first three come back with `z = 0` on every coordinate, with
the roof only in the `Z_Min`, `Z_Max` and `Volume` attributes. This project
reads the file geodatabase, which keeps the Esri multipatch surfaces.

**Transformations applied.** Read through GDAL's OpenFileGDB driver as
`MultiPolygon Z`; a small number of records arrive as ISO WKB TIN and have their
geometry type code rewritten to the structurally identical MultiPolygon, moving
no coordinate (`backend/suncokret/wkb.py`). Coordinates are reprojected from
EPSG:4326 to EPSG:3765 (HTRS96 / Croatia TM) so that areas and angles are in
metres. Each planar ring is reduced to an outward normal, a 3D area, a tilt and
an azimuth (`backend/suncokret/roofs.py`). Nothing is added to the geometry and
no missing roof is inferred.

### PVGIS hourly irradiance

- **Creator:** European Commission, Joint Research Centre
- **Source:** https://re.jrc.ec.europa.eu/api/v5_3/seriescalc
- **Licence / reuse:** PVGIS data may be reused with attribution; see
  https://joint-research-centre.ec.europa.eu/pvgis-online-tool_en
- **Local path:** `data/pvgis/zagreb-2020.json`, committed, about 1.5 MB.
- **Retrieved:** see `data/pvgis/MANIFEST.json`.

**Transformations applied.** One request for a horizontal plane at the city
centre, with `components=1` so beam, diffuse and reflected arrive separately.
Nothing is smoothed, gap-filled or rescaled. Every roof plane's irradiance is
derived from these components by this project's own code rather than by a
further request to PVGIS.

PVGIS applies a far horizon computed from a terrain model (`use_horizon:
true`), which has no buildings in it. The shading by buildings is computed here
from ZG3D.

`scripts/crosscheck_pvgis.py` also sends 20 horizon profiles from the bundle to
the same service, with and without each horizon, and stores the totals it gets
back in `data/survey/pvgis_crosscheck.json`.

### OpenStreetMap

- **Creator:** OpenStreetMap contributors
- **Licence:** Open Database License (ODbL) 1.0, https://www.openstreetmap.org/copyright
- **Used:** named streets around the district, fetched once through the
  Overpass API by `scripts/fetch_osm_streets.py` and committed as
  `data/osm/streets.json`, with the query and the OSM data timestamp in the
  file. `scripts/build_district.py` stores each selectable roof plane's nearest
  street and its distance in the bundle; that part of the bundle is a derived
  database under the same licence. The coordinates of Trg bana Jelačića and
  Zrinjevac, looked up with Nominatim, are in `scripts/build_district.py`.

## Software

| Package | Licence |
|---|---|
| NumPy | BSD-3-Clause |
| Shapely | BSD-3-Clause |
| pyogrio | MIT (wheels bundle GDAL, MIT/X style) |
| pyproj | MIT (wheels bundle PROJ, MIT) |
| requests | Apache-2.0 |
| pytest | MIT |
| ruff | MIT |
| three.js | MIT |
| Vite | MIT |
| Vitest | MIT |
| Playwright | Apache-2.0 |
| Archivo (font, via Fontsource) | SIL Open Font License 1.1 |
| Spline Sans Mono (font, via Fontsource) | SIL Open Font License 1.1 |

## What the derived numbers are

Roof planes, tilts, azimuths, shading shares and sky view factors here are
computed from the city model, not measured on any roof, and no per-roof
generation exists for Zagreb to compare them with. 54.5% of the buildings come
from the 2008 capture, and 56% of those carry a single roof face for the whole
roof; `data/survey/roof_survey.json` reports the split.
