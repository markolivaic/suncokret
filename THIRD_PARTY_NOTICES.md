# Third party data and software

Everything this project reads comes from someone else. This file says who, under
what licence, and exactly what was done to it.

## Data

### ZG3D 2022, 3D model of the City of Zagreb

- **Creator:** Grad Zagreb (City of Zagreb)
- **Source:** https://data.zagreb.hr/dataset/zg3d-2022-3d-model-gz
- **Licence:** Otvorena dozvola (Open Licence), http://data.gov.hr/otvorena-dozvola
- **Local path:** `data/raw/*.gdb`, not committed. `scripts/fetch_zg3d.py`
  rebuilds it, about 329 MB compressed.
- **Retrieved:** see `data/raw/MANIFEST.json`, written by the fetch script with
  the resource URL and a SHA-256 of the archive.

**Which format, and why it matters.** The portal offers the same dataset as
GeoJSON, CSV, XLSX and a file geodatabase. The first three are flattened: every
coordinate comes back with `z = 0` and the roof survives only as the `Z_Min`,
`Z_Max` and `Volume` attributes. Only the file geodatabase keeps the Esri
multipatch, which is the actual roof surface. This project reads the file
geodatabase.

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

**One thing to be clear about.** PVGIS applies a far horizon computed from a
terrain DEM (`use_horizon: true`). That is terrain only. Buildings are not in
it. The inter-building shading in this project is computed separately from ZG3D
and is not part of what PVGIS supplies.

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

## What the derived numbers are not

The roof planes, tilts, azimuths, shading fractions and sky view factors in this
repository are computed from a city model. They are not measurements of any
roof, and nobody has been up a ladder to check one.

There is no ground truth available for any of it. Per-roof generation is not
published for Zagreb by anyone, so no figure here has been compared against a
real installation's output. Comparisons between two roofs in this model are
meaningful because both sides of the comparison come from the same pipeline. An
absolute annual yield in kilowatt hours is not, and this project does not
present one as if it were.

The building model itself is not uniform. Slightly over half of it was captured
in 2008 and carries a single roof plane per building, which stands in for
whatever the real roof does. `data/survey/roof_survey.json` reports that split
and `scripts/survey_roofs.py` reproduces it.
