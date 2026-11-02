# Coverage Detective

Open, statistics-based analysis of UK mobile coverage (4G/5G, phones and mobile data). For any point in the UK it
gathers public evidence (measured speeds, approximate mast sites, terrain line of sight, Ofcom area figures,
current weather and flood warnings) and scores the likely reasons the signal is poor with fixed, documented rules.
For any area it ranks under-served zones and suggests a rule-based fix. Every figure comes from code and SQL, with
its source and date. No language model and no API keys.

> This README is being rewritten as the project moves to a terrain-aware radio propagation model and a multi-scale
> map. Details: [docs/setup.md](docs/setup.md), [docs/usage.md](docs/usage.md), [docs/data.md](docs/data.md).

## Try it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --sample          # offline, on the committed extracts: http://localhost:8000/signal
.venv/bin/python -m pytest -q             # offline tests
```

Sample mode covers a few example places (Great Langdale, CO9 1TP, central London, near Rhayader, Sutton St Edmund)
and zones. For the whole UK, build the data ([docs/setup.md](docs/setup.md)) and run `python run.py`.

## How it works

- **Data layer**: DuckDB over Parquet files built from public data (`coverage_detective/data/db.py`).
- **Evidence** for a point (`coverage_detective/evidence.py`): four logged SQL queries, line of sight over the
  Copernicus DEM to each operator's nearest site, and live keyless sources.
- **Diagnosis** (`coverage_detective/diagnosis.py`): each cause from a fixed set (terrain blocking, distance to
  mast, single operator, capacity strain, weather, active incident, insufficient data) gets a rule score from the
  evidence; weak evidence caps a score.
- **Zones** (`coverage_detective/planning/`): gap zones and improvement opportunities, a transparent priority
  score with adjustable weights, and rule-based fixes with candidate sites checked by line of sight.

## Data and licences

Speedtest by Ookla open data is CC BY-NC-SA 4.0, so Ookla-derived outputs are for **non-commercial use only**.
Other sources: OpenCelliD (CC BY-SA 4.0), OpenStreetMap (ODbL), Ofcom (Connected Nations, Wireless Telegraphy
Register), ONS and OS OpenData (OGL v3), WorldPop (CC BY 4.0), Copernicus DEM GLO-30. Our code is [MIT](LICENSE);
see [THIRD_PARTY.md](THIRD_PARTY.md).

## Limitations

- Causes are inferred from public data, not confirmed: operators' network data is not public.
- Mast data is crowd-sourced and incomplete, and positions are approximate; "no site found" is weak evidence.
- Antenna heights are mostly assumed (25 m); the terrain model is a surface model; no building heights yet.
- Ookla speeds are quarterly aggregates with no time-of-day detail.
- No address-level Ofcom coverage; use Ofcom's [mobile coverage checker](https://www.ofcom.org.uk/mobile-coverage-checker).
