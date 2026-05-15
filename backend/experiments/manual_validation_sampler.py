"""
Manual validation sampler for k-GeoKG paper revision (R1.3, R2.20).

Reviewer 1 comment 3 and Reviewer 2 comment 20 require precision/recall
validation of the 1.33M generated relationships. This script generates
stratified random samples (default 100 per strategy) of relationships,
exports them as a CSV with all the information a human evaluator needs
to label each one (correct / incorrect / borderline / unsure), and
imports the labeled CSV back to compute precision per strategy.

Workflow:
  1. python -m backend.experiments.manual_validation_sampler --action sample
     -> generates results/validation_sample_<ts>.csv
  2. Human evaluator opens the CSV, fills in the `label` column
     (1=correct, 0=incorrect, ?=borderline)
  3. python -m backend.experiments.manual_validation_sampler --action analyze --csv results/validation_sample_<ts>.csv
     -> produces precision summary and Wilson confidence interval

Strategies sampled (mapped to rule_type):
  - attribute_match     : ON_PARCEL, ON_STREET (exact match - sanity check)
  - nearest             : FRONTS_ROAD (most concerning per reviewers)
  - same_attr_cluster   : SAME_DONG, SAME_USAGE
  - proximity           : NEAREST_SHELTER, ACCESSIBLE_BY_TRANSIT, NEAR_PARK
  - attr_prefix_proximity : ON_PARCEL via bjd_code
  - through_relationship  : COLOCATED
"""

import sys
import os
import csv
import json
import math
import random
import argparse
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from backend.db.neo4j_client import db

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


# ───────────────────────────────────────────────────────────────────
# Sampling per strategy
# ───────────────────────────────────────────────────────────────────

# Each entry: (strategy_label, rel_type, sample_size, query, judging_hint)
SAMPLING_PLAN = [
    {
        "strategy": "attribute_match",
        "rel_type": "ON_PARCEL",
        "sample_size": 50,
        "where": "type(rel) = 'ON_PARCEL' AND startNode(rel):Building",
        "hint": "Source.pnu should equal Target.pnu (any mismatch = error)",
    },
    {
        "strategy": "attribute_match",
        "rel_type": "ON_STREET",
        "sample_size": 50,
        "where": "type(rel) = 'ON_STREET' AND startNode(rel):Building",
        "hint": "Source.road_name should equal Target.name",
    },
    {
        "strategy": "nearest",
        "rel_type": "FRONTS_ROAD",
        "sample_size": 200,
        "where": "type(rel) = 'FRONTS_ROAD'",
        "hint": "Visually verify that Building's road frontage faces Road.name on a map",
    },
    {
        "strategy": "same_attr_cluster",
        "rel_type": "SAME_DONG",
        "sample_size": 100,
        "where": "type(rel) = 'SAME_DONG'",
        "hint": "Source.admin_dong should equal Target.admin_dong; both should be within ~300m",
    },
    {
        "strategy": "proximity",
        "rel_type": "NEAREST_SHELTER",
        "sample_size": 200,
        "where": "type(rel) = 'NEAREST_SHELTER'",
        "hint": "Distance should be ≤500m AND target.iot_type ∈ {CivilDefense, EQOUT}",
    },
    {
        "strategy": "proximity",
        "rel_type": "ACCESSIBLE_BY_TRANSIT",
        "sample_size": 100,
        "where": "type(rel) = 'ACCESSIBLE_BY_TRANSIT'",
        "hint": "Distance should be ≤300m AND target.iot_type ∈ {BUSST, TAXIST}",
    },
    {
        "strategy": "proximity",
        "rel_type": "NEAR_PARK",
        "sample_size": 100,
        "where": "type(rel) = 'NEAR_PARK'",
        "hint": "Distance should be ≤500m AND target.iot_type ∈ {CHPARK, SCPARK, ChPlayground}",
    },
    {
        "strategy": "through_relationship",
        "rel_type": "COLOCATED",
        "sample_size": 50,
        "where": "type(rel) = 'COLOCATED'",
        "hint": "Source and Target should share at least one Parcel via ON_PARCEL",
    },
]


