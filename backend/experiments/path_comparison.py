"""
Path comparison stratified sample (R2.15).

For each of three path-finding modes (spatial / road_network / all),
sample 100 building pairs across three Euclidean distance bins
(short ~500m, medium ~2km, long ~5km), measure:
  - hop count
  - computation time (ms)
  - presence/absence of a path

Reports mean ± std per (mode × distance-bin) cell.

Usage: python -m backend.experiments.path_comparison
"""

import os
import sys
import json
import time
import math
import random
import statistics
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from backend.db.neo4j_client import db
import urllib.parse, urllib.request

API_BASE = os.environ.get("GEOKG_API", "http://127.0.0.1:8000")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    lon1, lat1, lon2, lat2 = map(math.radians, (lon1, lat1, lon2, lat2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def sample_building_pairs(n_per_bin=33, seed=42):
    """Sample building pairs across three distance bins."""
    random.seed(seed)
    rows = db.query(
        "MATCH (b:Building) WHERE b.longitude IS NOT NULL "
        "RETURN b.uid AS uid, b.longitude AS lon, b.latitude AS lat LIMIT 5000"
    )
    bins = {"short": [], "medium": [], "long": []}
    target_distances = {"short": (300, 800), "medium": (1500, 2500), "long": (4000, 6000)}

    attempts = 0
    while min(len(v) for v in bins.values()) < n_per_bin and attempts < 200_000:
        attempts += 1
        a, b = random.sample(rows, 2)
        d = haversine_m(a["lon"], a["lat"], b["lon"], b["lat"])
        for label, (lo, hi) in target_distances.items():
            if lo <= d <= hi and len(bins[label]) < n_per_bin:
                bins[label].append({
                    "from_uid": a["uid"], "to_uid": b["uid"],
                    "euclidean_m": round(d, 1), "bin": label,
                })
                break

    pairs = []
    for label in ["short", "medium", "long"]:
        pairs.extend(bins[label])
    return pairs


def measure_one(from_uid, to_uid, mode):
    """Measure hops and time for a single path query via HTTP API."""
    qs = urllib.parse.urlencode({"from_uid": from_uid, "to_uid": to_uid, "mode": mode})
    url = f"{API_BASE}/api/kg/path?{qs}"
    t0 = time.time()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        elapsed_ms = (time.time() - t0) * 1000
        hops = data.get("hops")
        return {"hops": int(hops) if hops is not None else None,
                "time_ms": round(elapsed_ms, 1), "ok": hops is not None}
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.time() - t0) * 1000
        return {"hops": None, "time_ms": round(elapsed_ms, 1), "ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000
        return {"hops": None, "time_ms": round(elapsed_ms, 1), "ok": False, "error": str(e)[:100]}


def main():
    print("=" * 70)
    print("  Path Comparison Stratified Sample (R2.15)")
    print("=" * 70)
    pairs = sample_building_pairs(n_per_bin=33, seed=42)
    print(f"\n  Sampled {len(pairs)} pairs across 3 distance bins")
    bin_counts = {b: sum(1 for p in pairs if p["bin"] == b) for b in ("short", "medium", "long")}
    print(f"    bin counts: {bin_counts}")

    modes = ["spatial", "road_network", "all"]

    detailed = []
    for i, pair in enumerate(pairs):
        for mode in modes:
            r = measure_one(pair["from_uid"], pair["to_uid"], mode)
            detailed.append({**pair, "mode": mode, **r})
        if (i + 1) % 20 == 0:
            print(f"    progress: {i + 1}/{len(pairs)}")

    # Aggregate by (mode, bin)
    summary = {}
    for mode in modes:
        summary[mode] = {}
        for binname in ("short", "medium", "long"):
            cell = [d for d in detailed if d["mode"] == mode and d["bin"] == binname]
            n = len(cell)
            ok = [c for c in cell if c["ok"] and c["hops"] is not None]
            ok_n = len(ok)
            if ok_n:
                hops = [c["hops"] for c in ok]
                times = [c["time_ms"] for c in ok]
                summary[mode][binname] = {
                    "n": n,
                    "n_path_found": ok_n,
                    "hops_mean": round(statistics.mean(hops), 2),
                    "hops_std": round(statistics.stdev(hops), 2) if len(hops) > 1 else 0.0,
                    "hops_min": min(hops),
                    "hops_max": max(hops),
                    "time_ms_mean": round(statistics.mean(times), 1),
                    "time_ms_std": round(statistics.stdev(times), 1) if len(times) > 1 else 0.0,
                }
            else:
                summary[mode][binname] = {"n": n, "n_path_found": 0}

    # Print
    print("\n  Mode × distance-bin × {hops, time_ms}")
    print("  " + "─" * 80)
    print(f"  {'Mode':<14} {'Bin':<8} {'n':>3} {'paths':>6} {'hops (mean±std)':>18} {'time ms (mean±std)':>20}")
    print("  " + "─" * 80)
    for mode in modes:
        for binname in ("short", "medium", "long"):
            cell = summary[mode][binname]
            if cell.get("n_path_found", 0) > 0:
                print(f"  {mode:<14} {binname:<8} {cell['n']:>3} {cell['n_path_found']:>6} "
                      f"{cell['hops_mean']:>5.2f} ± {cell['hops_std']:<5.2f}    "
                      f"{cell['time_ms_mean']:>6.1f} ± {cell['time_ms_std']:<6.1f}")
            else:
                print(f"  {mode:<14} {binname:<8} {cell['n']:>3} {0:>6}  no paths found")

    out = {
        "experiment": "path_comparison",
        "addresses": ["R2.15"],
        "timestamp": datetime.now().isoformat(),
        "total_pairs": len(pairs),
        "summary": summary,
        "detail": detailed,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"path_comparison_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  saved: {out_path}")


if __name__ == "__main__":
    main()
