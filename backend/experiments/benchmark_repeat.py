"""
Repeated benchmark runs to compute mean ± std for reviewer R2.3.

Reviewer 2 comment 3 asked for "statistical measurements across multiple runs"
on timing claims like "under 1 second" and completeness guarantees.

This runs the rule engine N times (default 5) on the same dataset and
reports mean, std, min, max for each strategy.

Usage:
  python -m backend.experiments.benchmark_repeat --runs 5
"""

import sys
import os
import json
import time
import argparse
import statistics
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def _save(name, data):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"{name}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  saved: {path}")
    return path


def aggregate(values):
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "stdev": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "ci_95_halfwidth": round(
            1.96 * statistics.stdev(values) / (len(values) ** 0.5), 3
        ) if len(values) > 1 else 0.0,
    }


def run_rule_engine_once(data, builder, db):
    """Clear rule-generated edges, then re-run the rule engine.

    We DO NOT re-load nodes — only relationships are rebuilt to isolate
    rule-engine timing variance from data-loading I/O variance.
    """
    from backend.geokg.relationship_engine import enrich_by_rules, _clear_label_cache
    from backend.geokg.relationship_rules import RELATIONSHIP_RULES

    # Delete only rule-generated relationship types (preserve seed relationships)
    rule_rel_types = sorted({r["rel_type"] for r in RELATIONSHIP_RULES})
    for rt in rule_rel_types:
        # Delete in batches to avoid memory issues
        while True:
            res = db.query(f"MATCH ()-[r:{rt}]->() WITH r LIMIT 50000 DELETE r RETURN count(*) AS c")
            if not res or res[0]["c"] == 0:
                break

    _clear_label_cache()

    t0 = time.time()
    timing = enrich_by_rules(db)
    elapsed = time.time() - t0

    return elapsed, timing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", "-n", type=int, default=5)
    parser.add_argument("--regen-data", action="store_true",
                        help="Also regenerate data each run (slower; tests full pipeline variance)")
    args = parser.parse_args()

    from backend.db.neo4j_client import db
    from backend.geokg.builder import GeoKGBuilder

    stats = db.stats()
    if stats.get("node_count", 0) == 0:
        print("[ERROR] DB is empty. Build the graph first via /api/reseed.")
        return 1

    print("=" * 70)
    print(f"  Repeated rule engine runs (n={args.runs})")
    print("=" * 70)

    builder = GeoKGBuilder()

    runs = []
    for i in range(args.runs):
        print(f"\n--- Run {i + 1}/{args.runs} ---")
        elapsed, timing = run_rule_engine_once(None, builder, db)

        run_info = {
            "run": i + 1,
            "total_elapsed_s": round(elapsed, 3),
            "strategies": {
                k: {
                    "elapsed_s": round(v["elapsed"], 3),
                    "rels_created": v["rels_created"],
                }
                for k, v in timing["strategies"].items()
            },
        }
        runs.append(run_info)
        print(f"  Total: {elapsed:.2f}s")

    # Aggregate
    total_times = [r["total_elapsed_s"] for r in runs]
    strategy_names = list(runs[0]["strategies"].keys())

    aggregated = {
        "total_elapsed_s": aggregate(total_times),
        "by_strategy": {},
    }

    for strat in strategy_names:
        times = [r["strategies"][strat]["elapsed_s"] for r in runs]
        rels = [r["strategies"][strat]["rels_created"] for r in runs]
        aggregated["by_strategy"][strat] = {
            "elapsed_s": aggregate(times),
            "rels_created": aggregate(rels),
        }

    result = {
        "experiment": "benchmark_repeat",
        "addresses": ["R2.3"],
        "timestamp": datetime.now().isoformat(),
        "runs": args.runs,
        "individual_runs": runs,
        "aggregated": aggregated,
    }

    print(f"\n{'=' * 70}")
    print(f"  Aggregated results across {args.runs} runs:")
    print(f"{'=' * 70}")
    print(f"\n  Total rule engine time:")
    a = aggregated["total_elapsed_s"]
    print(f"    {a['mean']:.2f} ± {a['stdev']:.2f} s  (95% CI: ±{a['ci_95_halfwidth']:.2f}, range {a['min']:.2f}-{a['max']:.2f})")

    print(f"\n  Per-strategy timing (mean ± std, seconds):")
    for strat, info in aggregated["by_strategy"].items():
        e = info["elapsed_s"]
        r = info["rels_created"]
        print(f"    {strat:<30s} {e['mean']:>8.3f} ± {e['stdev']:>6.3f}   rels={r['mean']:>10,.0f} ± {r['stdev']:>6,.0f}")

    _save("benchmark_repeat", result)


if __name__ == "__main__":
    sys.exit(main() or 0)
