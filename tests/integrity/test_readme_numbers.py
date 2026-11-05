"""Numbers in the README against the committed files they come from."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SURVEY = ROOT / "data" / "survey"
MANIFEST = ROOT / "web" / "public" / "district" / "kaptol-donji-grad.json"
# On one line, so a number split across a line break still matches.
README = re.sub(r"\s+", " ", (ROOT / "README.md").read_text(encoding="utf-8"))


def artefact(name: str) -> dict:
    return json.loads((SURVEY / name).read_text(encoding="utf-8"))


def survey_numbers(s: dict) -> list[str]:
    cascade = s["cascade"]
    old = s["provenance"]["aerofotogrametrijsko snimanje (2008)"]
    new = s["provenance"]["Multisenzorsko snimanje (2022)"]
    closed = s["area_check_vs_zg3d_sarea_attribute"]["closed_solids"]
    compared = closed["all"]["buildings_compared"]
    over = round((1 - closed["all"]["within_1_percent"]) * compared)
    faces, modes = s["degenerate_faces"], s["failure_modes"]
    closure = s["closure_check_vs_zg3d_volume_attribute"]
    return [
        f"{cascade['surveyed']:,} swept in {round(s['timing']['wall_clock_s'])} s",
        *(
            text
            for key in ("has_roof_face", "has_pitched_roof_modelling", "pv_ready")
            for text in (f"{cascade[key]['count']:,}", f"{cascade[key]['fraction'] * 100:.2f}%")
        ),
        f"{old['buildings']:,}",
        f"{new['buildings']:,}",
        f"{old['share_of_dataset'] * 100:.1f}%",
        f"{new['share_of_dataset'] * 100:.1f}%",
        f"median of {int(old['median_faces_per_building'])} faces",
        f"median of {int(new['median_faces_per_building'])}",
        f"{old['one_roof_face_fraction'] * 100:.0f}% of",
        f"{closed['total']:,}",
        str(closed["excluded_sarea_not_positive"]),
        f"{compared:,}",
        f"{closed['all']['median_relative_error']:.1e}",
        f"{over:,}",
        f"{closed['sarea_below_volume_minimum']:,}",
        f"{faces['fraction_zero_area'] * 100:.1f}%",
        f"{faces['faces_total']:,}",
        f"{modes['wkb_needed_repair']['count']:,}",
        str(modes["orientation_undecidable"]["count"]),
        f"{closure['median_abs_ratio_open']:.1f}",
        f"{(1 - closure['closed_solid_fraction']) * 100:.0f}% of ZG3D is open shells",
    ]


def sun_numbers(s: dict) -> list[str]:
    raw = s["at_pvgis_timestamp"]
    return [
        f"{raw['hours_compared']:,}",
        f"RMS {raw['rms_deg']:.3f}",
        f"maximum {raw['max_abs_deg']:.3f}",
    ]


def shading_numbers(s: dict) -> list[str]:
    envelope = s["cost_envelope"]
    donji = {d["district"]: d for d in s["districts"]}["donji-grad"]
    box = donji["box_at_ridge_height_error"]
    samples = {5: "five"}[s["method"]["samples_per_plane"]]
    texts = [
        f"with {samples} sample points",
        f"{envelope['per_building_ms_min']:.1f} to {envelope['per_building_ms_max']:.1f} ms",
        f"{donji['beam_kept_fraction']['all']['p10']:.3f}",
        f"{box['mean_delta']:.3f}",
        f"{box['max_abs_delta']:.3f}",
        f"{box['buildings_over_1pct']:,} of {donji['buildings_evaluated']:,}",
    ]
    for d in s["districts"]:
        beam = d["beam_kept_fraction"]["all"]
        texts += [f"{beam['mean']:.3f}", f"{beam['worst']:.3f}"]
    for sens in s["sensitivity"]:
        res = {r["height_field_m"]: r["beam_kept"] for r in sens["height_field_resolution"]}
        rad = {r["radius_m"]: r["beam_kept_vs_500m"] for r in sens["search_radius"]}
        texts += [
            f"{sens['buildings_sampled']:,}",
            f"{res[1.0]['mean_abs_error']:.4f}",
            f"{res[5.0]['mean_abs_error']:.3f}",
            f"{res[1.0]['mean_abs_error_pitched']:.4f}",
            f"{res[1.0]['mean_abs_error_flat']:.4f}",
            f"{rad[250.0]['mean_abs_error']:.4f}",
        ]
    return texts


def district_numbers(d: dict) -> list[str]:
    penalty, kept = d["single_sample_penalty"], d["kept_distribution"]
    diffuse = d["diffuse_shading_effect"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [
        f"{penalty['planes_checked']} of the {penalty['planes_in_bundle']}",
        f"{penalty['kept_mean_abs_difference']:.4f}",
        f"{penalty['kept_max_abs_difference']:.3f}",
        f"{kept['mean']:.3f}",
        f"{kept['median']:.3f}",
        f"{diffuse['diffuse_share_of_global_horizontal'] * 100:.0f}%",
        f"{diffuse['mean_overstatement_worst_tenth']:.3f}",
        f"{d['selectable_planes_on_single_plane_buildings']} of the",
        # radius_m is a Chebyshev half-width, so the square is twice it.
        f"{int(manifest['radius_m']) * 2} m square",
    ]


def crosscheck_numbers(c: dict) -> list[str]:
    return [
        f"RMS {c['beam_kept_rms_difference']:.3f}",
        f"{c['beam_kept_max_abs_difference']:.3f}",
        f"{c['resampling_effect_max_abs']:.3f}",
        f"{abs(c['poa_open_mean_relative_difference']) * 100:.1f}%",
        f"{abs(c['poa_shaded_mean_relative_difference']) * 100:.1f}%",
    ]


QUOTED = {
    "roof_survey.json": survey_numbers,
    "sun_validation.json": sun_numbers,
    "shading_benchmark.json": shading_numbers,
    "district_build.json": district_numbers,
    "pvgis_crosscheck.json": crosscheck_numbers,
}


@pytest.mark.parametrize("name", QUOTED)
def test_the_readme_quotes_the_artefact(name):
    absent = [text for text in QUOTED[name](artefact(name)) if text not in README]
    assert not absent, f"{name} says these and the README does not: {absent}"


def test_the_reference_case_count_is_the_file():
    cases = json.loads((ROOT / "web" / "tests" / "sun-reference.json").read_text(encoding="utf-8"))
    assert f"{len(cases['cases'])} reference cases" in README


def test_what_the_readme_says_in_words_still_holds():
    survey, sun = artefact("roof_survey.json"), artefact("sun_validation.json")
    closed = survey["area_check_vs_zg3d_sarea_attribute"]["closed_solids"]
    old = survey["provenance"]["aerofotogrametrijsko snimanje (2008)"]
    # The tilt-cut sensitivity uses the pv_ready rule with only the cut moved.
    cuts = survey["tilt_cut_sensitivity_pv_ready"]
    assert cuts["cut_60deg"] == survey["cascade"]["pv_ready"]["count"]
    # The 2008 survey is the majority of the city, and open shells the majority of the dataset.
    assert old["share_of_dataset"] > 0.5
    assert 1 - survey["closure_check_vs_zg3d_volume_attribute"]["closed_solid_fraction"] > 0.5
    # Every closed solid that disagrees by more than 1% is accounted for.
    assert (
        closed["total"]
        == closed["all"]["buildings_compared"] + (closed["excluded_sarea_not_positive"])
    )
    assert sun["at_pvgis_timestamp"]["offset_minutes"] == 0, "the README says no offset was fitted"
    assert artefact("pvgis_crosscheck.json")["planes"] == 20
    for sens in artefact("shading_benchmark.json")["sensitivity"]:
        assert sens["buildings_sampled"] >= 500
    counts = artefact("district_build.json")["counts"]
    assert counts["selectable_planes"] > 0
    assert counts["simplified_buildings"] > 0
