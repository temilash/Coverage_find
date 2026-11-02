"""LiDAR tile catalogue helpers: OS grid tile names and bounds (offline)."""

import pytest
from shapely.geometry import box

from coverage_detective.lidar import catalog as C


@pytest.mark.parametrize("name, bounds", [
    ("SU9545", (495000, 145000, 496000, 146000)),                    # read as a 1 km reference
    ("NS16NE_50CM_DSM_PHASE3.tif", (215000, 665000, 220000, 670000)),  # 5 km quadrant of a 10 km square
    ("HY20_1M_DSM_PHASE1.tif", (320000, 1000000, 330000, 1010000)),    # 10 km square (Orkney)
    ("NG2003_50cm_DSM_ScotlandNationalLiDAR.tif", (120000, 803000, 121000, 804000)),  # 1 km
    ("NC3461_50CM_DSM_COASTAL20252026.tif", (234000, 961000, 235000, 962000)),
])
def test_grid_tile_bounds(name, bounds):
    assert C.grid_tile_bounds(name) == tuple(float(v) for v in bounds)


def test_bad_names_have_no_bounds():
    assert C.grid_tile_bounds("wg_del_1_222381_20200322dsm.tif") is None
    assert C.grid_tile_bounds("IZ1234") is None  # no I in the grid letters


def test_ea_5km_tile_names_round_trip():
    assert C.tile_name_5km((495000, 145000)) == "SU9545"
    assert C.tile_name_5km((500000, 150000)) == "TQ0050"
    assert C.tile_name_5km((325000, 505000)) == "NY2505"


def test_grid_tiles_cover_a_region():
    tiles = C.grid_tiles(box(497000, 147000, 503000, 152000), 5000)  # spans 2 x 2 tiles
    assert len(tiles) == 4 and (495000.0, 145000.0, 500000.0, 150000.0) in tiles


def test_collections_and_ni_area_names():
    assert C.scotland_collection("lidar/phase-3/dsm/27700/gridded/NS16NE_50CM_DSM_PHASE3.tif") == "phase-3"
    assert C.scotland_collection("lidar/hes/hes-2016/dtm/27700/gridded/NM2520_25CM_DTM_HES2016.tif") == "hes/hes-2016"
    assert "hes/hes-2010" not in C.SCOTLAND_OGL and "hes/hes-luing" not in C.SCOTLAND_OGL  # non-commercial only
    assert C._ni_area("Bangor Extension_05_03_2012") == "bangor extension"
    assert C._ni_area("Foyle_23_05_2004") == "foyle"
