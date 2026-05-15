"""Build Yuseong vs Sejong side-by-side comparison + density-normalised tables.

Reads the most-recent reseed_inmem JSON files (Y + S) and prints
markdown-ready tables for paper Table 19 and a normalised density table.

Usage:
    python scripts/compare_regions.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent


def latest(pattern: str) -> Path | None:
    cands = sorted((ROOT / "backend" / "experiments" / "results").glob(pattern),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def main():
    yuseong_p = latest("reseed_inmem_yuseong_*.json")
    sejong_p  = latest("reseed_inmem_sejong_*.json")
    if not yuseong_p or not sejong_p:
        print(f"Missing files. Yuseong={yuseong_p}, Sejong={sejong_p}")
        sys.exit(1)

    with open(yuseong_p, encoding="utf-8") as f:
        y = json.load(f)
    with open(sejong_p, encoding="utf-8") as f:
        s = json.load(f)

    print(f"Yuseong file: {yuseong_p.name}")
    print(f"Sejong  file: {sejong_p.name}")

    print("\n" + "=" * 78)
    print(" TABLE A: Node counts (paper §IV.A / Table 12)")
    print("=" * 78)
    print(f"{'Label':<25s} {'Yuseong':>12s} {'Sejong':>12s}  {'Ratio S/Y':>10s}")
    print("-" * 65)
    for k in y["node_counts"]:
        yv = y["node_counts"].get(k, 0)
        sv = s["node_counts"].get(k, 0)
        ratio = (sv / yv) if yv else 0
        print(f"  {k:<23s} {yv:>12,} {sv:>12,}  {ratio:>10.2f}")
    print("-" * 65)
    yt = y["total_nodes"]; st = s["total_nodes"]
    print(f"  {'TOTAL':<23s} {yt:>12,} {st:>12,}  {st/yt:>10.2f}")

    print("\n" + "=" * 78)
    print(" TABLE B: Per-rule edge counts (paper Table 19)")
    print("=" * 78)
    print(f"{'Rule':<30s} {'Yuseong':>12s} {'Sejong':>12s}")
    print("-" * 56)
    for k in sorted(y["relationship_counts"]):
        if k == "__TOTAL__":
            continue
        yv = y["relationship_counts"].get(k, 0)
        sv = s["relationship_counts"].get(k, 0)
        print(f"  {k:<28s} {yv:>12,} {sv:>12,}")
    print("-" * 56)
    yr = y["relationship_counts"]["__TOTAL__"]
    sr = s["relationship_counts"]["__TOTAL__"]
    print(f"  {'TOTAL RELATIONSHIPS':<28s} {yr:>12,} {sr:>12,}")

    print("\n" + "=" * 78)
    print(" TABLE C: Density-normalised metrics")
    print("=" * 78)
    print(f"{'Metric':<32s} {'Yuseong':>14s} {'Sejong':>14s}")
    print("-" * 62)
    y_area = y["area_km2"]; s_area = s["area_km2"]
    y_pop = y["population_estimate"]; s_pop = s["population_estimate"]
    y_b = y["node_counts"]["Building"]; s_b = s["node_counts"]["Building"]
    y_int = y["node_counts"]["RoadIntersection"]; s_int = s["node_counts"]["RoadIntersection"]
    y_link = y["node_counts"]["AutoRoadLink"]; s_link = s["node_counts"]["AutoRoadLink"]
    y_iot = y["node_counts"]["ThingsAddr"]; s_iot = s["node_counts"]["ThingsAddr"]

    print(f"  {'Area (km²)':<30s} {y_area:>14,.1f} {s_area:>14,.1f}")
    print(f"  {'Population (estimate)':<30s} {y_pop:>14,} {s_pop:>14,}")
    print(f"  {'Buildings':<30s} {y_b:>14,} {s_b:>14,}")
    print(f"  {'  Buildings/km²':<30s} {y_b/y_area:>14,.1f} {s_b/s_area:>14,.1f}")
    print(f"  {'Intersections':<30s} {y_int:>14,} {s_int:>14,}")
    print(f"  {'  Intersections/km²':<30s} {y_int/y_area:>14,.2f} {s_int/s_area:>14,.2f}")
    print(f"  {'AutoRoadLinks':<30s} {y_link:>14,} {s_link:>14,}")
    print(f"  {'  Links/km²':<30s} {y_link/y_area:>14,.2f} {s_link/s_area:>14,.2f}")
    print(f"  {'  Links/Building':<30s} {y_link/y_b:>14,.2f} {s_link/s_b:>14,.2f}")
    print(f"  {'ThingsAddr (safety)':<30s} {y_iot:>14,} {s_iot:>14,}")
    print(f"  {'  ThingsAddr/1000 buildings':<30s} {y_iot/y_b*1000:>14,.1f} {s_iot/s_b*1000:>14,.1f}")

    # Safety category breakdown
    print()
    print(f"  Safety-category breakdown:")
    for k in ["n_shelters", "n_transit", "n_parks", "n_monitor"]:
        yv = y["metrics"].get(k, 0)
        sv = s["metrics"].get(k, 0)
        print(f"  {k:<30s} {yv:>14,} {sv:>14,}")

    print()
    print(f"  Entrance match rate (%):")
    print(f"  {'  Yuseong':<30s} {y['metrics']['entrance_match_rate_pct']:>14,.1f}")
    print(f"  {'  Sejong':<30s} {s['metrics']['entrance_match_rate_pct']:>14,.1f}")

    print("\n" + "=" * 78)
    print(" TABLE D: Dong-level safety scores (top + bottom 5)")
    print("=" * 78)
    for region, R in [("Yuseong", y), ("Sejong", s)]:
        print(f"\n  {region} top-5 (paper weights):")
        ranked = sorted(R["dong_safety_paper_weights"].items(),
                        key=lambda x: -x[1]["safety_score"])
        for bjd, d in ranked[:5]:
            print(f"    bjd={bjd}  score={d['safety_score']:>6.2f}  buildings={d['n_buildings']:>5}  "
                  f"shelter={d['shelter_pct']:>5.1f}%  transit={d['transit_pct']:>5.1f}%")
        print(f"\n  {region} bottom-5 (paper weights):")
        for bjd, d in ranked[-5:]:
            print(f"    bjd={bjd}  score={d['safety_score']:>6.2f}  buildings={d['n_buildings']:>5}  "
                  f"shelter={d['shelter_pct']:>5.1f}%  transit={d['transit_pct']:>5.1f}%")

    print("\n" + "=" * 78)
    print(" TABLE E: Wall-time comparison")
    print("=" * 78)
    print(f"{'Phase':<30s} {'Yuseong (s)':>14s} {'Sejong (s)':>14s}")
    print("-" * 60)
    print(f"  {'data preparation':<28s} {y['timing_sec']['data_preparation_total']:>14.2f} "
          f"{s['timing_sec']['data_preparation_total']:>14.2f}")
    print(f"  {'rule engine (in-memory)':<28s} {y['timing_sec']['rule_engine_total']:>14.2f} "
          f"{s['timing_sec']['rule_engine_total']:>14.2f}")
    print(f"  {'dong safety scoring':<28s} {y['timing_sec']['dong_safety_total']:>14.2f} "
          f"{s['timing_sec']['dong_safety_total']:>14.2f}")
    print("-" * 60)
    print(f"  {'TOTAL':<28s} {y['timing_sec']['total']:>14.2f} {s['timing_sec']['total']:>14.2f}")


if __name__ == "__main__":
    main()
