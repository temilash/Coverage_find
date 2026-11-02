import pandas as pd
import pytest

from coverage_detective.data.masts import bearing_deg, masts_per_cell, merge_sites, merge_wtr
from coverage_detective.sources.opencellid import cluster_sites
from coverage_detective.sources.osm_masts import is_comms_mast, operator_groups


def _cells(rows):
    return pd.DataFrame(rows, columns=["radio", "label", "operator_group", "lat", "lon", "samples"])


def test_cells_within_150m_form_one_site():
    # Two sectors ~50 m apart, a third ~1 km away.
    cells = _cells(
        [
            ("LTE", "EE", "EE", 54.4450, -3.1050, 10),
            ("NR", "O2 (UK)", "O2", 54.4454, -3.1050, 5),
            ("LTE", "JT", "other", 54.4540, -3.1050, 3),
        ]
    )
    sites, site_of = cluster_sites(cells)
    assert len(sites) == 2
    assert site_of[0] == site_of[1] != site_of[2]
    big = sites[sites.cell_count == 2].iloc[0]
    assert big.operators == "EE,O2" and big.operator_labels == "EE,O2 (UK)" and big.radio_types == "LTE,NR"
    # "other" networks keep their label but are not one of the four operators.
    small = sites[sites.cell_count == 1].iloc[0]
    assert small.operators == "" and small.operator_labels == "JT"


def test_no_chaining_along_a_line():
    # Cells every 100 m along a line: single-linkage would make one 1 km "site".
    cells = _cells([("LTE", "EE", "EE", 54.0 + i * 0.0009, -3.0, 1) for i in range(11)])
    sites, _ = cluster_sites(cells)
    assert len(sites) >= 4


def test_merge_sites_matches_within_200m():
    ocid = pd.DataFrame(
        {
            "lat": [54.0000, 54.0500], "lon": [-3.0000, -3.0000],
            "operators": ["EE", "O2"], "operator_labels": ["EE", "O2 (UK)"],
            "radio_types": ["LTE", "NR"], "cell_count": [3, 2],
        }
    )
    osm = pd.DataFrame(
        {
            "osm_id": ["node/1", "node/2"], "lat": [54.0010, 54.3000], "lon": [-3.0000, -3.0000],  # ~110 m; far
            "osm_operator": ["MBNL", None], "operators": ["EE,Three", ""], "mobile_tagged": [True, False],
        }
    )
    sites = merge_sites(ocid, osm).set_index("sources")
    assert sorted(sites.index) == ["opencellid", "opencellid,osm", "osm"]
    both = sites.loc["opencellid,osm"]
    assert both.lat == pytest.approx(54.0010)  # OSM position wins
    assert both.operators == "EE,Three" and both.operators_inferred == "EE,Three" and both.operators_confirmed == ""
    assert both.cell_count == 3


def _wtr(rows):
    return pd.DataFrame(rows, columns=["lat", "lon", "operators", "licensees", "links", "has_microwave_backhaul",
                                       "antenna_height_m"])


def test_merge_wtr_confirms_operators_and_adds_new_sites():
    ocid = pd.DataFrame({"lat": [54.0], "lon": [-3.0], "operators": ["EE"], "operator_labels": ["EE"],
                         "radio_types": ["LTE"], "cell_count": [3]})
    osm = pd.DataFrame(columns=["osm_id", "lat", "lon", "osm_operator", "operators", "mobile_tagged"])
    sites = merge_sites(ocid, osm)
    wtr = _wtr([
        (54.0009, -3.0, "EE,Three", "MOBILE BROADBAND NETWORK LIMITED", 2, True, 25.0),  # ~100 m away: matches
        (54.5, -3.0, "O2", "Telefonica UK Limited", 1, True, 18.0),                      # far: new site
    ])
    merged, stats = merge_wtr(sites, wtr)
    assert stats == {"wtr_sites": 2, "matched_existing": 1, "new_sites": 1}
    m = merged.set_index("sources")
    hit = m.loc["ofcom_wtr,opencellid"]
    assert hit.operators_inferred == "EE" and hit.operators_confirmed == "EE,Three" and hit.operators == "EE,Three"
    assert hit.has_microwave_backhaul and hit.antenna_height_m == 25.0
    assert hit.lat == pytest.approx(54.0009)  # moved from OpenCelliD's estimate to the register position
    new = m.loc["ofcom_wtr"]
    assert new.operators == "O2" and new.operators_confirmed == "O2" and new.operators_inferred == ""


