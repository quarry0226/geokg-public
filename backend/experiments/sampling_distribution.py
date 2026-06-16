"""
Sampling spatial distribution preservation analysis (R2.9).

Reviewer 2 comment 9: scalability test uses random sampling — does this
preserve the spatial distribution? If buildings are sampled non-uniformly
across dongs, proximity-based relationships are biased.

This script takes the current full Building set and simulates the
10/25/50/75/100% sampling identical to benchmark.py (sequential slicing
of the loaded list, NOT spatially-stratified). It then computes:
  - admin_dong distribution (sample vs full)
  - chi-square test of homogeneity
  - per-dong building density
  - max deviation from uniform sampling

If the result shows significant bias, paper Section V.C must be
qualified, or a stratified-by-dong sampling alternative provided.

Usage:
  python -m backend.experiments.sampling_distribution
"""

import sys
import os
import json
import math
import argparse
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from backend.db.neo4j_client import db

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def chi_square(observed, expected):
    """Chi-square statistic for goodness-of-fit (only meaningful when expected ≥ 5)."""
    chi2 = 0.0
    df = 0
    for o, e in zip(observed, expected):
        if e <= 0:
            continue
        chi2 += (o - e) ** 2 / e
        df += 1
    return chi2, max(df - 1, 1)


def main():
    print("=" * 70)
    print("  Sampling spatial distribution preservation (R2.9)")
    print("=" * 70)

    # Pull all buildings with admin_dong (in DB-insertion order)
    rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.admin_dong IS NOT NULL AND b.admin_dong <> ''
        RETURN b.uid AS uid, b.admin_dong AS dong, b.longitude AS lon, b.latitude AS lat
        """
    )
    print(f"\n  Buildings with admin_dong: {len(rows):,}")

    # Full distribution
    full_dist = Counter(r["dong"] for r in rows)
    dongs = sorted(full_dist.keys())
    full_total = sum(full_dist.values())
    full_pct = {d: full_dist[d] / full_total * 100 for d in dongs}

    print(f"\n  Full population dong distribution:")
    for d in dongs:
        print(f"    {d:<20s} {full_dist[d]:>6,} ({full_pct[d]:5.2f}%)")

    # Simulate the sequential sampling used by benchmark.py:
    #   subset["buildings"] = all_buildings[:n_bldg]
    # This is INSERTION-ORDER prefix, which depends on how SHP is read.
    percentages = [10, 25, 50, 75, 100]
    sample_results = []
    for pct in percentages:
        n_sample = max(1, int(len(rows) * pct / 100))
        sample = rows[:n_sample]
        sample_dist = Counter(r["dong"] for r in sample)
        sample_total = sum(sample_dist.values())

        # Expected count under uniform sampling
        expected = [full_dist[d] * pct / 100 for d in dongs]
        observed = [sample_dist.get(d, 0) for d in dongs]
        chi2, df = chi_square(observed, expected)

        # Maximum percentage point deviation
        max_dev = 0
        max_dev_dong = None
        for d in dongs:
            obs_pct = sample_dist.get(d, 0) / sample_total * 100 if sample_total else 0
            exp_pct = full_pct[d]
            dev = abs(obs_pct - exp_pct)
            if dev > max_dev:
                max_dev = dev
                max_dev_dong = d

        per_dong = {}
        for d in dongs:
            per_dong[d] = {
                "expected": round(expected[dongs.index(d)], 1),
                "observed": sample_dist.get(d, 0),
                "deviation_pct_points": round(
                    sample_dist.get(d, 0) / sample_total * 100 - full_pct[d] if sample_total else 0,
                    2,
                ),
            }

        sample_results.append({
            "percentage": pct,
            "n_sampled": n_sample,
            "chi2_statistic": round(chi2, 2),
            "degrees_of_freedom": df,
            "max_deviation_pct_points": round(max_dev, 2),
            "max_deviation_dong": max_dev_dong,
            "per_dong": per_dong,
        })

        print(f"\n  Scale {pct}% (n={n_sample:,}):")
        print(f"    chi^2 = {chi2:.2f}  df = {df}")
        print(f"    max dev: {max_dev:.2f}pp at dong '{max_dev_dong}'")

    # Recommendation
    has_bias = any(r["max_deviation_pct_points"] > 5 for r in sample_results if r["percentage"] < 100)
    recommendation = (
        "Spatial bias detected: prefix sampling does NOT preserve dong distribution. "
        "Recommend re-running scalability with stratified-by-dong random sampling."
        if has_bias else
        "Prefix sampling preserves dong distribution within ±5pp. "
        "Existing scalability results are spatially representative."
    )

    print(f"\n  Recommendation:")
    print(f"    {recommendation}")

    result = {
        "experiment": "sampling_distribution",
        "addresses": ["R2.9"],
        "timestamp": datetime.now().isoformat(),
        "full_population": {
            "total": full_total,
            "by_dong": dict(full_dist),
            "by_dong_pct": {d: round(full_pct[d], 2) for d in dongs},
        },
        "samples": sample_results,
        "has_significant_bias": has_bias,
        "recommendation": recommendation,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"sampling_distribution_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  saved: {path}")


if __name__ == "__main__":
    main()
