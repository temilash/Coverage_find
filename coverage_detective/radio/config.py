"""Every assumption of the radio model in one place. Change a value here (or pass another RadioConfig) and
rebuild; each output row carries MODEL_VERSION and the receiver height, and the build records the full config.

Defaults model each operator's 4G coverage layer, LTE Band 20 (800 MHz): every UK operator holds 800 MHz
spectrum (Ofcom's 2013 auction: EE 2x5 MHz, O2 2x10, Three 2x5, Vodafone 2x10), and it is the band that
reaches furthest, so it sets where there is any 4G signal at all. Higher bands add capacity, not reach.

RSRP is the power of one LTE reference-signal resource element, so the transmit side is expressed as the
reference-signal EIRP: total power spread over 12 subcarriers per resource block, plus antenna gain, minus
feeder loss. For 46 dBm (40 W), 15 dBi and 2 dB: 34.2 dBm at 5 MHz (25 RBs), 31.2 dBm at 10 MHz (50 RBs).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

MODEL_VERSION = "hata-bullington-1"
OPERATORS = ("EE", "O2", "Three", "Vodafone")


@dataclass(frozen=True)
class Band:
    name: str
    freq_mhz: float
    bandwidth_mhz: float
    max_range_km: float   # site-cell pairs further apart are not computed


BANDS = {
    "B20": Band("LTE 800 (Band 20)", 800.0, 10.0, 30.0),
    "B20_5": Band("LTE 800 (Band 20)", 800.0, 5.0, 30.0),
    "B3": Band("LTE 1800 (Band 3)", 1800.0, 20.0, 15.0),
    "B7": Band("LTE 2600 (Band 7)", 2600.0, 20.0, 8.0),
    "n78": Band("NR 3.5 GHz (n78)", 3500.0, 40.0, 8.0),   # SS-RSRP, 30 kHz subcarriers
}

RESOURCE_BLOCKS = {1.4: 6, 3.0: 15, 5.0: 25, 10.0: 50, 15.0: 75, 20.0: 100, 40.0: 106}  # 40 MHz: NR at 30 kHz subcarrier spacing


@dataclass(frozen=True)
class RadioConfig:
    # The band modelled per operator (keys of BANDS).
    operator_band: dict = field(default_factory=lambda: {"EE": "B20_5", "O2": "B20", "Three": "B20_5",
                                                         "Vodafone": "B20"})
    tx_power_dbm: float = 46.0          # per sector and carrier (40 W)
    antenna_gain_dbi: float = 15.0      # typical 800 MHz macro panel
    feeder_loss_db: float = 2.0
    antenna_pattern: str = "omni"       # the only pattern so far: full gain in every direction, no downtilt
    antenna_height_m: float = 25.0      # used when Ofcom's register gives no height for the site
    site_height_overrides: dict = field(default_factory=dict)   # {site_id: antenna height above ground, m}
    rx_height_m: float = 1.5            # any height works; Hata's correction is valid for 1-10 m only
    rx_gain_dbi: float = 0.0
    sample_spacing_m: float = 30.0      # the DEM's resolution (GLO-30)
    k_factor: float = 4 / 3             # effective earth radius factor (standard atmosphere)
    # Hata environment per cell from people per km2 (the cell's r7 parent): urban, suburban, else open.
    urban_density: float = 2500.0
    suburban_density: float = 300.0

    def band(self, operator: str) -> Band:
        return BANDS[self.operator_band[operator]]

    def rs_eirp_dbm(self, operator: str) -> float:
        """Reference-signal EIRP per resource element (dBm)."""
        b = self.band(operator)
        subcarriers = 12 * RESOURCE_BLOCKS[b.bandwidth_mhz]
        return self.tx_power_dbm - 10 * math.log10(subcarriers) + self.antenna_gain_dbi - self.feeder_loss_db

    def antenna_gain_db(self, bearing_deg, operator: str):
        """Extra gain relative to the main beam towards a bearing. Omni: 0 everywhere."""
        if self.antenna_pattern != "omni":
            raise ValueError(f"unknown antenna pattern {self.antenna_pattern!r}")
        return 0.0

    def site_height(self, site_id: str, register_height_m: float | None) -> tuple[float, str]:
        """(antenna height above ground, where it came from)."""
        if site_id in self.site_height_overrides:
            return float(self.site_height_overrides[site_id]), "override"
        if register_height_m is not None and not math.isnan(register_height_m):
            return float(register_height_m), "ofcom_register"
        return self.antenna_height_m, "assumed_default"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["model_version"] = MODEL_VERSION
        d["rs_eirp_dbm"] = {op: round(self.rs_eirp_dbm(op), 1) for op in self.operator_band}
        d["bands"] = {op: asdict(self.band(op)) for op in self.operator_band}
        return d


DEFAULT = RadioConfig()

# How estimated RSRP is described on the map and in the API (common LTE rules of thumb, dBm lower bounds).
RSRP_CLASSES = [(-80.0, "strong"), (-90.0, "good"), (-100.0, "fair"), (-110.0, "weak"), (float("-inf"), "very weak")]
MODEL_LABEL = "Model estimate, not a measurement"


def rsrp_class(rsrp_dbm: float | None) -> str | None:
    if rsrp_dbm is None or math.isnan(rsrp_dbm):
        return None
    return next(label for floor, label in RSRP_CLASSES if rsrp_dbm >= floor)
