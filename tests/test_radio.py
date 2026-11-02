"""Radio model on synthetic terrain (no DEM files): formulas against known values, profiles, coverage."""

import h3
import numpy as np
import pandas as pd
import pytest

from coverage_detective.radio import coverage, profile, propagation
from coverage_detective.radio.config import RadioConfig


class FakeDEM:
    """Flat ground at `base` m, with an optional thin ridge across latitude `ridge_lat`."""

    def __init__(self, base=0.0, ridge_lat=None, ridge_height=0.0, half_width_deg=0.0002):
        self.base, self.ridge_lat, self.h, self.w = base, ridge_lat, ridge_height, half_width_deg

    def elevation(self, lats, lons):
        z = np.full(np.shape(lats), self.base, dtype=np.float32)
        if self.ridge_lat is not None:
            z[np.abs(np.asarray(lats) - self.ridge_lat) <= self.w] += self.h
        return z


def one_path(dem, rx_height=1.5, mast=25.0, obstruction=None, lat_rx=54.02):
    """A site at 54.00 N and a receiver due north (2.22 km for 54.02)."""
    p = profile.build([54.0], [-3.0], [lat_rx], [-3.0], dem, 30.0, obstruction)
    return p, propagation.excess_diffraction_db(p, p.ground_tx + mast, p.ground_rx + rx_height, 800.0)[0]


# ------------------------------------------------------------------ formulas


def test_free_space_known_values():
    assert propagation.free_space_db(900, 1.0) == pytest.approx(91.53, abs=0.01)
    assert propagation.free_space_db(2400, 1.0) == pytest.approx(100.05, abs=0.01)
    # +6 dB per doubling of distance
    assert propagation.free_space_db(800, 2.0) - propagation.free_space_db(800, 1.0) == pytest.approx(6.02, abs=0.01)


def test_hata_worked_example():
    # Okumura-Hata, 900 MHz, 30 m base, 1.5 m mobile, medium city: 126.4 dB at 1 km, slope 35.2 dB/decade.
    assert propagation.hata_db(900, 1.0, 30, 1.5, "urban") == pytest.approx(126.4, abs=0.1)
    assert propagation.hata_db(900, 10.0, 30, 1.5, "urban") == pytest.approx(126.4 + 35.22, abs=0.1)
    # Suburban 900 MHz: -2 (log(900/28))^2 - 5.4 = -9.9 dB; open: -4.78 (log f)^2 + 18.33 log f - 40.94 = -28.5 dB.
    assert propagation.hata_db(900, 1.0, 30, 1.5, "suburban") == pytest.approx(126.4 - 9.9, abs=0.1)
    assert propagation.hata_db(900, 1.0, 30, 1.5, "open") == pytest.approx(126.4 - 28.5, abs=0.1)
    # COST-231 Hata at 1800 MHz, same geometry: 46.3 + 33.9 log 1800 - 13.82 log 30 - a(1.5) = 136.3 dB.
    assert propagation.hata_db(1800, 1.0, 30, 1.5, "urban") == pytest.approx(136.3, abs=0.2)


def test_hata_environment_per_path():
    out = propagation.hata_db(800, [1.0, 1.0, 1.0], 25, 1.5, np.array(["urban", "suburban", "open"]))
    assert out[0] > out[1] > out[2]


def test_knife_edge_textbook():
    assert propagation.knife_edge_db(0.0) == pytest.approx(6.0, abs=0.05)     # grazing: 6 dB
    assert propagation.knife_edge_db(-1.0) == 0.0
    # Rappaport, Wireless Communications, Example 4.7: 900 MHz, edge 25 m above the line, 1 km from each
    # end: v = 2.74 and about 22 dB (the book's approximation; P.526's formula gives 21.7 dB).
    v = propagation.fresnel_v(25.0, 1000.0, 1000.0, propagation.wavelength_m(900.0))
    assert v == pytest.approx(2.74, abs=0.01)
    assert propagation.knife_edge_db(v) == pytest.approx(22.0, abs=0.5)


# ------------------------------------------------------------------ profiles


def test_profile_samples_and_ends():
    dem = FakeDEM(base=50.0)
    p = profile.build([54.0, 54.0], [-3.0, -3.0], [54.02, 54.001], [-3.0, -3.0], dem, 30.0)
    assert len(p) == 2
    assert p.length_m[0] == pytest.approx(2224, abs=5)
    counts = np.bincount(p.seg)
    assert counts[0] == pytest.approx(2224 / 30, abs=1) and counts[1] >= 1
    assert p.starts.tolist() == [0, counts[0]]
    assert (p.di[p.seg == 0] > 0).all() and (p.di[p.seg == 0] < p.length_m[0]).all()
    assert (p.ground_tx == 50).all() and (p.hi == 50).all()


