"""Recompute the entrance-anchored FRONTS_ROAD distance histogram for the
current Yuseong-gu corpus (n=18,106 buildings).

The bin counts previously published in Table 9(c) of the paper (3,616 + 8,204
+ 11,896 + 12,360 + 3,055 + 130 = 39,261) date from the pre-revision corpus
(58,409 / 39,261 FRONTS_ROAD edges) and are inconsistent with the current
``n = 18,106 edges`` label. This script measures the entrance-anchored
distance for the *current* Yuseong corpus and prints the histogram in the
same bin layout used by the paper, so the table cells can be updated.

The script reads the processed seed-dump under
``release/geokg-public/data/processed/yuseong.json.gz`` (which is the same
input that ``reseed_neo4j.py`` consumes) and reproduces the per-building
nearest-road assignment used by ``_enrich_fronts_road`` in
``backend/data/relationship_enrichment.py``.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import sys
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "release" / "geokg-public" / "data" / "processed" / "yuseong.json.gz"


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


def main():
    print(f"[measure] Loading {DUMP}")
    with gzip.open(DUMP, "rb") as f:
        payload = json.loads(f.read().decode("utf-8"))

    buildings = payload["buildings"]
    roads = payload["roads"]
    print(f"[measure] Buildings: {len(buildings):,}")
    print(f"[measure] Roads:     {len(roads):,}")

    # Build road midpoint table (same as relationship_enrichment._enrich_fronts_road)
    road_mids = []
    for r in roads:
        mid = road_midpoint(r)
        if mid is None:
            continue
        road_mids.append((r["uid"], mid[0], mid[1]))
    print(f"[measure] Roads with mid-point: {len(road_mids):,}")

    # For each building, find its nearest road by haversine and record the distance.
    # Use entrance point if available, else centroid.
    use_entrance = 0
    use_centroid = 0
    distances_m = []
    for b in buildings:
        if "entrance_lon" in b and b["entrance_lon"] is not None and b["entrance_lon"] != 0.0:
            blon = b["entrance_lon"]
            blat = b["entrance_lat"]
            use_entrance += 1
        else:
            blon = b.get("longitude")
            blat = b.get("latitude")
            use_centroid += 1
            if blon is None:
                continue
        best = float("inf")
        for uid, rlon, rlat in road_mids:
            # Approximate planar squared distance for ranking, then convert to m
            dx = (rlon - blon) * 111_320.0 * math.cos(math.radians(blat))
            dy = (rlat - blat) * 110_540.0
            d2 = dx * dx + dy * dy
            if d2 < best:
                best = d2
        # Convert back to haversine (already in m via planar approx; refine with haversine)
        distances_m.append(math.sqrt(best))

    print(f"[measure] Entrance-anchored: {use_entrance:,}")
    print(f"[measure] Centroid fallback: {use_centroid:,}")
    print(f"[measure] Distance samples : {len(distances_m):,}")

    # Bin counts (same edges as paper Table 9(c))
    bins = [
        ("≤ 50 m",        0,      50),
        ("50–100 m",      50,    100),
        ("100–200 m",     100,   200),
        ("200–500 m",     200,   500),
        ("500–1,000 m",   500,  1000),
        ("> 1,000 m",     1000, float("inf")),
    ]

    n = len(distances_m)
    print()
    print(f"=== FRONTS_ROAD distance histogram (n = {n:,} edges, entrance-anchored) ===")
    print(f"{'Bin':<15s} {'Count':>10s}  {'Share':>8s}")
    print("-" * 40)
    cumulative = 0
    for label, lo, hi in bins:
        c = sum(1 for d in distances_m if lo <= d < hi)
        cumulative += c
        pct = c / n * 100
        print(f"{label:<15s} {c:>10,d}  {pct:>7.2f}%")
    print("-" * 40)
    print(f"{'Total':<15s} {cumulative:>10,d}  {cumulative/n*100:>7.2f}%")

    distances_m.sort()
    median = distances_m[n // 2]
    p90 = distances_m[int(n * 0.9)]
    p95 = distances_m[int(n * 0.95)]
    mx = distances_m[-1]
    print()
    print(f"Summary: median {median:.1f} m, 90th pct {p90:.1f} m, "
          f"95th pct {p95:.1f} m, max {mx:.1f} m")

    # Tail count
    tail_500 = sum(1 for d in distances_m if d > 500)
    print(f"Tail (>500 m): {tail_500:,} edges "
          f"({tail_500/n*100:.2f}% of edges, "
          f"{tail_500/len(buildings)*100:.2f}% of buildings)")

    # Coverage at the entrance-anchored side
    print()
    print("Coverage of TL_SPBD_ENTRC entrance points:")
    print(f"  entrance-anchored: {use_entrance:,} ({use_entrance/len(buildings)*100:.1f}% of buildings)")
    print(f"  centroid fallback: {use_centroid:,} ({use_centroid/len(buildings)*100:.1f}% of buildings)")


if __name__ == "__main__":
    main()
