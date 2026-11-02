"""Path loss: Hata / COST-231 Hata (median loss over average ground), free space as a lower bound, plus
knife-edge diffraction over the terrain profile (Bullington's single equivalent edge).

    total loss = max(free space, Hata for the cell's environment) + excess diffraction loss
    excess diffraction = max(0, Bullington(actual profile) - Bullington(smooth profile))

The smooth profile is a straight line between the ground at the two ends, with the same antenna heights.
Hata is an empirical median that already includes the loss from ordinary ground near a low receiver (the
ground enters the first Fresnel zone even on a flat path), so only diffraction beyond that is added: the
"delta" idea of ITU-R P.526-16 section 4.5.4 and P.1812, with a straight line in place of P.526's
spherical-earth term. Flat ground adds nothing; a ridge, or buildings in the obstruction input, add their
loss; valleys below the straight line are not credited.

Formulas and their valid ranges (outside them the formulas are extrapolated; docs/radio_validation.md):

  Free space (ITU-R P.525):  L = 32.45 + 20 log f + 20 log d                     f in MHz, d in km
  Okumura-Hata (Hata 1980), urban, small / medium city:
      L = 69.55 + 26.16 log f - 13.82 log hb - a(hm) + (44.9 - 6.55 log hb) log d
      a(hm) = (1.1 log f - 0.7) hm - (1.56 log f - 0.8)
      valid: f 150-1500 MHz, hb 30-200 m, hm 1-10 m, d 1-20 km
  COST-231 Hata (COST Action 231, 1999), f 1500-2000 MHz:
      L = 46.3 + 33.9 log f - 13.82 log hb - a(hm) + (44.9 - 6.55 log hb) log d + Cm   (Cm = 0; 3 dB metropolitan)
  Environment corrections (Hata), applied to both:
      suburban: L - 2 (log(f / 28))^2 - 5.4          open: L - 4.78 (log f)^2 + 18.33 log f - 40.94
  Knife edge (ITU-R P.526-16 eq. 31):  J(v) = 6.9 + 20 log(sqrt((v - 0.1)^2 + 1) + v - 0.1) for v > -0.78, else 0
  Bullington (ITU-R P.526-16 section 4.5.1): the steepest slopes from each end to the profile points give
      one equivalent knife edge where they meet (or, with line of sight, the point of least clearance);
      its v gives the loss J(v). Earth curvature raises each point by d1 d2 / (2 k R).

hb is the antenna height above ground at the site and hm the receiver height above ground. Hata already
includes the average effect of buildings and gentle ground; the excess diffraction adds the hills and
ridges on the actual path. The DEM is a surface model, so in towns and woods some buildings and trees are
counted as terrain, and Hata's urban allowance counts them again.
"""

from __future__ import annotations

import numpy as np

HATA_RX_RANGE = (1.0, 10.0)
MIN_D_KM = 0.02   # the log terms are evaluated at least this far out; free space dominates there anyway
SPEED_OF_LIGHT = 299_792_458.0


def wavelength_m(freq_mhz: float) -> float:
    return SPEED_OF_LIGHT / (freq_mhz * 1e6)


def free_space_db(freq_mhz, d_km):
    d_km = np.maximum(np.asarray(d_km, dtype=float), 1e-3)
    return 32.45 + 20 * np.log10(freq_mhz) + 20 * np.log10(d_km)


def hata_db(freq_mhz: float, d_km, hb_m, hm_m: float, environment="urban"):
    """Okumura-Hata (f <= 1500 MHz) or COST-231 Hata (above; extrapolated beyond 2000 MHz).

    environment: "urban", "suburban" or "open", or an array of them (one per path). hm is clamped to the
    1-10 m range the a(hm) term was fitted on, so any receiver height can be passed.
    """
    lf = np.log10(freq_mhz)
    d = np.log10(np.maximum(np.asarray(d_km, dtype=float), MIN_D_KM))
    hb = np.log10(np.maximum(np.asarray(hb_m, dtype=float), 1.0))
    hm = float(np.clip(hm_m, *HATA_RX_RANGE))
    a_hm = (1.1 * lf - 0.7) * hm - (1.56 * lf - 0.8)
    base = 69.55 + 26.16 * lf if freq_mhz <= 1500 else 46.3 + 33.9 * lf
    urban = base - 13.82 * hb - a_hm + (44.9 - 6.55 * hb) * d
    env = np.broadcast_to(np.asarray(environment), np.shape(urban))
    return np.select([env == "suburban", env == "open"],
                     [urban - 2 * np.log10(freq_mhz / 28) ** 2 - 5.4,
                      urban - 4.78 * lf ** 2 + 18.33 * lf - 40.94], urban)


