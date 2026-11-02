"""Radio propagation model: estimated signal strength (RSRP) from approximate mast sites to H3 cells.

    config.py       every assumption in one place (bands, transmit power, heights, ranges)
    profile.py      terrain path profiles from the DEM, with an optional obstruction (clutter) input
    propagation.py  free-space loss, Hata / COST-231 Hata, knife-edge diffraction, Bullington
    coverage.py     site-cell pairs within range, path loss, best server per cell and operator

Results are model estimates, not measurements.
"""
