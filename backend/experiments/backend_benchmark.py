"""
Multi-backend benchmark for spatial-relationship generation (R1.4, R2.16, R2.8).

Reviewers asked for a quantitative comparison against alternative methods.
PostGIS is not installed in this environment, so we substitute three backends
that span the implementation spectrum:

  Backend A: Neo4j (current paper method)
    - Pre-materialized via the rule engine (~9 s for 90,541 NEAREST_SHELTER edges)
    - Per-query lookup is index-free O(deg(node)) traversal

  Backend B: Shapely STRtree (in-memory R-tree spatial index)
    - Builds an R-tree on Building centroids; for each ThingsAddr, query 500m
    - Equivalent to PostGIS ST_DWithin without the database overhead

  Backend C: Brute-force pairwise Haversine
    - Naive O(n*m) pairwise distance check
    - Provides a baseline lower bound on what "no spatial index" looks like

For each backend we measure:
  - Construction / index-build time
  - Query time (for a single facility-type at 500m)
  - Total wall-clock time
  - Output size (relationships / pairs found)

Usage:
  python -m backend.experiments.backend_benchmark
"""

import sys
import os
import json
import time
import math
import argparse
import statistics
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from backend.db.neo4j_client import db
from shapely.geometry import Point
from shapely.strtree import STRtree

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    lon1, lat1, lon2, lat2 = map(math.radians, (lon1, lat1, lon2, lat2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ───────────────────────────────────────────────────────────────────
# Data loading (shared across all backends)
# ───────────────────────────────────────────────────────────────────

def load_buildings():
    """Load all Building nodes with coordinates."""
    rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL AND b.latitude IS NOT NULL
        RETURN b.uid AS uid, b.longitude AS lon, b.latitude AS lat
        """
    )
    return rows


def load_shelters():
    """Load ThingsAddr nodes that are shelters (CivilDefense or EQOUT)."""
    rows = db.query(
        """
        MATCH (n:ThingsAddr)
        WHERE n.iot_type IN ['CivilDefense', 'EQOUT']
          AND n.longitude IS NOT NULL AND n.latitude IS NOT NULL
        RETURN n.uid AS uid, n.iot_type AS type, n.longitude AS lon, n.latitude AS lat
        """
    )
    return rows


# ───────────────────────────────────────────────────────────────────
# Backend A: Neo4j (already materialized)
# ───────────────────────────────────────────────────────────────────

def backend_neo4j_query():
    """Query existing pre-materialized NEAREST_SHELTER edges from Neo4j."""
    t0 = time.time()
    rows = db.query(
        """
        MATCH (b:Building)-[:NEAREST_SHELTER]->(t:ThingsAddr)
        RETURN b.uid AS b_uid, t.uid AS t_uid
        """
    )
    elapsed = time.time() - t0
    return {
        "backend": "Neo4j (materialized)",
        "construction_time_s": None,  # already constructed
        "query_time_s": round(elapsed, 4),
        "total_time_s": round(elapsed, 4),
        "result_count": len(rows),
        "notes": "Reads pre-existing NEAREST_SHELTER edges; no spatial computation at query time",
    }


def backend_neo4j_inline_query(buildings, shelters, max_targets=3, radius_m=500):
    """Re-execute the NEAREST_SHELTER 500m proximity query inline against Neo4j data already in memory.

    This isolates query computation time without using the pre-materialized result.
    """
    # We use Cypher with explicit Haversine to make the comparison fair.
    t0 = time.time()
    rows = db.query(
        """
        MATCH (b:Building), (t:ThingsAddr)
        WHERE b.longitude IS NOT NULL AND t.iot_type IN ['CivilDefense', 'EQOUT']
        WITH b, t,
             6371000 * 2 * asin(sqrt(
                 sin(radians((t.latitude - b.latitude) / 2))^2 +
                 cos(radians(b.latitude)) * cos(radians(t.latitude)) *
                 sin(radians((t.longitude - b.longitude) / 2))^2
             )) AS dist
        WHERE dist <= $r
        WITH b, t, dist
        ORDER BY b.uid, dist
        WITH b, collect({t: t.uid, d: dist})[..$k] AS top_k
        UNWIND top_k AS row
        RETURN b.uid AS b_uid, row.t AS t_uid
        """,
        r=radius_m,
        k=max_targets,
    )
    elapsed = time.time() - t0
    return {
        "backend": "Neo4j (inline Cypher Haversine)",
        "construction_time_s": None,
        "query_time_s": round(elapsed, 4),
        "total_time_s": round(elapsed, 4),
        "result_count": len(rows),
        "notes": "Re-executes the proximity query in Cypher without using pre-materialized edges",
    }


# ───────────────────────────────────────────────────────────────────
# Backend B: Shapely STRtree (R-tree spatial index)
# ───────────────────────────────────────────────────────────────────

def backend_strtree(buildings, shelters, max_targets=3, radius_m=500):
    """Build STRtree on shelters; for each building, query within radius.

    Note: shapely STRtree works in projected (Cartesian) space. We use the
    longitude/latitude degrees directly since (1) all features are in the same
    small region (Yuseong-gu, ~15km extent) and (2) we recompute Haversine
    distance for the actual radius check after the R-tree filter. The R-tree
    is only used as a coarse pre-filter.
    """
    # Convert radius_m to approximate degrees (1 deg lat ≈ 111 km, 1 deg lon at 36° ≈ 90 km)
    bbox_deg_lat = radius_m / 111000.0
    bbox_deg_lon = radius_m / (111000.0 * math.cos(math.radians(36.37)))

    # Build R-tree on shelters
    t0 = time.time()
    shelter_pts = [Point(s["lon"], s["lat"]) for s in shelters]
    tree = STRtree(shelter_pts)
    construction_time = time.time() - t0

    # Query for each building
    results = []
    t0 = time.time()
    for b in buildings:
        b_pt = Point(b["lon"], b["lat"])
        # bounding box query (R-tree-supported)
        b_bbox = (b["lon"] - bbox_deg_lon, b["lat"] - bbox_deg_lat,
                  b["lon"] + bbox_deg_lon, b["lat"] + bbox_deg_lat)
        # query returns indices into shelter_pts
        candidate_indices = tree.query(Point(b["lon"], b["lat"]).buffer(max(bbox_deg_lon, bbox_deg_lat)))
        candidates = []
        for idx in candidate_indices:
            s = shelters[idx]
            d = haversine_m(b["lon"], b["lat"], s["lon"], s["lat"])
            if d <= radius_m:
                candidates.append((s["uid"], d))
        candidates.sort(key=lambda x: x[1])
        for uid, d in candidates[:max_targets]:
            results.append({"b_uid": b["uid"], "t_uid": uid, "dist_m": d})
    query_time = time.time() - t0
    total_time = construction_time + query_time

    return {
        "backend": "Shapely STRtree (R-tree)",
        "construction_time_s": round(construction_time, 4),
        "query_time_s": round(query_time, 4),
        "total_time_s": round(total_time, 4),
        "result_count": len(results),
        "notes": "Builds in-memory R-tree on shelters, queries each building with bbox+Haversine refinement",
    }


# ───────────────────────────────────────────────────────────────────
# Backend C: Brute-force pairwise Haversine
# ───────────────────────────────────────────────────────────────────

def backend_brute_force(buildings, shelters, max_targets=3, radius_m=500, sample_buildings=None):
    """Brute-force pairwise Haversine. Optionally sample buildings to bound runtime."""
    if sample_buildings and len(buildings) > sample_buildings:
        import random
        random.seed(42)
        bs = random.sample(buildings, sample_buildings)
        scale = len(buildings) / sample_buildings
    else:
        bs = buildings
        scale = 1.0

    t0 = time.time()
    results = []
    for b in bs:
        candidates = []
        for s in shelters:
            d = haversine_m(b["lon"], b["lat"], s["lon"], s["lat"])
            if d <= radius_m:
                candidates.append((s["uid"], d))
        candidates.sort(key=lambda x: x[1])
        for uid, d in candidates[:max_targets]:
            results.append({"b_uid": b["uid"], "t_uid": uid, "dist_m": d})
    query_time = time.time() - t0

    return {
        "backend": f"Brute-force Haversine (sample={len(bs)})",
        "construction_time_s": 0,
        "query_time_s": round(query_time, 4),
        "total_time_s": round(query_time, 4),
        "result_count": len(results),
        "extrapolated_full_time_s": round(query_time * scale, 4) if scale > 1 else None,
        "notes": f"Pairwise Haversine on {len(bs)} buildings × {len(shelters)} shelters; "
                 f"extrapolated to full population by ×{scale:.1f}",
    }


# ───────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--radius", type=float, default=500.0, help="Search radius (m)")
    parser.add_argument("--max-targets", type=int, default=3, help="Max targets per source")
    parser.add_argument("--brute-sample", type=int, default=2000,
                        help="Sample size for brute-force backend (use 0 for full)")
    args = parser.parse_args()

    print("=" * 70)
    print(f"  Multi-backend benchmark: NEAREST_SHELTER (radius={args.radius}m, k={args.max_targets})")
    print("=" * 70)

    # Load data once
    print("\n  Loading data from Neo4j...")
    t = time.time()
    buildings = load_buildings()
    shelters = load_shelters()
    load_time = time.time() - t
    print(f"  Loaded {len(buildings):,} buildings and {len(shelters):,} shelters in {load_time:.2f}s")

    results = []

    # Backend A1: pre-materialized read
    print("\n  [A1] Neo4j (read pre-materialized NEAREST_SHELTER)...")
    res_a1 = backend_neo4j_query()
    print(f"    {res_a1['result_count']:,} edges, {res_a1['total_time_s']}s")
    results.append(res_a1)

    # Backend A2: inline Cypher Haversine
    print("\n  [A2] Neo4j (inline Cypher Haversine query)...")
    try:
        res_a2 = backend_neo4j_inline_query(buildings, shelters,
                                            max_targets=args.max_targets,
                                            radius_m=args.radius)
        print(f"    {res_a2['result_count']:,} edges, {res_a2['total_time_s']}s")
        results.append(res_a2)
    except Exception as e:
        print(f"    [A2] failed: {e}")

    # Backend B: STRtree
    print("\n  [B] Shapely STRtree...")
    try:
        res_b = backend_strtree(buildings, shelters,
                                max_targets=args.max_targets,
                                radius_m=args.radius)
        print(f"    construction={res_b['construction_time_s']}s, query={res_b['query_time_s']}s, "
              f"total={res_b['total_time_s']}s, {res_b['result_count']:,} edges")
        results.append(res_b)
    except Exception as e:
        print(f"    [B] failed: {e}")
        import traceback
        traceback.print_exc()

    # Backend C: brute force (sampled)
    print(f"\n  [C] Brute-force Haversine (sample={args.brute_sample if args.brute_sample else 'full'})...")
    sample = args.brute_sample if args.brute_sample > 0 else None
    res_c = backend_brute_force(buildings, shelters,
                                max_targets=args.max_targets,
                                radius_m=args.radius,
                                sample_buildings=sample)
    print(f"    {res_c['result_count']:,} edges (on sample), {res_c['total_time_s']}s")
    if res_c.get("extrapolated_full_time_s"):
        print(f"    extrapolated full-population time: {res_c['extrapolated_full_time_s']}s")
    results.append(res_c)

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  Summary table")
    print(f"{'=' * 70}")
    print(f"  {'Backend':<40s} {'Construction':>12s} {'Query':>10s} {'Total':>10s} {'Edges':>10s}")
    print("  " + "─" * 86)
    for r in results:
        c = r['construction_time_s']
        c_str = f"{c:.3f}s" if c else "—"
        print(f"  {r['backend']:<40s} {c_str:>12s} {r['query_time_s']:>9.3f}s {r['total_time_s']:>9.3f}s {r['result_count']:>10,}")
        if r.get("extrapolated_full_time_s"):
            print(f"  {'  └─ extrapolated to full pop':<40s} {'':>12s} {r['extrapolated_full_time_s']:>9.3f}s")

    # Speedup factors
    print(f"\n  Speedup factors (relative to brute-force extrapolated):")
    bf_time = res_c.get("extrapolated_full_time_s") or res_c["total_time_s"]
    for r in results:
        if r is res_c:
            continue
        speedup = bf_time / r["total_time_s"] if r["total_time_s"] > 0 else 0
        print(f"    {r['backend']:<40s} {speedup:>6.1f}×")

    out = {
        "experiment": "backend_benchmark",
        "addresses": ["R1.4", "R2.16", "R2.8"],
        "timestamp": datetime.now().isoformat(),
        "params": {
            "radius_m": args.radius,
            "max_targets": args.max_targets,
            "brute_sample": args.brute_sample,
        },
        "data": {
            "n_buildings": len(buildings),
            "n_shelters": len(shelters),
            "load_time_s": round(load_time, 3),
        },
        "results": results,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"backend_benchmark_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  saved: {out_path}")


if __name__ == "__main__":
    main()
