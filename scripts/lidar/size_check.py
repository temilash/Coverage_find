"""How much LiDAR DSM + DTM a region needs, per nation, without downloading it.

    python scripts/lidar/size_check.py --region Guildford --res 1
    python scripts/lidar/size_check.py --region England --res 2
    python scripts/lidar/size_check.py --region UK --res 2 --mbit 50
    python scripts/lidar/size_check.py --bbox -0.62,51.20,-0.52,51.27 --res 1

Lists the tiles that intersect the region and totals their sizes: Scotland and Northern Ireland from their
catalogues (exact), Wales by HEAD request (Content-Length; beyond --max-head tiles, the mean of those checked),
England from measured sample tiles (the EA tile server sends no sizes). Wales' tiles are 1 m only and Scotland's
are 50 cm or 1 m: a 2 m run reads their overviews or resamples, so the totals there are upper bounds on transfer.
Nothing is stored except the small catalogue caches (data/cache/lidar/, data/raw/lidar_index/).
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coverage_detective.lidar import catalog as C  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", help="UK, a nation, or a local authority name (ONS Dec 2024)")
    ap.add_argument("--bbox", help="w,s,e,n in WGS84 instead of --region")
    ap.add_argument("--res", type=int, choices=(1, 2), default=2, help="England tile resolution (m)")
    ap.add_argument("--max-head", type=int, default=300, help="Wales: HEAD this many tiles, estimate the rest")
    ap.add_argument("--mbit", type=float, default=50.0, help="download speed for the time estimate (Mbit/s)")
    ap.add_argument("--check-england", action="store_true", help="ask the EA catalogue which tiles exist (small regions)")
    args = ap.parse_args()
    if not (args.region or args.bbox):
        ap.error("give --region or --bbox")

    geom, label = C.region(args.region, args.bbox)
    ns = C.nations()
    print(f"Region: {label}; England at {args.res} m\n")
    tiles: list[C.Tile] = []
    for nation, poly in ns.items():
        part = geom.intersection(poly)
        part_bng = C.to_bng(part) if not part.is_empty else None
        if part_bng is None or C.polygon_area_km2(part_bng) < 1:  # border slivers of generalised boundaries
            continue
        print(f"{nation}: {C.polygon_area_km2(part_bng):,.0f} km2 of the region")
        if nation == "England":
            t = C.england_tiles(part_bng, args.res)
            if args.check_england:
                have = C.england_available(part)
                t = [x for x in t if x.tile in have]
        elif nation == "Wales":
            t = C.wales_tiles(part_bng, args.max_head)
        elif nation == "Scotland":
            t = C.scotland_tiles(part_bng)
        else:
            t = C.ni_tiles(part if part.area < poly.area * 0.99 else None)
        tiles += t

    rows = collections.defaultdict(lambda: [0, 0, set()])
    for t in tiles:
        r = rows[(t.nation, t.dataset, t.layer)]
        r[0] += 1
        r[1] += t.size or 0
        r[2].add(t.size_how)
    print(f"\n{'nation':<17} {'dataset':<44} {'layer':<8} {'files':>7} {'size':>11}  sizes from")
    per_nation = collections.Counter()
    for (nation, dataset, layer), (n, size, how) in sorted(rows.items()):
        per_nation[nation] += size
        print(f"{nation:<17} {dataset[:44]:<44} {layer:<8} {n:>7,} {C.human(size):>11}  {'/'.join(sorted(how))}")
    print()
    for nation, size in per_nation.items():
        print(f"{nation:<17} total {C.human(size):>11}  ~{C.transfer_hours(size, args.mbit):.1f} h at {args.mbit:g} Mbit/s")
    total = sum(per_nation.values())
    print(f"{'All':<17} total {C.human(total):>11}  ~{C.transfer_hours(total, args.mbit):.1f} h at {args.mbit:g} Mbit/s")
    print("\nStreamed one tile at a time and deleted after measuring: peak disk use is one DSM + DTM pair "
          f"(largest here {C.human(max((t.size or 0) for t in tiles) * 2 if tiles else 0)}).")


if __name__ == "__main__":
    main()
