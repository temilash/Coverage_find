"""Compare the radio model's estimated RSRP with Ookla's measured speeds per cell; writes docs/radio_validation.md.

    python scripts/radio/validate.py                 # every modelled cell (radio_coverage_r7.parquet)
    python scripts/radio/validate.py --region Surrey

Ookla gives download speed, not signal strength, and mixes every operator, band and time of day, so this
checks whether stronger estimated signal goes with faster measured speeds, not the dBm values themselves.
Breakdowns: by Hata environment (from people per km2) and by terrain, using los_r7 (line of sight from the
cell centre to its nearest site; built independently of the model) and the model's own diffraction loss.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coverage_detective.config import PROCESSED, ROOT  # noqa: E402

DOC = ROOT / "docs" / "radio_validation.md"
MIN_TESTS = 10


def load(region: str | None) -> pd.DataFrame:
    r = pd.read_parquet(PROCESSED / "radio_coverage_r7.parquet")
    if region:
        r = r[r.region == region]
    o = pd.read_parquet(PROCESSED / "ookla_mobile_r7.parquet", columns=["cell_id", "download_mbps", "tests", "quarter"])
    los = pd.read_parquet(PROCESSED / "los_r7.parquet", columns=["cell_id", "los_class"])
    m = pd.read_parquet(PROCESSED / "masts_r7.parquet", columns=["cell_id", "nearest_mast_km"])
    c = pd.read_parquet(PROCESSED / "cells_r7.parquet", columns=["cell_id", "population"])
    return r.merge(o, on="cell_id", how="left").merge(los, on="cell_id", how="left") \
        .merge(m, on="cell_id", how="left").merge(c, on="cell_id", how="left")


def spearman(x, y) -> tuple[float, float, float]:
    """rho and its 95% interval (Fisher z)."""
    rho = stats.spearmanr(x, y).statistic
    z, se = np.arctanh(rho), 1.06 / np.sqrt(max(len(x) - 3, 1))
    return rho, np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)


def fmt_rho(x, y) -> str:
    rho, lo, hi = spearman(x, y)
    return f"{rho:+.2f} ({lo:+.2f} to {hi:+.2f})"


def factor(log_err) -> str:
    return f"x{10 ** np.abs(log_err):.1f}"


def correlation_rows(df: pd.DataFrame, good: pd.DataFrame) -> list[list]:
    rows = []
    groups = [(r, df[df.region == r], good[good.region == r]) for r in sorted(df.region.unique())]
    if len(groups) > 1:
        groups.append(("all", df, good))
    for name, d, g in groups:
        t1 = d[d.tests >= 1]
        rows.append([name, len(g), fmt_rho(g.best_rsrp_dbm, g.download_mbps),
                     fmt_rho(t1.best_rsrp_dbm, t1.download_mbps), fmt_rho(-g.nearest_mast_km, g.download_mbps),
                     fmt_rho(g.population, g.download_mbps)])
    return rows


def table(rows: list[list], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region")
    args = parser.parse_args()
    df = load(args.region)
    regions = ", ".join(sorted(df.region.unique()))
    cfg = json.loads((PROCESSED / "radio_config.json").read_text())
    tested = df[df.tests >= 1].copy()
    good = df[df.tests >= MIN_TESTS].copy()
    good["log_speed"] = np.log10(good.download_mbps)
    fit = stats.linregress(good.best_rsrp_dbm, good.log_speed)
    good["resid"] = good.log_speed - (fit.intercept + fit.slope * good.best_rsrp_dbm)
    good["terrain"] = np.where(good.best_diffraction_db >= 6, "diffraction >= 6 dB", "diffraction < 6 dB")

    def breakdown(col: str, order: list[str]) -> str:
        rows = []
        for reg in sorted(good.region.unique()):
            for k in order:
                g = good[(good[col] == k) & (good.region == reg)]
                if len(g) < 3:
                    rows.append([reg, k, len(g), "", "", "", ""])
                    continue
                rows.append([reg, k, len(g), f"{g.best_rsrp_dbm.median():.0f}", f"{g.download_mbps.median():.1f}",
                             f"{g.resid.median():+.2f} ({factor(g.resid.median())})",
                             f"{g.resid.abs().median():.2f} ({factor(g.resid.abs().median())})"])
        return table(rows, ["region", col.replace("_", " "), "cells", "median RSRP (dBm)", "median Mbps",
                            "median residual, log10 (as a factor)", "median absolute residual"])

    weak = good.best_rsrp_dbm <= -100
    worst_over = good.nsmallest(8, "resid")
    worst_under = good.nlargest(5, "resid")

    def cell_rows(g):
        return [[r.cell_id, r.region, r.environment, f"{r.best_rsrp_dbm:.0f}", r.best_operator, f"{r.best_distance_km:.2f}",
                 f"{r.best_diffraction_db:.0f}", f"{r.download_mbps:.1f}", int(r.tests), r.los_class or ""]
                for r in g.itertuples()]

    cell_head = ["cell", "region", "environment", "RSRP (dBm)", "best server", "km", "diffraction (dB)", "Ookla Mbps",
                 "tests", "line of sight (los_r7)"]
    slow = good[good.download_mbps < 5]
    slow_text = (f"; the model's median RSRP there is {slow.best_rsrp_dbm.median():.0f} dBm (all cells: "
                 f"{good.best_rsrp_dbm.median():.0f} dBm)" if len(slow) else "")

    text = f"""# Radio model validation

