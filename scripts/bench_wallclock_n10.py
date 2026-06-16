"""Run reseed_neo4j N=10 times per region for wall-clock variance measurement.

Calls scripts/reseed_neo4j.py {region} 10 times in sequence per region,
captures the resulting JSON summaries, and writes the aggregated result
(mean, sd, 95% Student-t CI for each region) to
``backend/experiments/results/measured_wallclock_n10.json``.

Usage:
    python scripts/bench_wallclock_n10.py

The script appends to any existing partial results so it can resume after
an interruption. Set BENCH_N_PER_REGION env var to override the default 10.
"""
from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
RESEED_SCRIPT = ROOT / "scripts" / "reseed_neo4j.py"
RESULTS_DIR = ROOT / "backend" / "experiments" / "results"
BENCH_OUT = RESULTS_DIR / "measured_wallclock_n10.json"

N_PER_REGION = int(os.environ.get("BENCH_N_PER_REGION", "10"))
REGIONS = ["yuseong", "sejong"]
BENCH_TAG = "BENCH_N10_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def run_once(region: str) -> dict:
    """Invoke reseed_neo4j.py for a single region and return its parsed JSON.

    Picks up the freshly-written reseed_neo4j_{region}_{stamp}.json file by
    timestamp filter after the subprocess exits."""
    before = {p.name for p in RESULTS_DIR.glob(f"reseed_neo4j_{region}_*.json")}
    t0 = time.time()
    res = subprocess.run(
        [sys.executable, str(RESEED_SCRIPT), region],
        cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    elapsed = time.time() - t0
    if res.returncode != 0:
        print(f"[bench]   FAILED (rc={res.returncode}) after {elapsed:.1f}s")
        print(res.stdout[-2000:])
        print(res.stderr[-2000:])
        raise RuntimeError(f"reseed_neo4j.py {region} failed")
    after = sorted(
        (p for p in RESULTS_DIR.glob(f"reseed_neo4j_{region}_*.json")
         if p.name not in before),
        key=lambda p: p.stat().st_mtime,
    )
    if not after:
        raise RuntimeError("No new reseed JSON produced")
    fresh = after[-1]
    return json.loads(fresh.read_text(encoding="utf-8")), fresh.name


def append_run(region: str, run_idx: int, summary: dict, src_file: str) -> dict:
    t = summary["timing_sec"]
    rule_eng = t.get("rule_engine", {})
    rule_total = (rule_eng.get("total_seconds")
                  or rule_eng.get("total")
                  or rule_eng.get("rule_engine_total"))
    return {
        "region": region,
        "run_idx": run_idx,
        "bench_tag": BENCH_TAG,
        "src_file": src_file,
        "data_prep_sec": t["data_preparation_total"],
        "neo4j_build_sec": t["neo4j_build_total"],
        "total_sec": t["data_preparation_total"] + t["neo4j_build_total"],
        "rule_engine_sec": rule_total,
        "metrics": summary.get("metrics", {}),
    }


def stats(xs):
    n = len(xs)
    if n == 0:
        return None
    mean = sum(xs) / n
    if n == 1:
        return {"n": n, "mean": mean, "sd": 0.0, "ci95_half": 0.0,
                "cv_pct": 0.0, "min": mean, "max": mean}
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
    # t-distribution 95% CI half-width for small n (df=n-1)
    # Use a small lookup of t-critical values for two-sided 95% CI.
    t_crit = {
        2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
        7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262, 11: 2.228,
        12: 2.201, 13: 2.179, 14: 2.160, 15: 2.145, 16: 2.131,
        20: 2.086, 30: 2.042,
    }
    df = n - 1
    closest = min(t_crit.keys(), key=lambda k: abs(k - df))
    t = t_crit[closest]
    ci_half = t * sd / math.sqrt(n)
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "ci95_half": ci_half,
        "cv_pct": 100.0 * sd / mean if mean else 0.0,
        "min": min(xs),
        "max": max(xs),
    }


def main():
    # Load any existing partial output to resume
    accumulated = {"bench_tag": BENCH_TAG, "n_per_region": N_PER_REGION, "runs": {}}
    if BENCH_OUT.exists():
        try:
            prev = json.loads(BENCH_OUT.read_text(encoding="utf-8"))
            if prev.get("n_per_region") == N_PER_REGION:
                accumulated = prev
                print(f"[bench] Resuming from {BENCH_OUT.name} "
                      f"(tag={accumulated.get('bench_tag')})")
        except Exception:
            pass
    accumulated.setdefault("runs", {})

    for region in REGIONS:
        runs = accumulated["runs"].setdefault(region, [])
        already = len(runs)
        print(f"\n[bench] === REGION {region.upper()} "
              f"({already}/{N_PER_REGION} already collected) ===")
        for i in range(already, N_PER_REGION):
            idx = i + 1
            print(f"[bench]   run {idx}/{N_PER_REGION} starting at "
                  f"{datetime.now().strftime('%H:%M:%S')}")
            t0 = time.time()
            summary, src_name = run_once(region)
            elapsed = time.time() - t0
            row = append_run(region, idx, summary, src_name)
            row["measured_elapsed_sec"] = round(elapsed, 2)
            runs.append(row)
            print(f"[bench]   run {idx} done: prep={row['data_prep_sec']:.1f}s "
                  f"build={row['neo4j_build_sec']:.1f}s total={row['total_sec']:.1f}s "
                  f"(wall={elapsed:.1f}s)")
            # Persist after every run so we never lose progress
            accumulated["last_updated"] = datetime.now().isoformat()
            BENCH_OUT.write_text(
                json.dumps(accumulated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # Final aggregate statistics
    aggregate = {}
    for region in REGIONS:
        runs = accumulated["runs"].get(region, [])
        prep = [r["data_prep_sec"] for r in runs]
        build = [r["neo4j_build_sec"] for r in runs]
        total = [r["total_sec"] for r in runs]
        rule = [r["rule_engine_sec"] for r in runs
                if r.get("rule_engine_sec") is not None]
        aggregate[region] = {
            "data_prep": stats(prep),
            "neo4j_build": stats(build),
            "total": stats(total),
            "rule_engine": stats(rule),
        }
    accumulated["aggregate"] = aggregate
    BENCH_OUT.write_text(
        json.dumps(accumulated, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print(" FINAL N=10 BENCHMARK SUMMARY")
    print("=" * 70)
    for region in REGIONS:
        a = aggregate[region]
        t = a["total"]
        b = a["neo4j_build"]
        p = a["data_prep"]
        r = a["rule_engine"]
        print(f"\n[{region}] n={t['n']}")
        print(f"  data_prep    : mean={p['mean']:7.1f}s  sd={p['sd']:5.1f}s  "
              f"95% CI=±{p['ci95_half']:5.1f}s  CV={p['cv_pct']:4.1f}%")
        print(f"  neo4j_build  : mean={b['mean']:7.1f}s  sd={b['sd']:5.1f}s  "
              f"95% CI=±{b['ci95_half']:5.1f}s  CV={b['cv_pct']:4.1f}%")
        print(f"  total        : mean={t['mean']:7.1f}s  sd={t['sd']:5.1f}s  "
              f"95% CI=±{t['ci95_half']:5.1f}s  CV={t['cv_pct']:4.1f}%")
        if r:
            print(f"  rule_engine  : mean={r['mean']:7.1f}s  sd={r['sd']:5.1f}s  "
                  f"95% CI=±{r['ci95_half']:5.1f}s  CV={r['cv_pct']:4.1f}%")

    print(f"\n[bench] Saved → {BENCH_OUT}")


if __name__ == "__main__":
    main()
