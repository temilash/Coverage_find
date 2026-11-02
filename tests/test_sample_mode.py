"""Sample mode end to end: DuckDB over the committed extracts, the committed DEM crops and saved live
responses. No network (any HTTP call fails the test) and no keys."""

import httpx
import pytest
from fastapi.testclient import TestClient

from coverage_detective.data import db

LANGDALE = {"lat": 54.445, "lon": -3.105}
LONDON = {"lat": 51.507211, "lon": -0.128269}
BETTWS = {"lat": 52.399095, "lon": -3.186185}  # no tests, no site within 10 km


@pytest.fixture
def sample(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "1")
    from coverage_detective.data import terrain
    monkeypatch.setattr(terrain, "_dem", None)  # the committed DEM crops, not data/raw/dem
    monkeypatch.setattr(terrain, "DEM_DIR", tmp_path / "no_raw_dem")
    monkeypatch.setattr(httpx, "request", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    db.reset()
    from coverage_detective.api.app import app
    yield TestClient(app)
    db.reset()


def test_every_sample_table_is_a_view_with_rows(sample):
    names = {t for _, t, _ in db.available()}
    assert {"cells_r7", "ookla_mobile_r7", "masts_sites", "gap_zones", "opportunity_zones"} <= names
    for schema, table, _ in db.available():
        assert db.query(f"SELECT count(*) AS n FROM {schema}.{table}")[0]["n"] > 0


def test_the_cell_query_runs_natively_and_returns_its_history(sample):
    import h3
    cid = h3.latlng_to_cell(LONDON["lat"], LONDON["lon"], 7)
    r = sample.get(f"/api/cell/{cid}")
    assert r.status_code == 200
    cell = r.json()["cell"]
    assert cell["tests"] > 1000 and cell["history"] and cell["history"] == sorted(cell["history"], key=lambda h: h["quarter"])
    assert "FROM CD_CELLS.cells_r7" in r.json()["query"]["sql"]


def test_sample_mode_refuses_a_point_outside_the_extracts_with_the_areas_it_covers(sample):
    for path in ("/api/diagnose", "/api/evidence"):
        r = sample.post(path, json={"lat": 51.75, "lon": -1.25})
        assert r.status_code == 404 and "Sample mode only has data around" in r.json()["detail"]
        assert "great langdale" in r.json()["detail"]


def test_great_langdale_is_terrain(sample):
    d = sample.post("/api/diagnose", json=LANGDALE).json()
    assert d["causes"][0]["cause"] == "terrain_blocking" and d["headline"].startswith("Terrain")
    ids = {e["id"] for e in d["evidence"]}
    assert {"speed_cell", "nearest_site", "los_ee_three", "los_o2", "los_vodafone"} <= ids
    assert "EE, Three, O2 and Vodafone" in d["causes"][0]["explanation"]
    for c in d["causes"]:  # every cause cites evidence that is in the bundle
        assert c["evidence_ids"] and set(c["evidence_ids"]) <= ids and 0 < c["score"] <= 0.95
    assert any("coverage checker" in a for a in d["actions"])
    assert d["queries"] and all(q["sql"] for q in d["queries"])


def test_london_measures_well(sample):
    d = sample.post("/api/diagnose", json=LONDON).json()
    assert d["headline"] == "No strong cause was found; the service measures well here."
    assert all(c["score"] <= 0.3 for c in d["causes"])


def test_no_data_says_so_and_never_says_no_coverage(sample):
    d = sample.post("/api/diagnose", json=BETTWS).json()
    assert d["headline"].startswith("Not enough data to say why")
    text = " ".join([d["headline"], *d["limitations"]]).lower()
    assert "operators could not be identified" in text
    assert "no coverage" not in text.replace("not the same as no coverage", "")


def test_evidence_carries_its_diagnosis(sample):
    b = sample.post("/api/evidence", json=LANGDALE).json()
    assert b["diagnosis"]["causes"] and b["queries"] and b["operators"]


def test_the_email_is_a_template_filled_from_the_evidence(sample):
    r = sample.post("/api/email", json={**LANGDALE, "to": "EE", "sent_by": "A. Resident"})
    assert r.status_code == 200
    e = r.json()
    assert e["contact"]["name"] == "EE" and "Terrain" in e["body_text"] and "A. Resident" in e["body_text"]
    assert "Evidence and sources" in e["markdown"] and "[los_ee_three]" in e["markdown"]
    assert "[los_ee_three]" not in e["body_text"]  # ids are dropped from what is sent
    edited = sample.post("/api/email", json={**LANGDALE, "to": "EE", "body_markdown": "Hello [Your name]."}).json()
    assert edited["body_text"].startswith("Hello A local resident.")
    assert sample.post("/api/email", json={**LANGDALE, "to": "Nobody"}).status_code == 422


def test_recipients_list_the_council_then_the_networks(sample):
    rs = sample.get("/api/outreach/recipients", params={"district": "Westmorland and Furness"}).json()["recipients"]
    assert rs[0]["kind"] == "council" and [r["name"] for r in rs[1:]] == ["EE", "O2", "Three", "Vodafone"]


def test_a_saved_postcode_far_from_the_point_is_not_reported(sample):
    from coverage_detective.sources import geocode
    assert geocode.reverse(54.47, -3.07).data["found"] is False  # Great Langdale's saved postcode is 3 km away
    near = geocode.reverse(54.445, -3.105).data
    assert near["found"] and near["distance_m"] < 2000


def test_health_reports_the_data(sample):
    h = sample.get("/health").json()
    assert h["data"] == "ok" and h["sample_mode"] is True
