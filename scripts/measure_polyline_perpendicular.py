"""Compare FRONTS_ROAD distance under two distance metrics:

  (a) point-to-point Haversine — building entrance to road representative point.
      This is what the current rule engine uses and what Table 9(c) reports
      (median 43.6 m, 90th pct 110.1 m for Yuseong-gu's 18,106 buildings).

  (b) polyline-perpendicular — minimum distance from the building entrance
      to ANY segment of the chosen road's polyline geometry.
      This is the physically meaningful "frontage" distance.

We measure both on a stratified random sample of n=200 buildings drawn
equally from the six distance bins reported in Table 9(c), so each bin
contributes proportionally to the gain estimate.

Output: backend/experiments/results/measured_polyline_gain.json
"""
from __future__ import annotations

import gzip
import io
import json
import math
import random
import sys
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "data" / "processed" / "yuseong.json.gz"
OUTPUT = ROOT / "backend" / "experiments" / "results" / "measured_polyline_gain.json"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000.0
    rad = math.radians
    dlat = rad(lat2 - lat1)
    dlon = rad(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def road_midpoint(road):
    coords = road.get("coordinates")
    if isinstance(coords, str):
        coords = json.loads(coords)
    if not coords:
        return None
    return coords[len(coords) // 2]


def road_polyline(road):
    coords = road.get("coordinates")
    if isinstance(coords, str):
        coords = json.loads(coords)
    return coords or []


def project_lonlat_to_local_m(ref_lat):
    """Return a (lon_scale, lat_scale) tuple for converting WGS84 deg
    offsets to metres in a small local neighbourhood centred at ref_lat.

    Cosine-corrected longitude scaling; valid up to ~100 km neighbourhoods
    with sub-percent error and so vastly more than enough for our 0-1 km
    FRONTS_ROAD context."""
    return (111_320.0 * math.cos(math.radians(ref_lat)), 110_540.0)


def polyline_min_distance_m(polyline_lonlat, point_lon, point_lat):
    """Min perpendicular distance from point (point_lon, point_lat) to a
    polyline given as a list of (lon, lat) tuples, computed in a local
    metre-projected plane centred at point_lat."""
    if len(polyline_lonlat) < 2:
        return float("inf")
    lon_s, lat_s = project_lonlat_to_local_m(point_lat)
    px = 0.0
    py = 0.0
    pts = [((lon - point_lon) * lon_s, (lat - point_lat) * lat_s)
           for lon, lat in polyline_lonlat]
    best = float("inf")
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 == 0:
            d = math.hypot(px - x1, py - y1)
        else:
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
            sx, sy = x1 + t * dx, y1 + t * dy
            d = math.hypot(px - sx, py - sy)
        if d < best:
            best = d
    return best


def assign_nearest_road(building, road_anchors):
    """Mimic the rule-engine's nearest-road assignment (entrance → road
    midpoint, planar Haversine)."""
    blon = (building.get("entrance_lon")
            if building.get("entrance_lon") not in (None, 0.0)
            else building.get("longitude"))
    blat = (building.get("entrance_lat")
            if building.get("entrance_lat") not in (None, 0.0)
            else building.get("latitude"))
    if blon is None:
        return None, None, None, None
    best_uid = None
    best_d2 = float("inf")
    for uid, rlon, rlat in road_anchors:
        dx = (rlon - blon) * 111_320.0 * math.cos(math.radians(blat))
        dy = (rlat - blat) * 110_540.0
        d2 = dx * dx + dy * dy
        if d2 < best_d2:
            best_d2 = d2
            best_uid = uid
    p2p_m = math.sqrt(best_d2)
    return best_uid, blon, blat, p2p_m


BINS = [
    ("≤50m",       0,      50),
    ("50-100m",    50,    100),
    ("100-200m",   100,   200),
    ("200-500m",   200,   500),
    ("500-1000m",  500,  1000),
    (">1000m",     1000, float("inf")),
]


def quantile(sorted_vals, q):
    if not sorted_vals:
        return float("nan")
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * q)))
    return sorted_vals[idx]