def test_flat_short_path_has_no_excess_diffraction():
    p, loss = one_path(FakeDEM())
    assert loss == 0.0
    # Plain Bullington sees the ground near a 1.5 m receiver in the Fresnel zone; Hata already includes that.
    plain = propagation.bullington_db(p, p.ground_tx + 25.0, p.ground_rx + 1.5, 800.0)[0]
    assert 0 < plain < 3
    # A sloping but straight path is smooth too.
    _, sloped = one_path(Slope())
    assert sloped == pytest.approx(0.0, abs=1e-9)


class Slope:
    """Ground rising evenly from 0 m at the site to 100 m at 54.02 N."""

    def elevation(self, lats, lons):
        return np.clip((np.asarray(lats) - 54.0) / 0.02, 0, 1) * 100.0


def test_single_ridge_matches_one_knife_edge():
    # A 100 m ridge 1.11 km along a 2.22 km path; the Bullington edge is the ridge itself.
    p, loss = one_path(FakeDEM(ridge_lat=54.01, ridge_height=100))
    d = p.length_m[0]
    i = int(np.argmax(p.hi))
    d1 = p.di[i]
    line = 25.0 + (1.5 - 25.0) * d1 / d
    bulge = d1 * (d - d1) / (2 * 4 / 3 * 6_371_000)
    expected = propagation.knife_edge_db(
        propagation.fresnel_v(100 + bulge - line, d1, d - d1, propagation.wavelength_m(800.0)))
    smooth = propagation.bullington_db(p, p.ground_tx + 25.0, p.ground_rx + 1.5, 800.0,
                                       heights=np.zeros_like(p.hi))[0]
    assert loss == pytest.approx(float(expected) - smooth, abs=0.5)
    assert 25 < loss < 40


def test_obstruction_input_on_and_off():
    dem = FakeDEM()
    p0, off = one_path(dem)
    # 20 m of buildings over the last 300 m before the receiver.
    near_rx = (p0.length_m[0] - p0.di) < 300
    p1, on = one_path(dem, obstruction=np.where(near_rx, 20.0, 0.0))
    assert off == 0.0 and on > 10
    assert (p1.hi - p0.hi).max() == 20.0
    with pytest.raises(ValueError):
        one_path(dem, obstruction=np.zeros(3))


def test_cell_clutter_from_cell_summary():
    rx = h3.latlng_to_cell(54.02, -3.0, 9)
    clutter = profile.CellClutter(pd.DataFrame({"cell_id": [rx], "median_height_m": [12.0],
                                                "building_fraction": [0.5]}))
    assert clutter.heights([54.02, 54.0], [-3.0, -3.0]).tolist() == [6.0, 0.0]
    _, off = one_path(FakeDEM())
    _, on = one_path(FakeDEM(), obstruction=clutter)
    assert on > off


def test_receiver_height_changes():
    dem = FakeDEM(ridge_lat=54.01, ridge_height=60)
    heights = (1.5, 10.0, 60.0, 120.0)
    total, plain = [], []
    for h in heights:
        p, _ = one_path(dem, rx_height=h)
        loss, _ = propagation.path_loss_db(p, p.ground_tx + 25.0, p.ground_rx + h, 800.0, 25.0, h, "open")
        total.append(loss[0])
        plain.append(propagation.bullington_db(p, p.ground_tx + 25.0, p.ground_rx + h, 800.0)[0])
    assert total[0] > total[1] > total[2] > total[3]
    assert plain[0] > plain[1] > plain[2] > plain[3]
    assert one_path(dem, rx_height=120.0)[1] == 0.0   # a drone at 120 m clears a 60 m ridge with a 25 m mast
    # Hata's a(hm) is clamped to 1-10 m, so heights beyond it still return a finite loss.
    assert propagation.hata_db(800, 2.0, 25, 120.0) == propagation.hata_db(800, 2.0, 25, 10.0)


# ------------------------------------------------------------------ coverage


def fixture_sites():
    return pd.DataFrame({
        "site_id": ["A", "B", "C", "D"],
        "lat": [54.00, 54.05, 54.02, 55.0],
        "lon": [-3.00, -3.00, -3.02, -3.0],
        "operators": ["EE,Three", "O2", "", "Vodafone"],     # C unknown operator, D 100+ km away
        "antenna_height_m": [np.nan, 40.0, np.nan, np.nan],
    })


def fixture_cells():
    ids = [h3.latlng_to_cell(54.01, -3.0, 7), h3.latlng_to_cell(54.04, -3.0, 7)]
    lat, lon = np.array([h3.cell_to_latlng(c) for c in ids]).T
    return pd.DataFrame({"cell_id": ids, "lat": lat, "lon": lon, "density": [100.0, 3000.0],
                         "environment": coverage.environment([100.0, 3000.0])})