def sample_relationships(seed=42):
    print("=" * 70)
    print(f"  Manual validation sampler  (seed={seed})")
    print("=" * 70)

    random.seed(seed)
    rows_out = []

    for plan in SAMPLING_PLAN:
        strat = plan["strategy"]
        rel_type = plan["rel_type"]
        n_target = plan["sample_size"]
        hint = plan["hint"]

        # First get total count
        total_q = f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c"
        total = db.query(total_q)[0]["c"]
        if total == 0:
            print(f"  [{rel_type:<25}] No relationships found, skipping.")
            continue

        # Reservoir sampling on the server side via SKIP/LIMIT random pick
        # Strategy: use Cypher-side rand() to scatter, then take first N
        sample_q = f"""
            MATCH (s)-[r:{rel_type}]->(t)
            WITH s, r, t, rand() AS rnd
            ORDER BY rnd
            LIMIT {n_target}
            RETURN
                s.uid AS s_uid, labels(s)[0] AS s_label,
                s.longitude AS s_lon, s.latitude AS s_lat,
                s.name AS s_name, s.pnu AS s_pnu,
                s.road_name AS s_road_name, s.admin_dong AS s_admin_dong,
                s.bjd_code AS s_bjd_code, s.iot_type AS s_iot_type,
                t.uid AS t_uid, labels(t)[0] AS t_label,
                t.longitude AS t_lon, t.latitude AS t_lat,
                t.name AS t_name, t.pnu AS t_pnu,
                t.road_name AS t_road_name, t.admin_dong AS t_admin_dong,
                t.bjd_code AS t_bjd_code, t.iot_type AS t_iot_type
        """
        rows = db.query(sample_q)

        for row in rows:
            d = None
            if row["s_lon"] is not None and row["t_lon"] is not None:
                d = haversine_m(row["s_lon"], row["s_lat"], row["t_lon"], row["t_lat"])
            entry = {
                "strategy": strat,
                "rel_type": rel_type,
                "judging_hint": hint,
                "src_uid": row["s_uid"],
                "src_label": row["s_label"],
                "src_lon": row["s_lon"],
                "src_lat": row["s_lat"],
                "src_pnu": row["s_pnu"],
                "src_road_name": row["s_road_name"],
                "src_admin_dong": row["s_admin_dong"],
                "src_bjd_code": row["s_bjd_code"],
                "src_iot_type": row["s_iot_type"],
                "tgt_uid": row["t_uid"],
                "tgt_label": row["t_label"],
                "tgt_lon": row["t_lon"],
                "tgt_lat": row["t_lat"],
                "tgt_name": row["t_name"],
                "tgt_pnu": row["t_pnu"],
                "tgt_road_name": row["t_road_name"],
                "tgt_admin_dong": row["t_admin_dong"],
                "tgt_iot_type": row["t_iot_type"],
                "computed_distance_m": round(d, 1) if d is not None else "",
                # Auto-pre-check: derived "expected_label" used for sanity, not ground truth
                "auto_check": _auto_check(row, rel_type, d),
                "label": "",  # human fills 1 / 0 / ?
                "evaluator_notes": "",
            }
            rows_out.append(entry)
        print(f"  [{rel_type:<25}] sampled {len(rows):>3} / {total:,} ({total} total)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(RESULTS_DIR, f"validation_sample_{ts}.csv")
    fieldnames = list(rows_out[0].keys()) if rows_out else []
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows_out:
            w.writerow(row)

    print(f"\n  Total samples: {len(rows_out)}")
    print(f"  CSV: {csv_path}")
    print(f"\n  Open the CSV in Excel, fill in the `label` column (1/0/?),")
    print(f"  then run with --action analyze --csv {csv_path}")

    # Save metadata
    meta_path = csv_path.replace(".csv", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"plan": SAMPLING_PLAN, "seed": seed, "ts": ts, "total": len(rows_out)}, f, indent=2, ensure_ascii=False)
    print(f"  Meta: {meta_path}")

    return csv_path


def _auto_check(row, rel_type, dist_m):
    """Heuristic auto-check (NOT ground truth). Provides convenience flag for evaluator."""
    if rel_type == "ON_PARCEL":
        return "PNU_MATCH" if row["s_pnu"] == row["t_pnu"] and row["s_pnu"] else "PNU_MISMATCH"
    if rel_type == "ON_STREET":
        return "ROAD_MATCH" if row["s_road_name"] and row["t_name"] and row["s_road_name"] == row["t_name"] else "ROAD_MISMATCH"
    if rel_type == "SAME_DONG":
        return "DONG_MATCH" if row["s_admin_dong"] == row["t_admin_dong"] else "DONG_MISMATCH"
    if rel_type in ("NEAREST_SHELTER", "ACCESSIBLE_BY_TRANSIT", "NEAR_PARK"):
        max_r = {"NEAREST_SHELTER": 500, "ACCESSIBLE_BY_TRANSIT": 300, "NEAR_PARK": 500}[rel_type]
        ok_dist = dist_m is not None and dist_m <= max_r * 1.05
        ok_iot = row["t_iot_type"] is not None
        return "OK" if ok_dist and ok_iot else f"FAIL(d={dist_m:.0f},iot={row['t_iot_type']})"
    return ""


# ───────────────────────────────────────────────────────────────────
# Analyze labeled CSV
# ───────────────────────────────────────────────────────────────────