def main(n_per_bin: int = 50, seed: int = 42):
    print(f"[measure] Loading {DUMP}")
    with gzip.open(DUMP, "rb") as f:
        payload = json.loads(f.read().decode("utf-8"))

    buildings = payload["buildings"]
    roads = {r["uid"]: r for r in payload["roads"]}
    print(f"[measure] Buildings: {len(buildings):,}")
    print(f"[measure] Roads:     {len(roads):,}")

    # Build road anchor list (uid, midpoint lon, midpoint lat) — same as engine
    road_anchors = []
    for uid, r in roads.items():
        mid = road_midpoint(r)
        if mid is None:
            continue
        road_anchors.append((uid, mid[0], mid[1]))
    print(f"[measure] Road anchors: {len(road_anchors):,}")

    # Pass 1: compute point-to-point FRONTS_ROAD distance for every building.
    print("[measure] Pass 1 — point-to-point distances for every building...")
    per_building = []
    for b in buildings:
        uid, blon, blat, p2p = assign_nearest_road(b, road_anchors)
        if uid is None:
            continue
        per_building.append({
            "uid": b.get("uid"),
            "road_uid": uid,
            "blon": blon, "blat": blat,
            "p2p_m": p2p,
        })
    print(f"[measure]   measured: {len(per_building):,}")

    # Stratify: pick n_per_bin from each bin uniformly at random.
    random.seed(seed)
    sampled = []
    print(f"[measure] Pass 2 — stratified subsample (n={n_per_bin} per bin):")
    for label, lo, hi in BINS:
        bucket = [x for x in per_building if lo <= x["p2p_m"] < hi]
        if not bucket:
            print(f"  [{label:>11s}] available=0 — skipped")
            continue
        k = min(n_per_bin, len(bucket))
        chosen = random.sample(bucket, k)
        for c in chosen:
            c["bin"] = label
            sampled.append(c)
        print(f"  [{label:>11s}] available={len(bucket):>6,d}, sampled={k}")

    # Pass 3: for each sampled building, compute polyline-perpendicular dist
    print(f"[measure] Pass 3 — polyline-perpendicular for {len(sampled)} sampled rows...")
    for row in sampled:
        rd = roads[row["road_uid"]]
        poly = road_polyline(rd)
        d_perp = polyline_min_distance_m(poly, row["blon"], row["blat"])
        row["polyline_m"] = d_perp
        row["gain_x"] = (row["p2p_m"] / d_perp) if d_perp > 0 else float("inf")

    # Per-bin and overall statistics
    print("\n=== POLYLINE-PERPENDICULAR vs POINT-TO-POINT (stratified n=200) ===")
    print(f"{'Bin':<13s} {'n':>5s}  {'p2p median':>12s} {'p2p p95':>10s}  "
          f"{'polyline med':>13s} {'polyline p95':>13s}  {'gain (med)':>11s}")
    print("-" * 84)

    overall_p2p = []
    overall_poly = []
    overall_gain = []
    bin_rows = []
    for label, lo, hi in BINS:
        bucket = [r for r in sampled if r.get("bin") == label]
        if not bucket:
            continue
        p2p_sorted = sorted(r["p2p_m"] for r in bucket)
        pl_sorted = sorted(r["polyline_m"] for r in bucket)
        gain_sorted = sorted(r["gain_x"] for r in bucket if r["gain_x"] != float("inf"))
        p2p_med = quantile(p2p_sorted, 0.5)
        p2p_p95 = quantile(p2p_sorted, 0.95)
        pl_med = quantile(pl_sorted, 0.5)
        pl_p95 = quantile(pl_sorted, 0.95)
        gain_med = quantile(gain_sorted, 0.5) if gain_sorted else float("nan")
        bin_rows.append({
            "bin": label, "n": len(bucket),
            "p2p_median_m": p2p_med, "p2p_p95_m": p2p_p95,
            "polyline_median_m": pl_med, "polyline_p95_m": pl_p95,
            "gain_median_x": gain_med,
        })
        overall_p2p.extend(p2p_sorted)
        overall_poly.extend(pl_sorted)
        overall_gain.extend(gain_sorted)
        print(f"{label:<13s} {len(bucket):>5d}  {p2p_med:>10.1f} m {p2p_p95:>8.1f} m  "
              f"{pl_med:>11.1f} m {pl_p95:>11.1f} m  {gain_med:>9.2f}x")

    overall_p2p.sort(); overall_poly.sort(); overall_gain.sort()
    print("-" * 84)
    o_p2p_med = quantile(overall_p2p, 0.5)
    o_p2p_p95 = quantile(overall_p2p, 0.95)
    o_pl_med = quantile(overall_poly, 0.5)
    o_pl_p95 = quantile(overall_poly, 0.95)
    o_gain_med = quantile(overall_gain, 0.5) if overall_gain else float("nan")
    print(f"{'OVERALL':<13s} {len(overall_p2p):>5d}  "
          f"{o_p2p_med:>10.1f} m {o_p2p_p95:>8.1f} m  "
          f"{o_pl_med:>11.1f} m {o_pl_p95:>11.1f} m  "
          f"{o_gain_med:>9.2f}x")

    # Persist machine-readable summary for the paper to ingest
    summary = {
        "method": "stratified random subsample, n=50 per Table 9(c) bin (total 200 target)",
        "seed": seed,
        "n_total_sampled": len(sampled),
        "per_bin": bin_rows,
        "overall": {
            "n": len(overall_p2p),
            "p2p_median_m": o_p2p_med,
            "p2p_p95_m": o_p2p_p95,
            "polyline_median_m": o_pl_med,
            "polyline_p95_m": o_pl_p95,
            "gain_median_x": o_gain_med,
        },
    }
    OUTPUT.write_text(json.dumps(summary, indent=2, default=lambda x: None),
                      encoding="utf-8")
    print(f"\n[measure] Saved summary -> {OUTPUT}")


if __name__ == "__main__":
    main()
