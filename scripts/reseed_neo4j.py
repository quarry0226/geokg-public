"""Full Neo4j reseed using seed_data + Builder.

Drops the target Neo4j database, builds the canonical TL_SGCO_RNADR_MST +
총괄표제부 + TN_RODWAY + TI_SPOT graph, and reports the final node /
relationship counts and per-phase timing.

Two seed sources are supported:

  --from raw         Read SHP/XLSX from ./data/ (requires the 1.4 GB raw
                     KAIS download per docs/DATA_DOWNLOAD.md).
  --from processed   Reload the gzipped JSON dumps under
                     ./data/processed/<region>.json.gz produced by
                     `scripts/dump_processed_data.py`. ~41 MiB total, no
                     SHP library needed.

The script auto-detects: if ``data/processed/<region>.json.gz`` exists,
it prefers the processed dump; otherwise it falls back to ``--from raw``.

Usage:
    python scripts/reseed_neo4j.py yuseong
    python scripts/reseed_neo4j.py sejong
    python scripts/reseed_neo4j.py both                    # both in sequence
    python scripts/reseed_neo4j.py yuseong --from raw
    python scripts/reseed_neo4j.py yuseong --from processed
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# UTF-8 stdout on Windows consoles
if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_scene(region: str, source: str) -> dict:
    """Return the in-memory scene dict, either from processed dump or raw SHP."""
    processed_path = ROOT / "data" / "processed" / f"{region}.json.gz"

    if source == "raw":
        from backend.data.seed_data import generate_scene_data
        return generate_scene_data(region=region)

    if source == "processed":
        if not processed_path.exists():
            raise SystemExit(
                f"[reseed] --from processed requested but {processed_path} is missing.\n"
                f"          Run `python scripts/dump_processed_data.py {region}` first,\n"
                f"          or pass --from raw to read SHP/XLSX directly."
            )
        print(f"[reseed] Loading processed dump: {processed_path.relative_to(ROOT)}")
        from scripts.dump_processed_data import load_processed
        return load_processed(region)

    # auto: prefer processed if present
    if processed_path.exists():
        print(f"[reseed] Loading processed dump: {processed_path.relative_to(ROOT)}")
        from scripts.dump_processed_data import load_processed
        return load_processed(region)
    print(f"[reseed] No processed dump found at {processed_path};"
          f" falling back to raw SHP pipeline.")
    from backend.data.seed_data import generate_scene_data
    return generate_scene_data(region=region)


def run_region(region: str, source: str = "auto") -> dict:
    """Clear DB for region, run seed, return summary metrics."""
    print("\n" + "=" * 78)
    print(f" REGION: {region.upper()} — Full reseed (source = {source})")
    print("=" * 78)

    # Late imports so set_region is honoured before py2neo Graph builds
    from backend.db.neo4j_client import db, set_region
    from backend.geokg.builder import GeoKGBuilder

    set_region(region)
    try:
        db.ensure_database()
    except Exception as e:
        print(f"[reseed] WARNING: ensure_database failed: {e}")

    builder = GeoKGBuilder()
    builder.initialize()
    print(f"[reseed] Clearing existing data for {region}...")
    builder.clear()
    pre_stats = db.stats()
    print(f"  [DB] cleared. pre-build stats: {pre_stats}")

    # 1. Load scene data (raw SHP or processed dump)
    t_seed = time.time()
    scene = _load_scene(region, source)
    seed_secs = time.time() - t_seed

    # 2. Build into Neo4j (rule engine runs here)
    t_build = time.time()
    timing = builder.build_from_data(scene)
    build_secs = time.time() - t_build

    post_stats = db.stats()
    metrics = scene["_metrics"]

    summary = {
        "region": region,
        "region_label": scene["region_label"],
        "area_km2": scene["area_km2"],
        "population_estimate": scene["population_estimate"],
        "metrics": metrics,
        "neo4j_stats": post_stats,
        "timing_sec": {
            "data_preparation_total": round(seed_secs, 2),
            "neo4j_build_total": round(build_secs, 2),
            "rule_engine": timing.get("rule_engine", {}),
            "neo4j_build_breakdown": timing.get("neo4j_build", {}),
            "data_preparation_breakdown": timing.get("data_preparation", {}),
        },
        "seed_source": source,
    }

    out_dir = ROOT / "backend" / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"reseed_neo4j_{region}_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n[reseed] Saved summary → {out_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("region", nargs="?", default="yuseong",
                        choices=["yuseong", "sejong", "both"],
                        help="Region to reseed (default: yuseong)")
    parser.add_argument("--from", dest="source", default="auto",
                        choices=["auto", "raw", "processed"],
                        help="Seed source: auto (prefer processed if present), raw, or processed")
    args = parser.parse_args()

    targets = ["yuseong", "sejong"] if args.region == "both" else [args.region]
    all_results = {}
    for r in targets:
        all_results[r] = run_region(r, source=args.source)

    if len(targets) == 2:
        print("\n" + "=" * 78)
        print(" SIDE-BY-SIDE COMPARISON (Yuseong vs Sejong)")
        print("=" * 78)
        y = all_results["yuseong"]
        s = all_results["sejong"]
        ym = y["metrics"]
        sm = s["metrics"]
        keys = [
            "n_buildings", "n_parcels", "n_roads",
            "n_intersections", "n_auto_road_links",
            "n_shelters", "n_transit", "n_parks", "n_monitor",
            "entrance_match_rate_pct", "summary_attribute_match_rate_pct",
        ]
        print(f"{'metric':<35s} {'Yuseong':>12s} {'Sejong':>12s}")
        for k in keys:
            print(f"  {k:<33s} {str(ym.get(k, '-')):>12s} {str(sm.get(k, '-')):>12s}")

        print()
        print("Neo4j stats:")
        print(f"  Yuseong: {y['neo4j_stats']}")
        print(f"  Sejong:  {s['neo4j_stats']}")
        print()
        print("Total wall-time:")
        print(f"  Yuseong: seed={y['timing_sec']['data_preparation_total']:.1f}s "
              f"build={y['timing_sec']['neo4j_build_total']:.1f}s")
        print(f"  Sejong:  seed={s['timing_sec']['data_preparation_total']:.1f}s "
              f"build={s['timing_sec']['neo4j_build_total']:.1f}s")

        out_dir = ROOT / "backend" / "experiments" / "results"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        combined = out_dir / f"reseed_neo4j_combined_{stamp}.json"
        with open(combined, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"\nCombined summary → {combined}")


if __name__ == "__main__":
    main()
