# progress

A running log, newest last. Dates are when the work happened, not when it was
tidied up.

## 2026-08-19, E0: the two measurements that decided the shape

Before any design, two numbers, because both of them change what gets built.

**How much of ZG3D carries usable roof geometry.** All 357,683 buildings swept,
not a sample, every one getting a row including the ones that fail. 99.23% have
a roof plane with a recoverable pitch and azimuth; 64.64% have enough roof for a
domestic install.

The survival rate turned out not to be the finding. The dataset is two datasets:
194,967 buildings (54.5%) from a 2008 aerial capture with a median of 6 faces
each, and 161,872 (45.3%) from the 2022 multisensor capture with a median of 72.
In the 2008 population 56% of buildings carry exactly one roof plane for the
whole roof. That is a per-record caveat, which made it a layout problem rather
than a README sentence, and it is why every drawing and every carpet in the app
hatches those buildings.

Three things cost real time and all three were worth it:

- The portal's GeoJSON and CSV exports are flattened to `z = 0`. The roof
  survives only as `Z_Min`, `Z_Max` and `Volume` attributes. Only the file
  geodatabase keeps the multipatch. Half a day would have gone into building on
  a footprint and calling it a roof model.
- A few hundred records are ISO WKB TIN, which shapely refuses. Skipping them
  would have been easy and would have meant measuring a population that had
  cleaned itself, so they get their type code rewritten instead, moving no
  coordinate.
- Orientation. Forcing normals upward turns every floor slab into a second roof.
  The obvious fix, the sign of the divergence-theorem volume, works on closed
  solids and 54% of ZG3D is open shells with no floor whose signed volume misses
  the dataset's own `Volume` by a median factor of ~15. Orientation now comes
  from the highest non-vertical face, which needs no closure, and agrees with
  the volume rule everywhere the volume rule can be checked.

**What inter-building shading costs, and whether it is worth computing.** 10 to
43 ms per building across four real districts. A single roof is therefore live
and a district is a precompute, which is exactly the architecture that shipped.

It is worth computing: in dense Donji grad the mean roof keeps 0.892 of its
direct beam, the worst tenth keep 0.733 or less, the worst keeps 0.486.

Travno went into the benchmark as a guess at the worst case, on the grounds that
it has the tallest residential slabs in the city. It came back the best at 0.994,
because a roof on top of the tallest thing around has nothing to shade it. The
guess was wrong and the measurement is left in.

## 2026-08-19, design

First mockup was rejected and the rejection was right. It was a stack of carpet
plots: it broke no rule, put none of the extracted geometry on screen, and could
have been about any city. It is kept at `docs/design/rejected-carpet-stack-mockup.html`
because the reason it failed is more useful than the fact that it did.

The second is an architect's shadow study, nine axonometric views of the same
blocks, three hours across and three dates down. Small multiples was unclaimed by
the earlier projects, the geometry is the picture, and the sweep is the ten
seconds. The carpet survives underneath as the summary, which is where it
belonged.

Ground moved from near-white to buff drafting stock so it does not read as the
same thumbnail as the first project in the set at a glance.

## 2026-08-19, build

Pipeline: fetch, survey, benchmark, then `build_district.py` writing a 2.5 MB
bundle the browser reads. The app is static; there is no server.

Things that went wrong and what they were:

- Nothing rendered. The fragment shader had an `#extension` directive after a
  `precision` line, which is invalid in the shading language three.js compiles
  to, and the whole program silently failed to link.
- Every drawing landed in the wrong place. three.js applies the device pixel
  ratio inside `setViewport`, and I was applying it again.
- Buildings rendered as hollow boxes with no roofs. ZG3D winds its rings inward,
  so backface culling was discarding most roof planes. The winding carries
  orientation information the pipeline uses, so the drawing renders both sides
  rather than rewriting it.
- The study was mostly walls. These blocks carry four times more wall area than
  roof area, so the camera had to become a steep plan oblique before the subject
  of the study was actually visible in it.
- Roofs were uniformly amber whatever the season. Lit is not a boolean for a
  reader: shading a lit plane by the cosine of incidence is both truer and the
  reason the nine frames differ from each other at all.

