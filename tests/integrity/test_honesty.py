"""Guards on what the repository claims about itself.

Two kinds of check live here. The first is that every number printed in the
README still equals the artefact that produced it, so a figure cannot rot into a
hand-typed literal after the script that made it changes. The second is that the
disclosures are still present, still above the fold, and still in the running
product rather than only in the documentation.

Prose is matched against whitespace-collapsed text. A guard that matches the raw
file passes or fails on where a line happens to wrap, which means it silently
stops guarding anything the first time someone reflows a paragraph.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
INDEX = ROOT / "web" / "index.html"
SURVEY = ROOT / "data" / "survey" / "roof_survey.json"
SUN = ROOT / "data" / "survey" / "sun_validation.json"
SHADING = ROOT / "data" / "survey" / "shading_benchmark.json"
DISTRICT = ROOT / "data" / "survey" / "district_build.json"


def collapse(text: str) -> str:
    """Whitespace-collapsed text, so a guard survives a reflowed paragraph."""
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def readme() -> str:
    return collapse(README.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def survey() -> dict:
    return json.loads(SURVEY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sun() -> dict:
    return json.loads(SUN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shading() -> dict:
    return json.loads(SHADING.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def district() -> dict:
    return json.loads(DISTRICT.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# the disclosures
# --------------------------------------------------------------------------


def test_the_no_model_claim_is_in_the_first_part_of_the_readme(readme):
    """It is the single sentence a reader must not be able to miss."""
    claim = "No model is trained and none runs."
    assert claim in readme
    assert readme.index(claim) < len(readme) * 0.25


def test_what_this_is_not_comes_before_the_walkthrough(readme):
    """A caveat below the fold is a caveat nobody reads."""
    assert readme.index("## What this is not") < readme.index("## Walkthrough")


def test_the_readme_refuses_to_promise_electricity(readme):
    assert "It does not tell you how much electricity your roof would make." in readme
    assert "There is no ground truth for any roof in Zagreb." in readme


def test_the_running_product_carries_the_disclosure_too(readme):
    """Section 6.1: the label has to be visible in the app, not only in docs."""
    page = collapse(INDEX.read_text(encoding="utf-8"))
    assert "No model is trained and none runs." in page
    assert "There is no ground truth" in page
    assert "never shows one" in page


def test_the_provenance_caveat_is_in_the_running_product(readme):
    """It varies building by building, so it cannot live in the README alone."""
    page = collapse(INDEX.read_text(encoding="utf-8"))
    assert "one roof plane for the whole building" in page
    assert "hatched" in page


def test_third_party_notices_name_every_source(readme):
    notices = collapse(NOTICES.read_text(encoding="utf-8"))
    assert "ZG3D 2022" in notices
    assert "Otvorena dozvola" in notices
    assert "PVGIS" in notices
    assert "Joint Research Centre" in notices
    # And the readme has to point at it rather than restating it badly.
    assert "THIRD_PARTY_NOTICES.md" in readme


# --------------------------------------------------------------------------
# every number in the readme, against the artefact that produced it
# --------------------------------------------------------------------------


def test_survey_headline_counts(readme, survey):
    cascade = survey["cascade"]
    assert f"{cascade['surveyed']:,}" in readme
    for key, label in (
        ("pitch_and_azimuth_recoverable", "354,927"),
        ("has_pitched_roof_modelling", "257,792"),
        ("pv_ready", "231,218"),
    ):
        count = cascade[key]["count"]
        assert f"{count:,}" == label, f"{key} changed, update the README"
        assert f"{count:,}" in readme
        assert f"{cascade[key]['fraction'] * 100:.2f}%" in readme


def test_provenance_split(readme, survey):
    provenance = survey["provenance"]
    old = provenance["aerofotogrametrijsko snimanje (2008)"]
    new = provenance["Multisenzorsko snimanje (2022)"]

    assert f"{old['buildings']:,}" in readme
    assert f"{new['buildings']:,}" in readme
    assert f"{old['share_of_dataset'] * 100:.1f}%" in readme
    assert f"{new['share_of_dataset'] * 100:.1f}%" in readme
    assert f"median of {int(old['median_faces_per_building'])} faces" in readme
    assert f"median of {int(new['median_faces_per_building'])}" in readme

    # The claim that this is the majority of the city has to stay true.
    assert old["share_of_dataset"] > 0.5


def test_area_check_against_the_dataset_attribute(readme, survey):
    check = survey["area_check_vs_zg3d_sarea_attribute"]["closed_solids"]
    assert f"{check['buildings_compared']:,}" in readme
    assert "2.8e-06" in readme
    assert check["median_relative_error"] == pytest.approx(2.8e-06, abs=5e-08)
    assert f"{check['within_1_percent'] * 100:.1f}%" in readme


def test_degenerate_and_failure_counts(readme, survey):
    faces = survey["degenerate_faces"]
    assert f"{faces['fraction_zero_area'] * 100:.1f}%" in readme
    assert f"{faces['faces_total']:,}" in readme

    modes = survey["failure_modes"]
    assert f"{modes['wkb_needed_repair']['count']:,}" in readme
    assert str(modes["orientation_undecidable"]["count"]) in readme


def test_open_shell_ratio_backs_the_orientation_argument(readme, survey):
    closure = survey["closure_check_vs_zg3d_volume_attribute"]
    assert f"{closure['median_abs_ratio_open']:.1f}" in readme
    # The README says the volume rule fails on most of the dataset.
    assert 1 - closure["closed_solid_fraction"] > 0.5


def test_sun_validation_figures(readme, sun):
    raw = sun["at_pvgis_timestamp"]
    assert f"{raw['hours_compared']:,}" in readme
    assert f"RMS {raw['rms_deg']:.3f}" in readme
    assert f"maximum {raw['max_abs_deg']:.3f}" in readme
    assert raw["offset_minutes"] == 0, "README says no offset was fitted"


def test_shading_cost_envelope(readme, shading):
    envelope = shading["cost_envelope"]
    span = f"{envelope['per_building_ms_min']:.1f} to {envelope['per_building_ms_max']:.1f} ms"
    assert span in readme


def test_shading_effect_per_district(readme, shading):
    by_name = {d["district"]: d for d in shading["districts"]}

    donji = by_name["donji-grad"]["beam_kept_fraction"]
    assert f"{donji['mean']:.3f}" in readme
    assert f"{donji['p10']:.3f}" in readme
    assert f"{donji['worst']:.3f}" in readme

    sesvete = by_name["sesvete"]["beam_kept_fraction"]
    assert f"mean {sesvete['mean']:.3f}, worst {sesvete['worst']:.3f}" in readme

    travno = by_name["novi-zagreb-travno"]["beam_kept_fraction"]
    assert f"{travno['mean']:.3f}" in readme
    # The surprise the README reports has to still be a surprise.
    assert travno["mean"] > donji["mean"]


def test_approximation_sensitivities(readme, shading):
    resolution = {
        r["height_field_m"]: r["beam_kept_mean_abs_error"]
        for r in shading["sensitivity"]["height_field_resolution"]
    }
    assert f"{resolution[1.0]:.3f}" in readme
    assert f"{resolution[5.0]:.3f}" in readme

    radius = {
        r["radius_m"]: r["beam_kept_mean_abs_error_vs_500m"]
        for r in shading["sensitivity"]["search_radius"]
    }
    assert f"{radius[250.0]:.4f}" in readme


def test_single_sample_penalty_is_reported(readme, district):
    penalty = district["single_sample_penalty"]
    assert f"{penalty['kept_mean_difference']:.4f}" in readme
    assert f"{penalty['kept_max_difference']:.3f}" in readme
    # If this ever grows, the README sentence stops being true.
    assert abs(penalty["kept_mean_difference"]) < 0.005


def test_district_size_claim(readme, district):
    counts = district["counts"]
    assert counts["selectable_planes"] > 0
    assert counts["simplified_buildings"] > 0
    manifest = json.loads(
        (ROOT / "web" / "public" / "district" / "kaptol-donji-grad.json").read_text(
            encoding="utf-8"
        )
    )
    # radius_m is a Chebyshev half-width, so the square is twice it.
    assert f"{int(manifest['radius_m']) * 2} m square" in readme


# --------------------------------------------------------------------------
# the shape of the documentation itself
# --------------------------------------------------------------------------


def test_no_local_path_leaked_into_the_docs():
    for path in (README, NOTICES):
        text = path.read_text(encoding="utf-8")
        assert "C:\\Users" not in text
        assert "/home/" not in text
        assert "makif" not in text


def test_badges_point_at_a_workflow_and_are_not_hand_typed():
    text = README.read_text(encoding="utf-8")
    badges = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    for url in badges:
        if "badge" in url or "shields.io" in url:
            assert "/actions/workflows/" in url, (
                f"{url} is not a live workflow badge; a literal is not evidence"
            )


WALKTHROUGH = ROOT / "docs" / "walkthrough"
WALKTHROUGH_FILES = {
    "app-walkthrough.mp4": (4, 8, b"ftyp"),
    "app-walkthrough.gif": (0, 6, b"GIF89a"),
    "app-walkthrough-poster.jpg": (0, 3, bytes([0xFF, 0xD8, 0xFF])),
}


def test_the_walkthrough_is_either_present_and_real_or_declared_missing(readme):
    """Having no recording is allowed. Papering over it is not.

    The first version of this checked that one file existed at a path this
    project invented for itself. It passed while the repository carried a GIF
    stitched out of eighteen screenshots, which the checklist rejects outright,
    and it passed while the two sibling repositories sat honestly blocked on the
    same gate. A test that is easier than the rule it stands for is worse than
    no test, because it reports green.
    """
    present = {
        name: WALKTHROUGH / name for name in WALKTHROUGH_FILES if (WALKTHROUGH / name).exists()
    }
    if not present:
        assert re.search(r"not recorded", readme, re.I), (
            "with no walkthrough committed, the README must say it is not recorded"
        )
        return

    missing = set(WALKTHROUGH_FILES) - set(present)
    assert not missing, f"walkthrough is partly committed, missing: {sorted(missing)}"

    for name, path in present.items():
        start, stop, magic = WALKTHROUGH_FILES[name]
        assert path.read_bytes()[start:stop] == magic, (
            f"{name} is not the format its extension claims"
        )

    assert (WALKTHROUGH / "app-walkthrough.gif").stat().st_size < 8 * 1024 * 1024
    assert (WALKTHROUGH / "app-walkthrough.mp4").stat().st_size < 5 * 1024 * 1024
    assert not re.search(r"not recorded", readme, re.I), (
        "the walkthrough is committed, so the README should stop saying it is missing"
    )
    assert "No screen is mocked." in readme


BANNED = [
    "robust",
    "seamless",
    "powerful",
    "leverage",
    "delve",
    "comprehensive",
    "cutting-edge",
    "unlock",
]


def test_no_marketing_vocabulary(readme):
    lowered = readme.lower()
    for word in BANNED:
        assert word not in lowered, f"{word!r} is on the banned list"


def test_no_em_dashes_anywhere_in_the_docs():
    for path in (README, NOTICES):
        text = path.read_text(encoding="utf-8")
        for char in ("\u2014", "\u2013", "\u00b7"):
            assert char not in text, f"{char!r} in {path.name}"


def test_the_readme_test_counts_are_real(readme):
    """A count in a README is a claim, and claims here are checked.

    The previous project this one is a reaction to shipped a tests-50-passing
    badge that was a hand-typed literal with no CI behind it.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    # pytest -q --collect-only prints one "<path>: <count>" line per file.
    collected = sum(
        int(line.rsplit(":", 1)[1])
        for line in result.stdout.splitlines()
        if re.fullmatch(r"\S+\.py:\s*\d+", line.strip())
    )
    assert collected > 0, result.stdout
    assert f"**{collected} behavioural**" in readme, (
        f"README claims a different count; pytest collects {collected}"
    )

    frontend = json.loads(
        (ROOT / "web" / "tests" / "sun-reference.json").read_text(encoding="utf-8")
    )
    # 140 generated cases plus the five hand-written ones in the same file.
    assert f"**{len(frontend['cases']) + 5} frontend**" in readme