def knife_edge_db(v):
    """Diffraction loss (dB) of a single knife edge with diffraction parameter v (ITU-R P.526 eq. 31)."""
    v = np.asarray(v, dtype=float)
    loss = 6.9 + 20 * np.log10(np.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)
    return np.where(v > -0.78, loss, 0.0)


def fresnel_v(h_m, d1_m, d2_m, wavelength):
    """v for an edge h metres above the direct line, d1 and d2 from the two ends."""
    return h_m * np.sqrt(2 * (d1_m + d2_m) / (wavelength * d1_m * d2_m))


def bullington_db(profile, hts_m, hrs_m, freq_mhz: float, k_factor: float = 4 / 3, heights=None) -> np.ndarray:
    """Bullington diffraction loss for every path in a Profiles batch (profile.py).

    hts_m, hrs_m: antenna heights above sea level at the transmitter (site) and receiver (cell) ends.
    heights: the profile heights to use instead of profile.hi (e.g. the smooth profile).
    """
    seg, di, d = profile.seg, profile.di, profile.length_m
    hi = profile.hi if heights is None else heights
    hts, hrs = np.asarray(hts_m, dtype=float), np.asarray(hrs_m, dtype=float)
    lam = wavelength_m(freq_mhz)
    ae = k_factor * 6_371_000.0
    starts = profile.starts
    dd = d[seg]
    h = hi + di * (dd - di) / (2 * ae)                 # ground (+ obstruction) raised by earth curvature
    stim = np.maximum.reduceat((h - hts[seg]) / di, starts)        # steepest slope seen from the site
    srim = np.maximum.reduceat((h - hrs[seg]) / (dd - di), starts)  # ... and from the receiver
    str_ = (hrs - hts) / d                                          # slope of the direct line
    # Line of sight: the profile point with the highest v (it can be below the line: v < 0).
    above = h - (hts[seg] * (dd - di) + hrs[seg] * di) / dd
    v_los = np.maximum.reduceat(fresnel_v(above, di, dd - di, lam), starts)
    # Obstructed: one edge where the two steepest lines meet.
    with np.errstate(divide="ignore", invalid="ignore"):
        db = np.clip((hrs - hts + srim * d) / (stim + srim), 1.0, d - 1.0)
        hb = hts + stim * db - (hts * (d - db) + hrs * db) / d
        v_b = fresnel_v(hb, db, d - db, lam)
    v = np.where(stim < str_, v_los, v_b)
    return knife_edge_db(v)


def path_loss_db(profile, hts_m, hrs_m, freq_mhz: float, hb_m, hm_m: float, environment,
                 k_factor: float = 4 / 3) -> tuple[np.ndarray, np.ndarray]:
    """(total path loss, the diffraction part) per path, dB."""
    d_km = profile.length_m / 1000
    median = np.maximum(free_space_db(freq_mhz, d_km), hata_db(freq_mhz, d_km, hb_m, hm_m, environment))
    diff = excess_diffraction_db(profile, hts_m, hrs_m, freq_mhz, k_factor)
    return median + diff, diff


def excess_diffraction_db(profile, hts_m, hrs_m, freq_mhz: float, k_factor: float = 4 / 3) -> np.ndarray:
    """Bullington loss of the actual profile beyond that of a straight ground line between the two ends."""
    seg = profile.seg
    smooth = profile.ground_tx[seg] + (profile.ground_rx - profile.ground_tx)[seg] * profile.di / profile.length_m[seg]
    actual = bullington_db(profile, hts_m, hrs_m, freq_mhz, k_factor)
    base = bullington_db(profile, hts_m, hrs_m, freq_mhz, k_factor, heights=smooth)
    return np.maximum(actual - base, 0.0)
