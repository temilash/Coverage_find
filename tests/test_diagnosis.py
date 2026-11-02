"""The rule-based diagnosis on hand-made evidence: scores, caps and wording."""

from coverage_detective import diagnosis as D


def item(id, value=None, tests=None, strength="moderate", source="x", **size):
    return {"id": id, "value": value, "strength": strength, "source": source,
            "sample_size": {**({"tests": tests} if tests is not None else {}), **size}}


def bundle(*items, likely_best=None):
    return {"items": list(items), "likely_best": likely_best or {}, "limitations": ["a fixed limitation"]}


def los(op, cls, strength="strong"):
    return item(f"los_{op}", {"los_class": cls, "blocked": cls == "blocked"}, strength=strength)


def test_terrain_scores_by_strength_and_clear_lines_lower_it():
    d = D.diagnose(bundle(los("ee", "blocked"), los("o2", "blocked")))
    assert d["causes"][0]["cause"] == "terrain_blocking" and d["causes"][0]["score"] == 0.7
    d = D.diagnose(bundle(los("ee", "blocked"), los("o2", "clear")))
    assert d["causes"][0]["score"] == 0.5  # as many clear lines as blocked ones


def test_weak_only_support_is_capped():
    d = D.diagnose(bundle(item("nearest_site", {"distance_km": 7.0}, strength="moderate"),
                          item("speed_cell", {"download_mbps": 3.0}, tests=40)))
    far = next(c for c in d["causes"] if c["cause"] == "distance_to_mast")
    assert far["score"] == 0.5 and not far["weak_only"]
    assert D.weak_reason(item("speed_cell", {"download_mbps": 3.0}, tests=12)) == "12 tests"
    assert D.weak_reason(item("operators_5km", {"operators": ["EE"], "operators_confirmed": []},
                              source="Approximate mast sites (CD_MASTS_SITES)")) == "crowd-sourced sites only"


def test_service_that_measures_well_caps_every_cause():
    d = D.diagnose(bundle(los("ee", "blocked"),
                          item("speed_cell", {"download_mbps": 120.0, "uk_median_download_mbps": 100.0}, tests=80)))
    assert d["headline"] == "No strong cause was found; the service measures well here."
    assert all(c["score"] <= D.MEASURES_WELL_CAP for c in d["causes"])


def test_single_operator_wording_follows_the_mast_rules():
    d = D.diagnose(bundle(item("nearest_site", {"distance_km": 1.0}),
                          item("operators_5km", {"operators": ["Vodafone"], "operators_confirmed": ["Vodafone"]},
                               sites_unknown_operator=2)))
    c = next(c for c in d["causes"] if c["cause"] == "single_operator")
    assert c["explanation"] == ("Only 1 operator has approximate mast sites within 5 km; operators could not be "
                                "identified for 2 nearby sites.")


def test_no_measurements_and_no_sites_say_not_enough_data():
    d = D.diagnose(bundle(item("speed_cell", None, tests=0, strength="weak"),
                          item("speed_1km", None, tests=0, strength="weak"),
                          item("nearest_site", None, strength="weak")))
    assert d["headline"].startswith("Not enough data to say why: there are no measured speeds")
    assert any(line.startswith("Operators could not be identified") for line in d["limitations"])


def test_calm_weather_and_no_floods_are_ruled_out():
    d = D.diagnose(bundle(item("weather", {"severity": "none"}), item("flood_warnings", {"warnings": []})))
    assert [r["cause"] for r in d["ruled_out"]] == ["weather", "active_incident"]
    assert D.RESIDENT_ACTIONS[0].startswith("Check your address on Ofcom's mobile coverage checker")
    assert d["limitations"][0] == D.METHOD and "a fixed limitation" in d["limitations"]
