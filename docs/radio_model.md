# Radio model

An estimate of 4G signal strength (RSRP, dBm) at every H3 cell centre, from every approximate mast site within
range, over the Copernicus DEM. Code: `coverage_detective/radio/`. Results are **model estimates, not
measurements**; how they compare with Ookla's measured speeds is in [radio_validation.md](radio_validation.md).

## Why this model

The brief preferred ITU-R P.1812 if an implementation was (a) open source under MIT, BSD or Apache 2.0,
(b) maintained, and (c) validated against ITU test data. Checked November 2026:

| Implementation | Licence | Maintained | Verdict |
|---|---|---|---|
| [Py1812](https://github.com/eeveetza/Py1812) (P.1812-8, validated against ITU-R SG3 test profiles) | custom notice (GitHub: `NOASSERTION`): MIT-like warranty text plus "you may improve, modify ... and distribute", not MIT, BSD or Apache 2.0 | yes (last push 28 Aug 2026) | fails (a); also needs ITU's digital maps (DN50, N050), which ITU does not license for redistribution |
| [pycraf](https://github.com/bwinkel/pycraf) (P.452, not P.1812) | GPL-3.0 | yes | fails (a) |

So, as the brief asked, the model is the baseline: **Hata / COST-231 Hata path loss, free space as a lower
bound, plus knife-edge diffraction along the terrain profile (Bullington)**. Bullington was chosen over
Deygout because it reduces each profile to one equivalent edge, which vectorises over millions of paths.

## Formulas

f in MHz, d in km, hb = antenna height above ground at the site, hm = receiver height above ground (m).

| Part | Formula | Valid range |
|---|---|---|
| Free space (ITU-R P.525) | L = 32.45 + 20 log f + 20 log d | any |
| Okumura-Hata (Hata 1980), urban | L = 69.55 + 26.16 log f - 13.82 log hb - a(hm) + (44.9 - 6.55 log hb) log d | f 150-1500 MHz, hb 30-200 m, hm 1-10 m, d 1-20 km |
| a(hm), small / medium city | (1.1 log f - 0.7) hm - (1.56 log f - 0.8) | hm 1-10 m |
| COST-231 Hata (COST 231, 1999), urban | L = 46.3 + 33.9 log f - 13.82 log hb - a(hm) + (44.9 - 6.55 log hb) log d + Cm, Cm = 0 | f 1500-2000 MHz, otherwise as Hata |
| Suburban correction | L - 2 (log(f / 28))^2 - 5.4 | as above |
| Open-area correction | L - 4.78 (log f)^2 + 18.33 log f - 40.94 | as above |
| Knife edge (ITU-R P.526-16 eq. 31) | J(v) = 6.9 + 20 log(sqrt((v - 0.1)^2 + 1) + v - 0.1) for v > -0.78, else 0 | single edge |
| Fresnel parameter | v = h sqrt(2 (d1 + d2) / (lambda d1 d2)) | h = edge height above the direct line |
| Bullington (ITU-R P.526-16 4.5.1) | the steepest slopes from each end over the profile meet at one equivalent edge (with line of sight: the point of least clearance); loss J(v) | profile with earth curvature d1 d2 / (2 k R), k = 4/3 |

**Total path loss** = max(free space, Hata for the cell's environment) + **excess diffraction**, where
excess diffraction = max(0, Bullington(actual profile) - Bullington(smooth profile)), and the smooth
profile is a straight line between the ground at the two ends. Hata is an empirical median that already
includes the loss from ordinary ground near a low receiver: on a flat 2 km path, plain Bullington gives
about 1.3 dB because the ground enters the first Fresnel zone. Adding that again would count it twice. So
only the diffraction beyond a smooth path is added, the "delta" idea of P.526-16 4.5.4 and P.1812, with a
straight line in place of their spherical-earth term. Flat or evenly sloping ground adds nothing; ridges and
the obstruction input add their loss; valleys below the line are not credited.

**RSRP** = reference-signal EIRP + antenna pattern gain (omni: 0 dB) + receiver gain (0 dBi) - path loss.
Reference-signal EIRP = transmit power - 10 log(12 x resource blocks) + antenna gain - feeder loss: 34.2 dBm at
5 MHz, 31.2 dBm at 10 MHz with the defaults.

Outside the valid ranges the formulas are extrapolated: most sites are assumed 25 m high (Hata's range starts
at 30 m), paths reach 30 km (Hata's ends at 20 km), and cells next to a site are under 1 km (free space takes
over there when it gives more loss). The receiver can be at any height; Hata's a(hm) is clamped to 1-10 m,
while the profile uses the true height, so drone altitudes change the diffraction but not Hata's term.

## Assumptions (all in `coverage_detective/radio/config.py`)

| Setting | Default | Note |
|---|---|---|
| Band per operator | LTE 800 MHz (Band 20): EE 5 MHz, O2 10 MHz, Three 5 MHz, Vodafone 10 MHz | the coverage layer; Ofcom's 2013 auction holdings. 1800 / 2600 MHz and 3.5 GHz are defined too |
| Maximum range | 30 km at 800 MHz, 15 km at 1800, 8 km at 2600 / 3500 | site-cell pairs further apart are not computed |
| Transmit power, antenna gain, feeder loss | 46 dBm (40 W), 15 dBi, 2 dB | per sector and carrier |
| Antenna pattern | omni | full gain every way, no downtilt; a sector pattern can be added in `antenna_gain_db` |
| Antenna height | Ofcom register when known (9% of sites), else 25 m; per-site overrides | `site_height_overrides` |
| Receiver height | 1.5 m | any height (`--rx-height`) |
| Environment | urban >= 2,500 people per km2, suburban >= 300, else open | from the cell's r7 population |
| Profile | 30 m samples (the DEM's spacing), k = 4/3 | |

Sites: every approximate mast site (`masts_sites`) with an operator label (confirmed by Ofcom's register or
inferred from crowd-sourced data), modelled for each operator it carries. Sites with no known operator
(8,321 in the UK) are left out: there is no band to model them on. One real mast can appear as several
sites, which only matters for the best-server choice (the strongest wins).

## Obstruction input (buildings later)

`profile.build(..., obstruction=...)` raises the profile above the bare DEM: `None` (default, terrain only),
one height per profile sample, or an object with `heights(lats, lons)`. `CellClutter` builds that from a
per-cell summary (cell_id, median_height_m, building_fraction): each sample point gets the median building
height times the built fraction of its cell. The build script takes the summary with `--clutter file.parquet`,
so the LiDAR building heights can be plugged in without changing the model.

## Running

```bash
.venv/bin/python scripts/radio/build_coverage.py --region Surrey --estimate   # work and timing only
.venv/bin/python scripts/radio/build_coverage.py --region Surrey             # ~4 min on a laptop
.venv/bin/python scripts/radio/validate.py                                   # docs/radio_validation.md
```

The region is "UK", a nation, a local authority, a county made of its districts (Surrey), or `--bbox`. Each run
estimates site-cell pairs x profile samples, times a sample, and stops if the estimate exceeds `--max-hours`
(default 1) unless `--yes` is passed. Measured: Surrey (356 r7 cells, 1.49 m pairs, 1.04 bn samples) 3.6 min;
Westmorland and Furness (827 cells, 0.66 m pairs) 1.6 min; about 400-600 ns per profile sample.

Output: `data/processed/radio_coverage_r7.parquet` (DuckDB view `CD_RADIO.radio_coverage_r7`), one row per
cell: `best_rsrp_dbm`, `best_operator`, `best_site_id`, `best_distance_km`, `best_path_loss_db`,
`best_diffraction_db`, then `rsrp_<op>_dbm`, `site_<op>`, `distance_<op>_km`, `diffraction_<op>_db` for EE, O2,
Three and Vodafone, `pairs_modelled`, `environment`, `rx_height_m`, `model_version`, `region`, `built_at`.
Rebuilding a region replaces its cells and keeps the others. Other receiver heights go to
`radio_coverage_r7_rx<h>m.parquet`. The config used is in `data/processed/radio_config.json`.

## Limitations

Model estimates are not measurements. The mast data is crowd-sourced, incomplete and approximate, and most
antenna heights are assumed. One band and an omni antenna per site; no sectors, tilt, capacity or 5G. The DEM
is a surface model that includes some trees and buildings. The value is at the cell centre, not across the
cell. The validation found no better ranking of cells by speed than distance to the nearest site
([radio_validation.md](radio_validation.md)).