The first district was 700 m across, at which a roof plane is three pixels and
the study says nothing. It is now 260 m, which is a handful of Donji grad blocks
and legible, and the bundle got smaller as a side effect.

Verification: 60 behavioural tests, 23 integrity tests that check every number in
the README against the artefact that produced it, and 145 frontend tests of which
140 are the JavaScript solar position checked case by case against the Python
one, because two implementations of the same series in two languages is exactly
the arrangement that drifts silently.

Walkthrough is recorded off the real page by driving the real control, so there
is no staged scenario to go stale.

## 2026-09-09, the hero was weak

Review of the built page: the bottom half worked, the top half did not. The
study read as a small orange smudge in a large field of empty paper. Four
things were wrong and all four were mechanical.

Rows were 155 px on the reviewer's window, because the clamp was written in
`vh` and their viewport was short. They are now at least 250 px whatever the
window does.

The camera framed the site with a closed form that assumes a thirty degree
isometric. This camera is not one, so the frame came out about a quarter wider
than the drawing needed and a quarter of every cell was spent on nothing. The
extents are now taken by putting the real vertices through the camera's own
axes, the camera sits at about forty four degrees instead of fifty eight, and
the frame goes most of the way from fitting the site to filling the cell. A
square site in a cell twice as wide as it is tall cannot do both. The drawing
now covers roughly 85% of the width and the ends of the site leave the frame,
which is what a viewport onto a site is supposed to do.

There was no ground. Buildings floated. This was the item that decided whether
the drawing read as a city or as a stain, and it is back: a level plane at the
median building base with the shadows marched across it. The roofs get their
shading from horizon profiles, which is right for a point that wants a whole
year, but a point on the ground keeps every blocker in the district above it,
so a profile per ground cell would have cost longer than the rest of the build
put together. Marching a ray toward the sun over a rasterised height field
until it clears the tallest thing in the raster costs two seconds. Roof faces
are rasterised rather than sampled along their edges, because an edge cloud
casts the outline of a building and leaves a lit hole in the middle of its own
shadow.

ZG3D models buildings and not terrain, so that plane is an assumption. It is in
the manifest, in the colophon and in this paragraph.

Last, the roofs were amber whatever the incidence, because the ramp ran out of
headroom at 0.62 and most lit roofs are above that. It now has three stops, the
same three the approved mockup used, and the top one is a pale gold rather than
a saturated orange. A December flat roof at 0.36 and a June one at 0.93 are now
different colours. Most of the seasonal drama turned out to come from the
ground anyway: at noon the sun reaches 16% of it in December and 39% in June.

One thing was tried and thrown away. Outlining every extracted plane, the way
the mockup stroked every polygon, does help at three times the size and turns
the drawing into a wireframe at the size it is actually read at.

A correction while checking the site: the district note claimed the cathedral
was in frame. It is not. Its south wall stands about 45 m north of the top edge
of the square and nothing inside the site reaches 40 m above the ground. The
note now says what is actually there.

## 2026-09-10, the walkthrough was never a walkthrough

The checklist wants `docs/walkthrough/app-walkthrough.{mp4,gif,jpg}`, one
uninterrupted recording of the running application, and it says in the same
breath: no generated frames. This repository had `docs/walkthrough.gif`, a path
it invented for itself, holding eighteen screenshots stitched together at 320 ms
apiece. That is a slideshow. It broke the rule it was built to satisfy.

The integrity test asserted that the file existed. Nothing else. So the suite
reported green on a gate the repository was failing, while the two sibling
projects, which carry the real check by magic bytes at the real paths, sat
honestly blocked on exactly the same thing. A test that is easier than the rule
it stands for is worse than no test, because no test at least does not lie about
the state.

The GIF and the stitcher are gone. The test is now the one the siblings use:
either all three files are present and pass their magic bytes and their size
caps, or the README says the recording has not been made. Both branches were
checked by breaking them on purpose, once each.

The frame recorder stays. It already drives the real page through the real
control, which is the half a video recorder needs. The other half needs ffmpeg,
which is not installed on this machine, and installing it is not something this
session does.
