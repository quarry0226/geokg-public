"""
Enhanced auto-validation for FRONTS_ROAD and COLOCATED relationships.

The original `manual_validation_sampler.py` left FRONTS_ROAD and COLOCATED
as "EMPTY" because their correctness cannot be checked from attribute equality
alone. This script applies stronger algorithmic checks that approximate
human visual judgement:

  FRONTS_ROAD: for each (Building b, Road r) edge, verify that
    (a) r is among the K=3 nearest roads to b by polyline-projection distance, AND
    (b) the polyline-projection distance b→r is below a "reasonable" threshold

  COLOCATED: for each (a, COLOCATED, b) edge, verify that there exists at least
    one Parcel p such that (a)-[:ON_PARCEL]->(p)<-[:ON_PARCEL]-(b).

Output: same CSV as input but with `auto_check` column populated for
FRONTS_ROAD and COLOCATED rows; precision summary printed.

Usage:
  python -m backend.experiments.auto_validate_enhanced --csv <validation_sample_csv>
"""

import sys
import os
import csv
import json
import math
import argparse
from datetime import datetime
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from backend.db.neo4j_client import db
from shapely.geometry import LineString, Point

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    lon1, lat1, lon2, lat2 = map(math.radians, (lon1, lat1, lon2, lat2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def wilson_interval(successes, total, z=1.96):
    if total == 0:
        return (0.0, 0.0, 0.0)
    p_hat = successes / total
    denom = 1 + z ** 2 / total
    center = (p_hat + z ** 2 / (2 * total)) / denom
    halfwidth = z * math.sqrt(p_hat * (1 - p_hat) / total + z ** 2 / (4 * total ** 2)) / denom
    return (round(p_hat, 4), round(center - halfwidth, 4), round(center + halfwidth, 4))


def load_all_roads_with_geometry():
    """Load all roads with their polyline geometry. Returns dict uid → (name, LineString, lon, lat)."""
    rows = db.query(
        """
        MATCH (r:Road)
        WHERE r.coordinates IS NOT NULL
        RETURN r.uid AS uid, r.name AS name,
               r.longitude AS lon, r.latitude AS lat,
               r.coordinates AS coords
        """
    )
    out = {}
    for row in rows:
        try:
            coords = json.loads(row["coords"]) if isinstance(row["coords"], str) else row["coords"]
            if not coords or len(coords) < 2:
                continue
            line = LineString(coords)
            out[row["uid"]] = {
                "name": row["name"],
                "line": line,
                "lon": row["lon"],
                "lat": row["lat"],
            }
        except Exception:
            continue
    return out


def find_k_nearest_roads(b_lon, b_lat, roads_dict, k=3, max_dist_m=2000):
    """Return list of (uid, polyline_dist_m) for the K nearest roads."""
    pt = Point(b_lon, b_lat)
    candidates = []
    for uid, info in roads_dict.items():
        try:
            projected = info["line"].interpolate(info["line"].project(pt))
            d = haversine_m(b_lon, b_lat, projected.x, projected.y)
            if d <= max_dist_m:
                candidates.append((uid, d, info["name"]))
        except Exception:
            continue
    candidates.sort(key=lambda x: x[1])
    return candidates[:k]


def fronts_road_check(rows, max_dist_m=500, k=3):
    """Apply nearest-K-consistency + polyline-distance check to FRONTS_ROAD samples."""
    print(f"\n  Loading all roads with geometry...")
    roads = load_all_roads_with_geometry()
    print(f"  Loaded {len(roads)} roads with valid polyline geometry")

    fronts_rows = [r for r in rows if r["rel_type"] == "FRONTS_ROAD"]
    print(f"  Checking {len(fronts_rows)} FRONTS_ROAD samples...")

    results = {"OK_NEAREST_AND_CLOSE": 0, "OK_NEAREST_BUT_FAR": 0,
               "OK_NOT_NEAREST_BUT_CLOSE": 0, "FAIL_NOT_NEAREST_AND_FAR": 0,
               "ERROR": 0}

    detail_rows = []
    for i, row in enumerate(fronts_rows):
        b_lon = float(row["src_lon"])
        b_lat = float(row["src_lat"])
        assigned_uid = row["tgt_uid"]
        try:
            top_k = find_k_nearest_roads(b_lon, b_lat, roads, k=k)
        except Exception as e:
            results["ERROR"] += 1
            detail_rows.append({**row, "verdict": "ERROR", "polyline_dist_m": None, "rank": None})
            continue

        # Find rank of assigned road in top_k
        assigned_dist = None
        rank = None
        for rk, (uid, d, name) in enumerate(top_k, 1):
            if uid == assigned_uid:
                assigned_dist = d
                rank = rk
                break

        # If not in top_k, compute its actual distance separately
        if assigned_dist is None:
            if assigned_uid in roads:
                line = roads[assigned_uid]["line"]
                pt = Point(b_lon, b_lat)
                projected = line.interpolate(line.project(pt))
                assigned_dist = haversine_m(b_lon, b_lat, projected.x, projected.y)

        is_nearest = (rank == 1)
        in_topk = rank is not None
        is_close = assigned_dist is not None and assigned_dist <= max_dist_m

        if is_nearest and is_close:
            verdict = "OK_NEAREST_AND_CLOSE"
        elif is_nearest and not is_close:
            verdict = "OK_NEAREST_BUT_FAR"
        elif in_topk and is_close:
            verdict = "OK_NOT_NEAREST_BUT_CLOSE"
        else:
            verdict = "FAIL_NOT_NEAREST_AND_FAR"

        results[verdict] += 1
        detail_rows.append({
            "src_uid": row["src_uid"],
            "tgt_uid": assigned_uid,
            "tgt_name": row["tgt_name"],
            "polyline_dist_m": round(assigned_dist, 1) if assigned_dist is not None else None,
            "rank_in_topk": rank,
            "top1_uid": top_k[0][0] if top_k else None,
            "top1_name": top_k[0][2] if top_k else None,
            "top1_dist_m": round(top_k[0][1], 1) if top_k else None,
            "verdict": verdict,
        })

        if (i + 1) % 50 == 0:
            print(f"    progress: {i + 1}/{len(fronts_rows)} processed")

    return results, detail_rows


def colocated_check(rows):
    """Verify each COLOCATED edge by graph traversal: do source and target share a Parcel?"""
    coloc_rows = [r for r in rows if r["rel_type"] == "COLOCATED"]
    print(f"\n  Checking {len(coloc_rows)} COLOCATED samples...")

    results = {"OK_SHARED_PARCEL": 0, "FAIL_NO_SHARED_PARCEL": 0, "ERROR": 0}
    detail_rows = []

    for row in coloc_rows:
        try:
            r = db.query(
                """
                MATCH (a {uid: $a_uid})-[:ON_PARCEL]->(p:Parcel)<-[:ON_PARCEL]-(b {uid: $b_uid})
                RETURN count(p) AS shared_count, collect(DISTINCT p.uid)[..3] AS shared_uids
                """,
                a_uid=row["src_uid"],
                b_uid=row["tgt_uid"],
            )
            shared_count = r[0]["shared_count"] if r else 0
            shared_uids = r[0]["shared_uids"] if r else []

            verdict = "OK_SHARED_PARCEL" if shared_count >= 1 else "FAIL_NO_SHARED_PARCEL"
            results[verdict] += 1

            detail_rows.append({
                "src_uid": row["src_uid"],
                "tgt_uid": row["tgt_uid"],
                "shared_parcel_count": shared_count,
                "shared_parcels": shared_uids,
                "verdict": verdict,
            })
        except Exception as e:
            results["ERROR"] += 1
            detail_rows.append({"error": str(e), "verdict": "ERROR", **row})

    return results, detail_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--max-dist", type=float, default=500,
                        help="Threshold (m) for 'close' classification (default 500)")
    parser.add_argument("--k", type=int, default=3,
                        help="K for nearest-K consistency check (default 3)")
    args = parser.parse_args()

    print("=" * 70)
    print("  Enhanced auto-validation: FRONTS_ROAD + COLOCATED")
    print("=" * 70)

    rows = []
    with open(args.csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    print(f"\n  Loaded {len(rows)} rows from {args.csv}")

    fr_results, fr_details = fronts_road_check(rows, max_dist_m=args.max_dist, k=args.k)
    coloc_results, coloc_details = colocated_check(rows)

    # FRONTS_ROAD precision computation
    fr_total = sum(fr_results.values()) - fr_results["ERROR"]
    fr_lenient_correct = fr_results["OK_NEAREST_AND_CLOSE"] + fr_results["OK_NEAREST_BUT_FAR"] + fr_results["OK_NOT_NEAREST_BUT_CLOSE"]
    fr_strict_correct = fr_results["OK_NEAREST_AND_CLOSE"]

    print(f"\n  ─── FRONTS_ROAD verdicts ──────────────────────────")
    for verdict, count in fr_results.items():
        pct = count / max(fr_total, 1) * 100 if verdict != "ERROR" else 0
        print(f"    {verdict:<35s} {count:>4}  ({pct:5.1f}%)")

    if fr_total > 0:
        p_strict, lo_s, hi_s = wilson_interval(fr_strict_correct, fr_total)
        p_lenient, lo_l, hi_l = wilson_interval(fr_lenient_correct, fr_total)
        print(f"\n  Strict precision  (nearest road AND ≤{args.max_dist:.0f}m): {p_strict:.4f}  [{lo_s:.4f}, {hi_s:.4f}]")
        print(f"  Lenient precision (any of: nearest OR ≤{args.max_dist:.0f}m + in top-{args.k}): {p_lenient:.4f}  [{lo_l:.4f}, {hi_l:.4f}]")

    # COLOCATED precision
    coloc_total = sum(coloc_results.values()) - coloc_results["ERROR"]
    coloc_correct = coloc_results["OK_SHARED_PARCEL"]

    print(f"\n  ─── COLOCATED verdicts ──────────────────────────")
    for verdict, count in coloc_results.items():
        pct = count / max(coloc_total, 1) * 100 if verdict != "ERROR" else 0
        print(f"    {verdict:<35s} {count:>4}  ({pct:5.1f}%)")

    if coloc_total > 0:
        p_coloc, lo_c, hi_c = wilson_interval(coloc_correct, coloc_total)
        print(f"\n  Precision (shared Parcel via ON_PARCEL): {p_coloc:.4f}  [{lo_c:.4f}, {hi_c:.4f}]")

    # Save consolidated result
    out = {
        "experiment": "auto_validate_enhanced",
        "addresses": ["R1.3", "R2.20"],
        "timestamp": datetime.now().isoformat(),
        "csv_path": args.csv,
        "params": {"max_dist_m": args.max_dist, "k": args.k},
        "fronts_road": {
            "total": fr_total,
            "verdicts": fr_results,
            "precision_strict": {
                "successes": fr_strict_correct,
                "wilson_95ci": [p_strict, lo_s, hi_s] if fr_total else None,
            },
            "precision_lenient": {
                "successes": fr_lenient_correct,
                "wilson_95ci": [p_lenient, lo_l, hi_l] if fr_total else None,
            },
            "details": fr_details,
        },
        "colocated": {
            "total": coloc_total,
            "verdicts": coloc_results,
            "precision": {
                "successes": coloc_correct,
                "wilson_95ci": [p_coloc, lo_c, hi_c] if coloc_total else None,
            },
            "details": coloc_details,
        },
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"auto_validate_enhanced_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