def analyze_csv(csv_path):
    print("=" * 70)
    print(f"  Analyzing labeled CSV: {csv_path}")
    print("=" * 70)

    by_rel = {}
    by_strat = {}

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rt = row["rel_type"]
            st = row["strategy"]
            label = row.get("label", "").strip()
            for bucket, key in ((by_rel, rt), (by_strat, st)):
                bucket.setdefault(key, {"correct": 0, "incorrect": 0, "borderline": 0, "unlabeled": 0})
                if label == "1":
                    bucket[key]["correct"] += 1
                elif label == "0":
                    bucket[key]["incorrect"] += 1
                elif label == "?":
                    bucket[key]["borderline"] += 1
                else:
                    bucket[key]["unlabeled"] += 1

    def report(bucket, title):
        print(f"\n  {title}:")
        print(f"  {'Key':<28s} {'#labeled':>8s} {'correct':>8s} {'wrong':>6s} {'precision':>10s} {'95% CI':>20s}")
        print("  " + "─" * 90)
        rows_out = []
        for key, c in sorted(bucket.items()):
            labeled = c["correct"] + c["incorrect"] + c["borderline"]
            if labeled == 0:
                print(f"  {key:<28s} {0:>8} (no labels yet)")
                continue
            # Precision excludes borderline as ambiguous
            denom = c["correct"] + c["incorrect"]
            p, lo, hi = wilson_interval(c["correct"], denom) if denom else (0, 0, 0)
            print(f"  {key:<28s} {labeled:>8} {c['correct']:>8} {c['incorrect']:>6} {p:>10.4f} [{lo:.4f}, {hi:.4f}]")
            rows_out.append({"key": key, "labeled": labeled, **c, "precision": p, "ci_low": lo, "ci_high": hi})
        return rows_out

    by_rel_rows = report(by_rel, "By relationship type")
    by_strat_rows = report(by_strat, "By strategy")

    out = {
        "experiment": "manual_validation",
        "addresses": ["R1.3", "R2.20"],
        "timestamp": datetime.now().isoformat(),
        "csv_path": csv_path,
        "by_rel_type": by_rel_rows,
        "by_strategy": by_strat_rows,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"validation_analysis_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Saved: {out_path}")
    return out_path


# ───────────────────────────────────────────────────────────────────
# Auto-judging using attribute consistency (preliminary, NOT ground truth)
# ───────────────────────────────────────────────────────────────────

def auto_judge_csv(csv_path):
    """Apply auto_check column as preliminary precision proxy.

    NOTE: This is NOT a substitute for human validation. It only verifies
    that attribute-match-style relationships satisfy their own definitional
    constraints (e.g., that ON_PARCEL really has matching PNUs).
    """
    print("=" * 70)
    print(f"  Auto-judging (preliminary, NOT ground truth)")
    print("=" * 70)

    by_rel = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rt = row["rel_type"]
            ac = row.get("auto_check", "")
            by_rel.setdefault(rt, {"OK": 0, "FAIL": 0, "MISMATCH": 0, "MATCH": 0, "EMPTY": 0})
            if ac.startswith("OK") or ac.endswith("MATCH"):
                by_rel[rt]["OK"] += 1
            elif ac.startswith("FAIL") or ac.endswith("MISMATCH"):
                by_rel[rt]["FAIL"] += 1
            elif not ac:
                by_rel[rt]["EMPTY"] += 1

    print(f"\n  {'Rel type':<30s} {'OK':>8s} {'FAIL':>8s} {'EMPTY':>8s} {'auto-precision':>15s}")
    print("  " + "─" * 75)
    for rt, c in sorted(by_rel.items()):
        labeled = c["OK"] + c["FAIL"]
        p = c["OK"] / labeled if labeled else 0
        print(f"  {rt:<30s} {c['OK']:>8} {c['FAIL']:>8} {c['EMPTY']:>8} {p:>15.4f}")

    out = {
        "auto_judge": True,
        "by_rel_type": by_rel,
        "csv_path": csv_path,
        "timestamp": datetime.now().isoformat(),
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"validation_autojudge_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Saved: {out_path}")
    return out_path


# ───────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", "-a", choices=["sample", "analyze", "autojudge"], default="sample")
    parser.add_argument("--csv", help="CSV path (for analyze/autojudge actions)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.action == "sample":
        sample_relationships(seed=args.seed)
    elif args.action == "analyze":
        if not args.csv:
            print("[ERROR] --csv required for analyze action")
            return 1
        analyze_csv(args.csv)
    elif args.action == "autojudge":
        if not args.csv:
            print("[ERROR] --csv required for autojudge action")
            return 1
        auto_judge_csv(args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