def test_the_committed_bundle_is_internally_consistent():
    """A roof that loses sky cannot also keep all of its irradiance.

    This invariant was violated for 327 of 777 planes at one point, and the
    interface ranked every one of them above roofs that really were open. The
    cause was a NaN azimuth on horizontal planes meeting a guard that read a
    non-finite denominator as "nothing was lost".
    """
    import numpy as np

    manifest = json.loads(
        (ROOT / "web" / "public" / "district" / "kaptol-donji-grad.json").read_text(
            encoding="utf-8"
        )
    )
    blob = (ROOT / "web" / "public" / "district" / manifest["bin"]).read_bytes()

    def buffer(name, dtype):
        spec = manifest["buffers"][name]
        return np.frombuffer(blob, dtype=dtype, count=spec["count"], offset=spec["offset"])

    kept = buffer("selectableKept", "<f4")
    beam_kept = buffer("selectableBeamKept", "<f4")
    sky_kept = buffer("selectableSkyKept", "<f4")

    assert np.isfinite(kept).all(), "a non-finite kept fraction reached the bundle"
    assert np.isfinite(beam_kept).all()
    assert np.isfinite(sky_kept).all()
    assert ((kept > 0) & (kept <= 1)).all()

    losing_sky = sky_kept < 0.99
    assert losing_sky.any(), "no shaded planes at all, so this guards nothing"
    assert not (losing_sky & (kept >= 0.999)).any(), (
        "a plane loses part of its sky and still claims to keep everything"
    )