def test_coverage_best_server_and_operators():
    cfg = RadioConfig()
    out = coverage.coverage(fixture_cells(), fixture_sites(), cfg, source=FakeDEM())
    assert out.environment.tolist() == ["open", "urban"]
    first = out.iloc[0]
    assert first.best_site_id in ("A", "B")
    assert np.isnan(first.rsrp_vodafone_dbm)                 # the only Vodafone site is out of range
    assert first.site_ee == "A" and first.site_three == "A" and first.site_o2 == "B"
    assert first.pairs_modelled == 3                          # A for EE and Three, B for O2; not C, not D
    # EE and Three share site A and the band, so their RSRP differs only by the EIRP.
    assert first.rsrp_ee_dbm - first.rsrp_three_dbm == pytest.approx(cfg.rs_eirp_dbm("EE") - cfg.rs_eirp_dbm("Three"),
                                                                     abs=0.15)
    assert first.best_rsrp_dbm == max(first.rsrp_ee_dbm, first.rsrp_o2_dbm, first.rsrp_three_dbm)
    assert -120 < first.best_rsrp_dbm < -40
    assert out.model_version.iloc[0] == "hata-bullington-1"


def test_coverage_ridge_and_height_override():
    sites = fixture_sites()
    cells = fixture_cells()
    between = (54.0 + cells.lat.iloc[0]) / 2          # half way from site A to the first cell centre
    flat = coverage.coverage(cells, sites, source=FakeDEM())
    ridge = coverage.coverage(cells, sites, source=FakeDEM(ridge_lat=between, ridge_height=150))
    assert ridge.rsrp_ee_dbm.iloc[0] < flat.rsrp_ee_dbm.iloc[0] - 10
    assert ridge.diffraction_ee_db.iloc[0] > 10
    taller = coverage.coverage(cells, sites, RadioConfig(site_height_overrides={"A": 200.0}),
                               source=FakeDEM(ridge_lat=between, ridge_height=150))
    assert taller.rsrp_ee_dbm.iloc[0] > ridge.rsrp_ee_dbm.iloc[0]


def test_estimate_counts_pairs_without_terrain():
    est = coverage.estimate(fixture_cells(), fixture_sites())
    assert est["pairs"] == 4                             # 2 cells x (A, B); C has no operator; D out of range
    assert est["groups"][0]["sites"] == 2
    assert est["samples"] > 0


def test_config_eirp():
    cfg = RadioConfig()
    assert cfg.rs_eirp_dbm("O2") == pytest.approx(46 - 10 * np.log10(600) + 15 - 2, abs=0.01)
    assert cfg.rs_eirp_dbm("EE") == pytest.approx(cfg.rs_eirp_dbm("O2") + 3.01, abs=0.01)
    assert cfg.site_height("S1", np.nan) == (25.0, "assumed_default")
    assert cfg.site_height("S1", 30.0) == (30.0, "ofcom_register")


# ------------------------------------------------------------------ API


def test_api_labels_estimates_and_lists_operators(monkeypatch, tmp_path):
    import json

    from fastapi.testclient import TestClient

    from coverage_detective.api.app import app
    from coverage_detective.data import db

    out = coverage.coverage(fixture_cells(), fixture_sites(), source=FakeDEM(), region="Test")
    out.to_parquet(tmp_path / "radio_coverage_r7.parquet", index=False)
    (tmp_path / "radio_config.json").write_text(json.dumps(RadioConfig().as_dict()))
    monkeypatch.setenv("SAMPLE_MODE", "0")
    monkeypatch.setattr(db, "PROCESSED", tmp_path)
    db.reset()
    try:
        client = TestClient(app)
        meta = client.get("/api/radio/meta").json()
        assert meta["label"] == "Model estimate, not a measurement"
        assert [r["region"] for r in meta["regions"]] == ["Test"] and meta["config"]["rx_height_m"] == 1.5
        cid = out.cell_id.iloc[0]
        r = client.get(f"/api/radio/cell/{cid}").json()
        assert r["label"] == meta["label"]
        assert [o["operator"] for o in r["operators"]] == ["EE", "O2", "Three", "Vodafone"]
        assert r["operators"][3]["rsrp_dbm"] is None                  # no Vodafone site in range
        assert r["best_server"]["rsrp_dbm"] == max(o["rsrp_dbm"] for o in r["operators"] if o["rsrp_dbm"] is not None)
        assert r["best_server"]["class"] in ("strong", "good", "fair", "weak", "very weak")
        cells = client.get("/api/radio/cells", params={"bbox": "-3.1,53.9,-2.9,54.1", "operator": "EE"}).json()
        assert cells["count"] == 2 and cells["label"] == meta["label"]
        assert client.get("/api/radio/cells", params={"bbox": "-3.1,53.9,-2.9,54.1", "operator": "Orange"}).status_code == 400
        assert client.get(f"/api/radio/cell/{h3.latlng_to_cell(51.5, -0.1, 7)}").status_code == 404
        assert client.get("/api/radio/cell/not-a-cell").status_code == 400
    finally:
        db.reset()


def test_api_without_results_says_how_to_build_them(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from coverage_detective.api.app import app
    from coverage_detective.data import db

    monkeypatch.setenv("SAMPLE_MODE", "0")
    monkeypatch.setattr(db, "PROCESSED", tmp_path)
    db.reset()
    try:
        r = TestClient(app).get("/api/radio/meta")
        assert r.status_code == 404 and "build_coverage.py" in r.json()["detail"]
    finally:
        db.reset()
