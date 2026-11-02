"""Live source clients, run offline against the saved samples in sample_data/."""

from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from coverage_detective.sources import flood_warnings, geocode, live, met_office_warnings as mo, weather

LANGDALE, LONDON, RHAYADER = (54.445, -3.105), (51.507211, -0.128269), (52.30, -3.51)


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """SAMPLE_MODE on, and an empty private cache."""
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setattr(live, "CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def failing_network(monkeypatch, tmp_path):
    """SAMPLE_MODE off, but every HTTP call fails."""
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setattr(live, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(live, "RETRY_BACKOFF_S", 0)
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(live.httpx, "get", boom)
    return calls


def test_postcode_vs_place():
    for q in ["WC2N 5DU", "wc2n5du", "LD6 5AD", "M1 1AE", "EH1 1YZ", "BT1 5GS"]:
        assert geocode.is_postcode(q), q
    for q in ["Rhayader", "Great Langdale", "WC2N", "London SW1", "12345"]:
        assert not geocode.is_postcode(q), q
    assert geocode.normalise_postcode("wc2n5du") == "WC2N 5DU"


def test_lookup_postcode_and_place_from_samples(offline):
    res = geocode.lookup("WC2N 5DU")
    assert res.status == "sample" and res.data["found"]
    hit = res.data["results"][0]
    assert hit["postcode"] == "WC2N 5DU" and hit["admin_district"] == "Westminster" and hit["cell_id"]
    place = geocode.lookup("Rhayader").data["results"][0]
    assert place["label"] == "Rhayader" and place["admin_district"] == "Powys" and place["country"] == "Wales"
    # Not in postcodes.io's place list: approximate matches from the retry.
    approx = geocode.lookup("Great Langdale").data["results"]
    assert approx and all(r["approximate"] for r in approx)


def test_postcode_not_found_is_clean(offline):
    res = geocode.lookup("ZZ1 1ZZ")
    assert res.status == "sample" and res.data == {"query": "ZZ1 1ZZ", "found": False, "results": []}


def test_reverse_from_sample(offline):
    res = geocode.reverse(*LANGDALE)
    n = res.data["nearest"]
    assert n["country"] == "England" and n["region"] == "North West"
    assert geocode.place_name(n)


def test_weather_severity_thresholds():
    assert weather.severity(72, 0, 0, [3])[0] == "severe"
    assert weather.severity(40, 9, 0, [61])[0] == "severe"
    assert weather.severity(10, 0, 0, [95])[0] == "severe"
    assert weather.severity(55, 0, 0, [3])[0] == "moderate"
    assert weather.severity(20, 2.5, 0, [61])[0] == "moderate"
    assert weather.severity(20, 0, 0.2, [71])[0] == "moderate"
    assert weather.severity(30, 1.0, 0, [2]) == ("none", [])


def test_weather_summary_from_sample(offline):
    res = weather.forecast(*RHAYADER)
    d = res.data
    assert res.status == "sample" and res.sample_place == "rhayader"
    assert d["next_24h"]["hours"] == 24 and d["severity"] in ("none", "moderate", "severe")
    assert d["next_24h"]["max_gust_kmh"] >= 0 and "weather" in d["now"]


def test_met_office_regions():
    assert mo.region_for({"country": "England", "region": "North West"}) == "nw"
    assert mo.region_for({"country": "England", "region": "London"}) == "se"
    assert mo.region_for({"country": "Wales", "region": "Wales"}) == "wl"
    assert mo.region_for({"country": "Scotland", "admin_district": "Highland"}) == "he"
    assert mo.region_for({"country": "Scotland", "admin_district": "City of Edinburgh"}) == "dg"
    assert mo.region_for({"country": "Northern Ireland"}) == "ni"
    assert mo.region_for(None) is None


FEED = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<title>Met Office warnings for Wales</title><pubDate>Sun, 25 Oct 2026 15:01:00 GMT</pubDate>
<item><title>Yellow warning of wind affecting Wales</title>
<description>Yellow warning of wind affecting Wales: Conwy, Gwynedd, Powys valid from 1100 Sat 24 Oct to 2100 Sun 25 Oct</description></item>
<item><title>Amber warning of rain, wind affecting Wales</title>
<description>Amber warning of rain, wind affecting Wales: Ceredigion valid from 0600 Mon 26 Oct to 1800 Mon 26 Oct</description></item>
<item><title>Yellow warning of ice affecting Wales</title>
<description>Yellow warning of ice affecting Wales: Powys valid from 0000 Fri 23 Oct to 0900 Fri 23 Oct</description></item>
</channel></rss>"""


def test_met_office_parse_and_active():
    ws = mo.parse_feed(FEED)
    assert [w["level"] for w in ws] == ["yellow", "amber", "yellow"]
    assert ws[1]["types"] == ["rain", "wind"] and ws[0]["areas"] == ["Conwy", "Gwynedd", "Powys"]
    # 1100 BST on 24 Oct = 10:00 UTC; 2100 GMT on 25 Oct (clocks went back) = 21:00 UTC.
    assert ws[0]["valid_from"] == "2026-10-24T10:00:00+00:00" and ws[0]["valid_to"] == "2026-10-25T21:00:00+00:00"
    now = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc)
    act = mo.active(ws, now)
    assert len(act) == 2  # the ice warning has expired
    assert act[0]["in_force"] and not act[1]["in_force"]


def test_met_office_from_sample(offline):
    place = {"country": "Wales", "region": "Wales", "admin_district": "Powys"}
    res = mo.for_place(place, *RHAYADER)
    assert res.status == "sample" and res.data["region"] == "Wales"
    assert all("district_listed" in w for w in res.data["warnings"])


def test_floods_not_covered_outside_england(offline):
    for country in ["Wales", "Scotland", "Northern Ireland"]:
        res = flood_warnings.near(*RHAYADER, country)
        assert res.status == "not_covered" and res.data["covered"] is False
        assert "not covered" in res.data["message"] and "warnings" not in res.data


def test_floods_from_sample(offline):
    res = flood_warnings.near(*LANGDALE, "England")
    assert res.status == "sample" and res.data["covered"]
    assert all(w["severity_level"] in (1, 2, 3) for w in res.data["warnings"])


def test_failure_falls_back_to_sample_after_one_retry(failing_network):
    res = weather.forecast(*LANGDALE)
    assert res.status == "sample" and res.sample_place == "great_langdale" and "no network" in res.error
    assert len(failing_network) == 2  # first try + one retry


def test_failure_prefers_cached_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setattr(live, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(live, "RETRY_BACKOFF_S", 0)
    monkeypatch.setattr(live.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"ok": 1}, request=httpx.Request("GET", "https://example.test/x")))
    assert live.get("t", "https://example.test/x", ttl_s=0, sample="none").status == "live"

    def boom(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(live.httpx, "get", boom)
    res = live.get("t", "https://example.test/x", ttl_s=0, sample="none")
    assert res.status == "cached" and res.body == {"ok": 1} and res.to_dict()["data_period"] == "live"


def test_unavailable_without_cache_or_sample(failing_network):
    res = live.get("nothing_saved", "https://example.test/y", ttl_s=60, sample="none")
    assert res.status == "unavailable" and res.error


def test_api_live_offline(offline):
    from coverage_detective.api.app import app

    body = TestClient(app).get("/api/live", params={"lat": RHAYADER[0], "lon": RHAYADER[1]}).json()
    assert body["sample_mode"] and body["place"]["country"] == "Wales"
    assert body["weather"]["status"] == "sample" and body["met_office_warnings"]["status"] == "sample"
    assert body["flood_warnings"]["status"] == "not_covered"
    for key in ("weather", "met_office_warnings", "flood_warnings"):
        assert {"source", "url", "fetched_at", "status", "as_of", "data_period"} <= set(body[key])