def test_masts_per_cell_distance_bearing_operators():
    sites = pd.DataFrame(
        {
            "site_id": ["S1", "S2", "S3"],
            "lat": [54.4450 + 0.0270, 54.4450, 54.4450 - 0.0180],  # ~3 km N; ~20 km E; ~2 km S
            "lon": [-3.1050, -3.1050 + 0.31, -3.1050],
            "operators": ["EE,Vodafone", "Three", ""],
            "sources": ["opencellid", "opencellid,osm", "osm"],
        }
    )
    cells = pd.DataFrame({"cell_id": ["c"], "lat": [54.4450], "lon": [-3.1050]})
    row = masts_per_cell(cells, sites).iloc[0]
    assert row.nearest_mast_id == "S3" and row.nearest_mast_km == pytest.approx(2.0, abs=0.05)
    assert row.nearest_mast_bearing_deg == pytest.approx(180.0, abs=0.5)
    assert row.masts_within_5km == 2 and row.masts_osm_only_within_5km == 1
    assert row.distinct_operators_within_5km == 2 and row.operators_within_5km == "EE,Vodafone"
    assert row.masts_known_operator_within_5km == 1 and row.masts_unknown_operator_within_5km == 1
    assert row.operator_data_status == "inferred" and row.wtr_sites_within_5km == 0


def test_operator_data_status():
    sites = pd.DataFrame(
        {
            "site_id": ["K", "U", "O", "C"],
            "lat": [54.0, 55.0, 56.0, 57.0], "lon": [-3.0, -3.0, -3.0, -3.0],
            "operators": ["EE", "", "", "O2"],
            "operators_confirmed": ["", "", "", "O2"],
            "operator_labels": ["EE", "", "Pronto GSM", ""],  # O: a known, non-main network
            "sources": ["opencellid", "osm", "opencellid", "ofcom_wtr"],
            "has_microwave_backhaul": [False, False, False, True],
        }
    )
    cells = pd.DataFrame(
        {"cell_id": ["k", "u", "o", "c", "far"], "lat": [54.0, 55.0, 56.0, 57.0, 58.5], "lon": [-3.0] * 5}
    )
    out = masts_per_cell(cells, sites).set_index("cell_id")
    assert out.operator_data_status.to_dict() == {
        "k": "inferred", "u": "unknown", "o": "inferred", "c": "confirmed", "far": "no_sites_found"}
    assert out.loc["c"].operators_confirmed_within_5km == "O2"
    assert out.loc["c"].wtr_sites_within_5km == 1 and out.loc["c"].microwave_backhaul_sites_within_5km == 1


def test_bearing_east():
    assert bearing_deg(54.0, -3.0, 54.0, -2.0) == pytest.approx(90.0, abs=0.5)


def test_osm_tags():
    assert is_comms_mast({"man_made": "mast", "tower:type": "communication"})
    assert is_comms_mast({"man_made": "communications_tower"})
    assert is_comms_mast({"man_made": "street_cabinet", "communication:mobile_phone": "yes"})
    assert not is_comms_mast({"man_made": "tower", "tower:type": "lighting"})
    assert operator_groups("MBNL") == ["EE", "Three"]
    assert operator_groups("Cornerstone Telecommunications Infrastructure Ltd") == ["O2", "Vodafone"]
    assert operator_groups("Vodafone UK") == ["Vodafone"]
    assert operator_groups("EE Limited") == ["EE"]
    assert operator_groups("Arqiva") == []
    assert operator_groups("Three") == ["Three"]