def test_the_ground_shadows_move_with_the_season():
    """The three rows claim a December, a March and a June.

    The drawing's strongest element is the shadow on the ground, and a reader
    reads the seasons off it before reading anything else. So the bundle has to
    actually carry three different days: less of the ground reaches the sun in
    December than in March, and less in March than in June.
    """
    import numpy as np

    manifest = json.loads(
        (ROOT / "web" / "public" / "district" / "kaptol-donji-grad.json").read_text(
            encoding="utf-8"
        )
    )
    blob = (ROOT / "web" / "public" / "district" / manifest["bin"]).read_bytes()
    ground = manifest["ground"]
    sweep = manifest["sweep"]

    spec = manifest["buffers"]["groundStates"]
    states = np.frombuffer(blob, dtype=np.uint8, count=spec["count"], offset=spec["offset"])
    assert spec["count"] == len(sweep["dates"]) * sweep["steps"] * ground["bytes_per_state"]
    states = states.reshape(len(sweep["dates"]), sweep["steps"], ground["bytes_per_state"])

    cells = ground["cells"] * ground["cells"]
    noon = round((12.0 - sweep["start_hour"]) * 60 / sweep["step_minutes"])
    lit = [
        np.unpackbits(states[d, noon], bitorder="little")[:cells].mean()
        for d in range(len(sweep["dates"]))
    ]

    assert sweep["dates"] == ["21 December", "21 March", "21 June"]
    assert lit[0] < lit[1] < lit[2], f"noon lit fractions do not increase with the season: {lit}"
    # Neither a blank sheet nor a solid shadow: both would mean the march found
    # nothing and the drawing is decoration.
    assert 0.02 < lit[0] < 0.5
    assert 0.2 < lit[2] < 0.8

    # And the plane the shadows fall on is inside the city it belongs to.
    positions = manifest["buffers"]["positions"]
    xyz = np.frombuffer(
        blob, dtype="<i2", count=positions["count"], offset=positions["offset"]
    ).reshape(-1, 3)
    scale = manifest["quantisation"]["extent_m"] / manifest["quantisation"]["scale"]
    z = xyz[:, 2] * scale
    assert z.min() <= ground["z_m"] <= z.max()
    # A flat plane through a sloping city would bury buildings. Donji grad is
    # flat, and this is the number that says by how much.
    assert ground["z_m"] - z.min() < 5.0
