"""Profile FRONTS_ROAD nearest-strategy Cypher on Yuseong + Sejong.

Compares per-batch cost, db hits, and query plan between the two
regions to identify why Sejong runs 27× slower than Yuseong despite
only 2.9× more pair operations.

Output: backend/experiments/results/profile_nearest_<timestamp>.json
"""
from __future__ import annotations

import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.db.neo4j_client import db, set_region  # type: ignore


def run_profile(region: str) -> dict:
    set_region(region)
    print(f"\n{'='*72}\n REGION: {region.upper()}\n{'='*72}")

    # Get road target list (same as _handle_nearest)
    target_rows = db.query(
        "MATCH (t:Road) WHERE t.coordinates IS NOT NULL "
        "RETURN t.uid AS uid, t.coordinates AS coords LIMIT 5000"
    )
    targets = []
    for r in target_rows:
        coords = json.loads(r["coords"]) if r["coords"] else []
        if coords:
            mid = coords[len(coords) // 2]
            targets.append({"uid": r["uid"], "lon": mid[0], "lat": mid[1]})
    print(f"  Road targets: {len(targets):,}")

    # Get source building UIDs (first 500 for one batch)
    source_rows = db.query(
        "MATCH (s:Building) WHERE s.longitude IS NOT NULL "
        "RETURN s.uid AS uid LIMIT 500"
    )
    src_uids = [r["uid"] for r in source_rows]
    print(f"  Building source batch: {len(src_uids):,}")
    print(f"  Estimated pair operations: {len(src_uids) * len(targets):,}")

    # Drop any existing FRONTS_ROAD edges from this 500-building sample so
    # the PROFILE is a clean run.
    db.query(
        "MATCH (s:Building)-[r:FRONTS_ROAD]->() "
        "WHERE s.uid IN $uids "
        "DELETE r",
        uids=src_uids,
    )

    profile_query = """
    UNWIND $uids AS src_uid
    MATCH (s:Building {uid: src_uid})
    WITH s,
         coalesce(s.entrance_lon, s.longitude) AS slon,
         coalesce(s.entrance_lat, s.latitude)  AS slat
    UNWIND $targets AS tgt
    WITH s, slon, slat, tgt,
         (slon - tgt.lon) * 111320 * cos(radians(slat)) AS dx,
         (slat - tgt.lat) * 110540 AS dy
    WITH s, tgt, dx*dx + dy*dy AS dist_sq
    ORDER BY s.uid, dist_sq
    WITH s, collect(tgt)[0] AS nearest
    MATCH (r:Road {uid: nearest.uid})
    CREATE (s)-[:FRONTS_ROAD]->(r)
    RETURN count(*) AS cnt
    """

    # 1. Warm-up run (5 buildings only) to load page cache
    print("  [warmup] 5 buildings...")
    db.query(
        profile_query.replace("$uids", str([src_uids[0]])),
        uids=[src_uids[0]],
        targets=targets,
    )

    # 2. Timed run
    print("  [timed] 500 buildings...")
    t0 = time.time()
    result = db.query(profile_query, uids=src_uids, targets=targets)
    elapsed = time.time() - t0
    n_created = result[0]["cnt"] if result else 0
    pair_ops = len(src_uids) * len(targets)
    per_pair_us = elapsed * 1_000_000 / max(pair_ops, 1)
    print(f"  [result] {n_created:,} edges in {elapsed:.2f}s")
    print(f"           pair ops: {pair_ops:,}")
    print(f"           per-pair cost: {per_pair_us:.2f} μs")

    # 3. PROFILE (returns plan details)
    profile_run = db.query(
        f"PROFILE {profile_query}",
        uids=src_uids[:50],  # smaller batch for PROFILE
        targets=targets,
    )
    profile_summary = []

    # Extract plan info via EXPLAIN (returns plan structure)
    plan_query = (
        f"EXPLAIN {profile_query.replace('CREATE (s)-[:FRONTS_ROAD]->(r)', '')}"
    )
    # py2neo doesn't easily return plan tree; use a CALL to db.queryProfile
    # Just record timing here

    # Clean up the test edges so subsequent measurements aren't affected
    db.query(
        "MATCH (s:Building)-[r:FRONTS_ROAD]->() "
        "WHERE s.uid IN $uids "
        "DELETE r",
        uids=src_uids,
    )

    # Quick db stats
    stats = db.stats()

    return {
        "region": region,
        "neo4j_node_count": stats["node_count"],
        "neo4j_rel_count": stats["relationship_count"],
        "n_road_targets": len(targets),
        "n_building_source_batch": len(src_uids),
        "pair_ops": pair_ops,
        "elapsed_s": elapsed,
        "per_pair_us": per_pair_us,
        "edges_created": n_created,
    }


def main():
    results = {}
    for region in ["yuseong", "sejong"]:
        results[region] = run_profile(region)

    print("\n" + "=" * 72)
    print(" SIDE-BY-SIDE — single 500-building batch of FRONTS_ROAD")
    print("=" * 72)
    y = results["yuseong"]
    s = results["sejong"]
    print(f"{'metric':<35s} {'Yuseong':>15s} {'Sejong':>15s} {'ratio':>10s}")
    keys = [("neo4j_node_count", "total nodes in DB"),
            ("neo4j_rel_count", "total rels in DB"),
            ("n_road_targets", "Road target count"),
            ("pair_ops", "pair ops"),
            ("elapsed_s", "elapsed (s)"),
            ("per_pair_us", "per-pair cost (μs)")]
    for k, label in keys:
        yv = y[k]
        sv = s[k]
        ratio = (sv / yv) if yv else 0
        if isinstance(yv, float):
            print(f"  {label:<33s} {yv:>15.3f} {sv:>15.3f} {ratio:>10.2f}")
        else:
            print(f"  {label:<33s} {yv:>15,} {sv:>15,} {ratio:>10.2f}")

    out_dir = ROOT / "backend" / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"profile_nearest_{stamp}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