Generated by `scripts/radio/validate.py` on {date.today().isoformat()} (model `{cfg['model_version']}`;
region{'s' if ',' in regions else ''}: {regions}). Surrey is the pilot; Westmorland and Furness (the Lake
District) is added as a hilly test of the diffraction term. The model is described in [radio_model.md](radio_model.md).
These are **model estimates, not measurements**.

## What is compared

Estimated best-server RSRP at each H3 r7 cell centre (receiver {cfg['rx_height_m']} m, 800 MHz, omni
antennas at {cfg['antenna_height_m']:g} m unless Ofcom's register gives a height) against Speedtest by Ookla's
mean mobile download speed in the cell ({good.quarter.dropna().iloc[0] if len(good) else 'n/a'}, all operators). Ookla measures
speed, not signal: speed also depends on the operator, band, 5G, load, the phone and time of day, so a good
model should rank cells in the right order, and no dBm-to-Mbps match is expected.

Cells modelled: {len(df):,}; with Ookla tests: {len(tested):,}; with at least {MIN_TESTS} tests (used below):
{len(good):,}.

## Correlation

Spearman rank correlation with download speed (95% interval), per region and for all modelled cells. The
baseline is minus the distance to the nearest site of any operator; population is a proxy for how much the
networks have invested in a place.

{table(correlation_rows(df, good), ["region", f"cells with >= {MIN_TESTS} tests", "model RSRP",
                                    "model RSRP, >= 1 test", "baseline: distance", "population"])}

Least-squares fit on all cells with >= {MIN_TESTS} tests: log10(Mbps) = {fit.intercept:.2f} + {fit.slope:.4f} x RSRP
(r = {fit.rvalue:+.2f}; {fit.slope * 10:+.2f} in log10 per 10 dB, i.e. x{10 ** (fit.slope * 10):.2f} speed per 10 dB).
Residuals below are measured minus fitted, in log10 Mbps (+0.30 = twice as fast as the fit predicts).

Cells the model puts at or below -100 dBm: {int(weak.sum())} (median {good[weak].download_mbps.median():.1f} Mbps)
against {int((~weak).sum())} above (median {good[~weak].download_mbps.median():.1f} Mbps).
Cells Ookla measures below 5 Mbps: {len(slow)}{slow_text}.

## By environment

{breakdown("environment", ["urban", "suburban", "open"])}

## By terrain

Line of sight from the cell centre to its nearest site (los_r7, independent of the model):

{breakdown("los_class", ["clear", "marginal", "blocked"])}

The model's own diffraction loss on the best-server path:

{breakdown("terrain", ["diffraction < 6 dB", "diffraction >= 6 dB"])}

## Where the model does badly

Strong estimated signal but slow measured speed (largest negative residuals):

{table(cell_rows(worst_over), cell_head)}

Weak estimated signal but fast measured speed (largest positive residuals):

{table(cell_rows(worst_under), cell_head)}

## Reading these results

**In short: so far the model does not rank cells by measured speed better than the distance to the nearest
site alone.** Overall, the model's rank correlation is {fmt_rho(good.best_rsrp_dbm, good.download_mbps)}, the
distance baseline's {fmt_rho(-good.nearest_mast_km, good.download_mbps)}. Where it does badly, and why:

- Hata's open-area correction (about 28 dB less loss than urban at 800 MHz) makes rural cells look strong:
  open cells have the highest estimated signal but the slowest speeds, in both regions. Rural speeds are
  limited by capacity, band and backhaul, which the model does not know.
- Terrain matters where the model misses it: in Westmorland and Furness the few cells blocked on los_r7 are
  the slowest group, but the model finds >= 6 dB of diffraction on only a handful of tested cells, because its
  best server is usually another, closer site with a clear path (often an OpenCelliD cluster).
- The signal estimate is dominated by the distance from the cell centre to the nearest labelled site.
  OpenCelliD clusters sit where phones reported a cell, not always on the mast, so a cell with a cluster
  near its centre gets a very strong estimate (up to about -30 dBm) whatever its real service.
- Ookla tests come from where people are, which are mostly valleys and towns near sites, so few tested cells
  are blocked even in the Lake District, and no cell with {MIN_TESTS}+ tests measures under 5 Mbps in either
  region. Most of Surrey is gently rolling, so terrain rarely decides the result there.
- Speed depends on capacity and on which networks have 5G and wide bands, none of which the model knows. Busy
  town centres can have a strong signal and modest speeds, and the reverse holds on quiet rural cells.
- Omni antennas at full gain in every direction overstate signal near sites (no downtilt or sector pattern),
  and one band per operator (800 MHz) ignores 1800 / 2100 / 2600 MHz and 5G.
- No buildings yet: the obstruction input is off (default). Hata's environment correction is the only
  allowance for towns.
- Ookla tests are crowd-sourced, so cells with few tests are noisy and indoor tests are mixed with outdoor.
"""
    DOC.write_text(text)
    print(f"wrote {DOC.relative_to(ROOT)}: {len(good)} cells with >= {MIN_TESTS} tests; "
          f"Spearman {fmt_rho(good.best_rsrp_dbm, good.download_mbps)}; "
          f"baseline distance {fmt_rho(-good.nearest_mast_km, good.download_mbps)}")


if __name__ == "__main__":
    main()
