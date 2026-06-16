"""
Reviewer-response data extraction for k-GeoKG paper revision.

Generates evidence for reviewer comments:
  R2.5  — FRONTS_ROAD distance distribution (>500m count, percentiles)
  R2.11 — Centroid-to-polyline overestimation factor (shapely re-computation)
  R2.12 — Spatial distribution of buildings excluded due to missing attributes
  R2.14 — Concrete count of "critical" entities by definition
  R1.6  — Quantitative quality metrics for 100% completeness claim
  R2.10 — Per-strategy cost discussion (uses existing exp1 data)

Usage:
  python -m backend.experiments.reviewer_response --section all
  python -m backend.experiments.reviewer_response --section fronts_road
  python -m backend.experiments.reviewer_response --section excluded_bldg
"""

import sys
import os
import json
import math
import argparse
import statistics
from datetime import datetime
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from backend.db.neo4j_client import db

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def _save(name, data):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"reviewer_{name}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  saved: {path}")
    return path


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    lon1, lat1, lon2, lat2 = map(math.radians, (lon1, lat1, lon2, lat2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ───────────────────────────────────────────────────────────────────
# Section A: FRONTS_ROAD distance distribution (R2.5)
# ───────────────────────────────────────────────────────────────────

def section_fronts_road_distance():
    """Compute distance distribution for FRONTS_ROAD edges using stored coords."""
    print("=" * 70)
    print("  Section A: FRONTS_ROAD distance distribution (R2.5)")
    print("=" * 70)

    # Pull all FRONTS_ROAD edges with both endpoints' coordinates
    rows = db.query(
        """
        MATCH (b:Building)-[:FRONTS_ROAD]->(r:Road)
        WHERE b.longitude IS NOT NULL AND b.latitude IS NOT NULL
          AND r.longitude IS NOT NULL AND r.latitude IS NOT NULL
        RETURN b.uid AS b_uid, b.longitude AS b_lon, b.latitude AS b_lat,
               r.uid AS r_uid, r.longitude AS r_lon, r.latitude AS r_lat
        """
    )

    distances = []
    pairs = []
    for row in rows:
        d = haversine_m(row["b_lon"], row["b_lat"], row["r_lon"], row["r_lat"])
        distances.append(d)
        pairs.append({"b_uid": row["b_uid"], "r_uid": row["r_uid"], "dist_m": d})

    if not distances:
        print("  No FRONTS_ROAD edges with coordinates found.")
        return None

    distances.sort()
    n = len(distances)

    def pct(p):
        idx = min(n - 1, max(0, int(round(p / 100.0 * (n - 1)))))
        return round(distances[idx], 1)

    buckets = [
        (0, 50, "<50m"),
        (50, 100, "50-100m"),
        (100, 200, "100-200m"),
        (200, 500, "200-500m"),
        (500, 1000, "500-1000m"),
        (1000, float("inf"), ">1000m"),
    ]
    bucket_counts = [(label, sum(1 for d in distances if lo <= d < hi)) for lo, hi, label in buckets]

    over_500 = sum(1 for d in distances if d > 500)
    over_1000 = sum(1 for d in distances if d > 1000)

    result = {
        "section": "fronts_road_distance",
        "addresses": ["R2.5", "R1.5", "R1.6"],
        "timestamp": datetime.now().isoformat(),
        "total_edges": n,
        "stats_m": {
            "mean": round(sum(distances) / n, 1),
            "stdev": round(statistics.stdev(distances) if n > 1 else 0, 1),
            "min": round(distances[0], 1),
            "p25": pct(25),
            "median": pct(50),
            "p75": pct(75),
            "p90": pct(90),
            "p95": pct(95),
            "p99": pct(99),
            "max": round(distances[-1], 1),
        },
        "buckets": dict(bucket_counts),
        "over_500m": {"count": over_500, "pct": round(over_500 / n * 100, 2)},
        "over_1000m": {"count": over_1000, "pct": round(over_1000 / n * 100, 2)},
    }

    print(f"\n  Total FRONTS_ROAD edges with coords: {n:,}")
    print(f"  Distance stats (m): mean={result['stats_m']['mean']:.1f} median={result['stats_m']['median']:.1f}")
    print(f"  Percentiles:  p25={result['stats_m']['p25']}, p90={result['stats_m']['p90']}, p99={result['stats_m']['p99']}, max={result['stats_m']['max']}")
    print(f"  >500m:  {over_500:,} ({result['over_500m']['pct']}%)")
    print(f"  >1000m: {over_1000:,} ({result['over_1000m']['pct']}%)")
    print(f"\n  Bucket distribution:")
    for label, count in bucket_counts:
        pct_v = count / n * 100
        bar = "█" * int(pct_v / 2)
        print(f"    {label:<12s} {count:>7,} ({pct_v:5.1f}%) {bar}")

    _save("fronts_road_distance", result)
    return result


# ───────────────────────────────────────────────────────────────────
# Section B: Centroid → polyline overestimation factor (R2.11)
# ───────────────────────────────────────────────────────────────────

def section_polyline_overestimation(sample_n=200, seed=42):
    """Recompute FRONTS_ROAD distance using actual polyline geometry via shapely.

    The current FRONTS_ROAD uses Building centroid → Road representative point.
    This recomputes Building centroid → nearest point on Road polyline.
    """
    print("=" * 70)
    print("  Section B: Centroid→polyline overestimation factor (R2.11)")
    print("=" * 70)

    try:
        from shapely.geometry import Point, LineString
    except ImportError:
        print("  Shapely not installed; skipping.")
        return None

    import random
    random.seed(seed)

    # Sample FRONTS_ROAD edges with road polyline
    rows = db.query(
        """
        MATCH (b:Building)-[:FRONTS_ROAD]->(r:Road)
        WHERE b.longitude IS NOT NULL AND b.latitude IS NOT NULL
          AND r.coordinates IS NOT NULL
        RETURN b.uid AS b_uid, b.longitude AS b_lon, b.latitude AS b_lat,
               r.uid AS r_uid, r.longitude AS r_lon, r.latitude AS r_lat,
               r.coordinates AS r_coords
        """
    )

    if len(rows) < sample_n:
        print(f"  Only {len(rows)} edges available, using all.")
        sample = rows
    else:
        sample = random.sample(rows, sample_n)

    pairwise = []
    for row in sample:
        try:
            coords = json.loads(row["r_coords"]) if isinstance(row["r_coords"], str) else row["r_coords"]
            if not coords or len(coords) < 2:
                continue
            line = LineString(coords)
            pt = Point(row["b_lon"], row["b_lat"])
            # Project to nearest point on line (in degree space, then convert)
            projected = line.interpolate(line.project(pt))
            d_polyline = haversine_m(row["b_lon"], row["b_lat"], projected.x, projected.y)
            d_centroid = haversine_m(row["b_lon"], row["b_lat"], row["r_lon"], row["r_lat"])
            ratio = d_centroid / d_polyline if d_polyline > 0.5 else None
            pairwise.append({
                "b_uid": row["b_uid"],
                "r_uid": row["r_uid"],
                "centroid_dist_m": round(d_centroid, 2),
                "polyline_dist_m": round(d_polyline, 2),
                "ratio": round(ratio, 3) if ratio else None,
            })
        except Exception as e:
            continue

    if not pairwise:
        print("  No valid pairs computed.")
        return None

    centroid = [p["centroid_dist_m"] for p in pairwise]
    polyline = [p["polyline_dist_m"] for p in pairwise]
    ratios = [p["ratio"] for p in pairwise if p["ratio"] is not None]

    centroid.sort()
    polyline.sort()

    result = {
        "section": "polyline_overestimation",
        "addresses": ["R2.11"],
        "timestamp": datetime.now().isoformat(),
        "sample_size": len(pairwise),
        "centroid_distance_m": {
            "mean": round(sum(centroid) / len(centroid), 1),
            "median": round(centroid[len(centroid) // 2], 1),
            "max": round(max(centroid), 1),
        },
        "polyline_distance_m": {
            "mean": round(sum(polyline) / len(polyline), 1),
            "median": round(polyline[len(polyline) // 2], 1),
            "max": round(max(polyline), 1),
        },
        "overestimation_ratio": {
            "n": len(ratios),
            "mean": round(sum(ratios) / len(ratios), 2) if ratios else None,
            "median": round(sorted(ratios)[len(ratios) // 2], 2) if ratios else None,
            "stdev": round(statistics.stdev(ratios), 2) if len(ratios) > 1 else None,
        },
        "samples": pairwise[:30],
    }

    print(f"\n  Sample: {len(pairwise)}")
    print(f"  Centroid→Centroid (current):  mean={result['centroid_distance_m']['mean']:.1f}m median={result['centroid_distance_m']['median']:.1f}m")
    print(f"  Centroid→Polyline (correct):  mean={result['polyline_distance_m']['mean']:.1f}m median={result['polyline_distance_m']['median']:.1f}m")
    print(f"  Overestimation factor: mean={result['overestimation_ratio']['mean']}× median={result['overestimation_ratio']['median']}×")

    _save("polyline_overestimation", result)
    return result


# ───────────────────────────────────────────────────────────────────
# Section C: Spatial distribution of buildings excluded (R2.12)
# ───────────────────────────────────────────────────────────────────

def section_excluded_buildings():
    """Analyze buildings without admin_dong - check for systematic bias."""
    print("=" * 70)
    print("  Section C: Excluded buildings spatial distribution (R2.12)")
    print("=" * 70)

    total = db.query("MATCH (b:Building) RETURN count(b) AS c")[0]["c"]
    with_dong = db.query(
        "MATCH (b:Building) WHERE b.admin_dong IS NOT NULL AND b.admin_dong <> '' RETURN count(b) AS c"
    )[0]["c"]
    without_dong = total - with_dong

    # Cross-tabulate missing patterns
    patterns = db.query(
        """
        MATCH (b:Building)
        WITH b,
             CASE WHEN b.admin_dong IS NOT NULL AND b.admin_dong <> '' THEN 1 ELSE 0 END AS has_dong,
             CASE WHEN b.road_address IS NOT NULL AND b.road_address <> '' THEN 1 ELSE 0 END AS has_road,
             CASE WHEN b.building_type IS NOT NULL AND b.building_type <> '' THEN 1 ELSE 0 END AS has_type
        RETURN has_dong, has_road, has_type, count(b) AS cnt
        ORDER BY has_dong, has_road, has_type
        """
    )

    # Spatial distribution: bucket by 0.01° lat/lon grid
    excluded_coords = db.query(
        """
        MATCH (b:Building)
        WHERE (b.admin_dong IS NULL OR b.admin_dong = '')
          AND b.longitude IS NOT NULL AND b.latitude IS NOT NULL
        RETURN b.longitude AS lon, b.latitude AS lat
        """
    )

    grid_excluded = Counter()
    for row in excluded_coords:
        key = (round(row["lon"], 2), round(row["lat"], 2))
        grid_excluded[key] += 1

    # Spatial distribution of all buildings for comparison
    all_coords = db.query(
        """
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL AND b.latitude IS NOT NULL
        RETURN b.longitude AS lon, b.latitude AS lat
        """
    )
    grid_all = Counter()
    for row in all_coords:
        key = (round(row["lon"], 2), round(row["lat"], 2))
        grid_all[key] += 1

    # Top concentration cells of excluded buildings
    top_excluded_cells = sorted(grid_excluded.items(), key=lambda x: -x[1])[:15]

    # Check whether excluded buildings cluster: compare ratio per cell
    cell_ratios = []
    for cell, exc_cnt in grid_excluded.items():
        all_cnt = grid_all.get(cell, exc_cnt)
        if all_cnt >= 50:  # only meaningful cells
            cell_ratios.append({"cell": cell, "excluded": exc_cnt, "all": all_cnt, "ratio": round(exc_cnt / all_cnt, 3)})
    cell_ratios.sort(key=lambda x: -x["ratio"])

    result = {
        "section": "excluded_buildings",
        "addresses": ["R2.12", "R2.7"],
        "timestamp": datetime.now().isoformat(),
        "total_buildings": total,
        "with_admin_dong": with_dong,
        "without_admin_dong": without_dong,
        "exclusion_ratio_pct": round(without_dong / total * 100, 2) if total else 0,
        "missing_attribute_patterns": [
            {"has_dong": p["has_dong"], "has_road": p["has_road"], "has_type": p["has_type"], "count": p["cnt"]}
            for p in patterns
        ],
        "top_excluded_cells_001deg": [
            {"lon": c[0], "lat": c[1], "count": v} for c, v in top_excluded_cells
        ],
        "cells_with_high_exclusion_ratio": cell_ratios[:15],
        "spatial_summary": {
            "total_excluded_with_coords": len(excluded_coords),
            "total_with_coords": len(all_coords),
            "n_unique_cells_excluded": len(grid_excluded),
            "n_unique_cells_all": len(grid_all),
        },
    }

    print(f"\n  Total buildings:        {total:,}")
    print(f"  With admin_dong:        {with_dong:,} ({with_dong/total*100:.1f}%)")
    print(f"  Without admin_dong:     {without_dong:,} ({without_dong/total*100:.1f}%)")
    print(f"\n  Missing attribute patterns (admin_dong/road/type):")
    for p in patterns:
        flags = f"{p['has_dong']}{p['has_road']}{p['has_type']}"
        print(f"    {flags}: {p['cnt']:,}")
    print(f"\n  Spatial dispersion of excluded:")
    print(f"    Excluded buildings span {len(grid_excluded)} unique 0.01° cells (out of {len(grid_all)} cells with any building)")

    _save("excluded_buildings", result)
    return result


# ───────────────────────────────────────────────────────────────────
# Section D: Critical entity counts (R2.14)
# ───────────────────────────────────────────────────────────────────

def section_critical_entities():
    """Provide concrete counts for the 'critical' entity definition."""
    print("=" * 70)
    print("  Section D: Critical entity definitions and counts (R2.14)")
    print("=" * 70)

    counts = {}

    # Sensors, Cameras, Facilities, ParkingLots are all "critical infrastructure"
    for label in ["Sensor", "Camera", "Facility", "ParkingLot"]:
        c = db.query(f"MATCH (n:{label}) RETURN count(n) AS c")
        counts[label] = c[0]["c"] if c else 0

    # ThingsAddr split by iot_type
    iot_breakdown = db.query(
        "MATCH (n:ThingsAddr) RETURN n.iot_type AS t, count(n) AS c ORDER BY c DESC"
    )
    counts["ThingsAddr_total"] = sum(r["c"] for r in iot_breakdown)
    counts["ThingsAddr_by_type"] = {r["t"]: r["c"] for r in iot_breakdown}

    # Critical: emergency-relevant subset
    critical_iot_types = ["CivilDefense", "EQOUT", "CoolingCen"]
    counts["ThingsAddr_critical"] = sum(
        counts["ThingsAddr_by_type"].get(t, 0) for t in critical_iot_types
    )

    # Aggregated definitions used by /api/kg/road_impact
    counts["critical_def1_emergency"] = (
        counts["Sensor"] + counts["Camera"]
        + counts["Facility"] + counts["ParkingLot"]
        + counts["ThingsAddr_critical"]
    )

    counts["critical_def2_all_infra"] = (
        counts["Sensor"] + counts["Camera"]
        + counts["Facility"] + counts["ParkingLot"]
        + counts["ThingsAddr_total"]
    )

    print(f"\n  Critical entity counts:")
    print(f"    Sensor:      {counts['Sensor']:,}")
    print(f"    Camera:      {counts['Camera']:,}")
    print(f"    Facility:    {counts['Facility']:,}")
    print(f"    ParkingLot:  {counts['ParkingLot']:,}")
    print(f"    ThingsAddr (total):    {counts['ThingsAddr_total']:,}")
    print(f"    ThingsAddr (critical CivilDefense+EQOUT+CoolingCen): {counts['ThingsAddr_critical']:,}")
    print(f"\n  Definition 1 (emergency-only): {counts['critical_def1_emergency']:,}")
    print(f"  Definition 2 (all infra):      {counts['critical_def2_all_infra']:,}")
    print(f"\n  ThingsAddr breakdown by iot_type:")
    for t, c in counts["ThingsAddr_by_type"].items():
        print(f"    {t:<25s} {c:,}")

    result = {
        "section": "critical_entities",
        "addresses": ["R2.14"],
        "timestamp": datetime.now().isoformat(),
        "counts": counts,
    }
    _save("critical_entities", result)
    return result


# ───────────────────────────────────────────────────────────────────
# Section E: Building-road completeness quality (R1.6)
# ───────────────────────────────────────────────────────────────────

def section_road_completeness_quality():
    """Quantify the 100% claim with quality metrics."""
    print("=" * 70)
    print("  Section E: Building-road completeness quality (R1.6, R2.5)")
    print("=" * 70)

    total = db.query("MATCH (b:Building) RETURN count(b) AS c")[0]["c"]
    on_street = db.query(
        "MATCH (b:Building)-[:ON_STREET]->() RETURN count(DISTINCT b) AS c"
    )[0]["c"]
    fronts_only = db.query(
        """
        MATCH (b:Building)-[:FRONTS_ROAD]->()
        WHERE NOT (b)-[:ON_STREET]->()
        RETURN count(DISTINCT b) AS c
        """
    )[0]["c"]
    any_link = db.query(
        """
        MATCH (b:Building)
        WHERE (b)-[:ON_STREET]->() OR (b)-[:FRONTS_ROAD]->()
        RETURN count(DISTINCT b) AS c
        """
    )[0]["c"]

    # FRONTS_ROAD distance categories using stored coords
    edges = db.query(
        """
        MATCH (b:Building)-[:FRONTS_ROAD]->(r:Road)
        WHERE b.longitude IS NOT NULL AND r.longitude IS NOT NULL
          AND NOT (b)-[:ON_STREET]->()
        RETURN b.longitude AS bl, b.latitude AS ba,
               r.longitude AS rl, r.latitude AS ra
        """
    )

    distances = [haversine_m(e["bl"], e["ba"], e["rl"], e["ra"]) for e in edges]
    high_quality = sum(1 for d in distances if d <= 100)
    medium_quality = sum(1 for d in distances if 100 < d <= 500)
    low_quality = sum(1 for d in distances if d > 500)

    result = {
        "section": "road_completeness_quality",
        "addresses": ["R1.6", "R2.5"],
        "timestamp": datetime.now().isoformat(),
        "total_buildings": total,
        "topological_completeness": {
            "any_link": any_link,
            "completeness_pct": round(any_link / total * 100, 2),
        },
        "quality_breakdown": {
            "ON_STREET_exact_match": on_street,
            "FRONTS_ROAD_only_fallback": fronts_only,
            "FRONTS_ROAD_distance_buckets": {
                "high_quality_<=100m": high_quality,
                "medium_quality_100-500m": medium_quality,
                "low_quality_>500m": low_quality,
            },
        },
        "interpretation": (
            "100% topological completeness includes "
            f"{low_quality:,} ({low_quality/total*100:.1f}%) buildings "
            "linked to roads further than 500m (centroid-to-centroid distance), "
            "which should be reported alongside the completeness claim."
        ),
    }

    print(f"\n  Total buildings:         {total:,}")
    print(f"  Any road link:           {any_link:,} ({any_link/total*100:.2f}%)")
    print(f"    via ON_STREET:         {on_street:,}")
    print(f"    via FRONTS_ROAD only:  {fronts_only:,}")
    print(f"\n  FRONTS_ROAD-only quality breakdown ({len(distances):,} edges):")
    print(f"    high   (<=100m): {high_quality:,} ({high_quality/max(len(distances),1)*100:.1f}%)")
    print(f"    medium (100-500m): {medium_quality:,} ({medium_quality/max(len(distances),1)*100:.1f}%)")
    print(f"    low    (>500m):  {low_quality:,} ({low_quality/max(len(distances),1)*100:.1f}%)")

    _save("road_completeness_quality", result)
    return result


# ───────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────

SECTIONS = {
    "fronts_road": section_fronts_road_distance,
    "polyline": section_polyline_overestimation,
    "excluded_bldg": section_excluded_buildings,
    "critical": section_critical_entities,
    "completeness": section_road_completeness_quality,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", "-s", default="all", choices=list(SECTIONS.keys()) + ["all"])
    args = parser.parse_args()

    targets = SECTIONS.values() if args.section == "all" else [SECTIONS[args.section]]
    for fn in targets:
        try:
            fn()
        except Exception as e:
            print(f"\n[ERROR in {fn.__name__}]: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
