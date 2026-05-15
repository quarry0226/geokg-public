"""Generate stratified+random visual-validation sample for FRONTS_ROAD.

Produces a CSV with 100 (50 random + 50 stratified by algorithmic verdict)
edges plus the polyline-aware top-1 distance for each. The polyline-aware
distance gives an algorithmic ground-truth proxy that does not require
manual KakaoMap labelling for every row.

Output columns:
  uid, building_pnu, road_uid, road_name, building_lon/lat,
  centroid_dist_m, polyline_dist_m, top1_polyline_road, top1_dist_m,
  algorithmic_verdict, sample_kind
"""
import csv
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.neo4j_client import db, set_region

random.seed(42)
set_region("yuseong")
g = db.graph

print("[Sampler] Loading all FRONTS_ROAD edges + Road polylines...")

edges = g.run(
    """
    MATCH (b:Building)-[:FRONTS_ROAD]->(r:Road)
    RETURN b.uid AS b_uid, b.pnu AS b_pnu,
           coalesce(b.entrance_lon, b.longitude) AS b_lon,
           coalesce(b.entrance_lat, b.latitude)  AS b_lat,
           b.entrance_lon IS NOT NULL AS has_entrance,
           b.road_address AS b_addr,
           r.uid AS r_uid, r.name AS r_name,
           r.coordinates AS r_coords
    """
).data()
print(f"  → {len(edges)} FRONTS_ROAD edges")

roads = g.run(
    "MATCH (r:Road) WHERE r.coordinates IS NOT NULL "
    "RETURN r.uid AS uid, r.name AS name, r.coordinates AS coords"
).data()
print(f"  → {len(roads)} Road polylines")

# parse coords once
road_polylines = {}
for r in roads:
    try:
        pts = json.loads(r["coords"])
        if pts and len(pts) >= 2:
            road_polylines[r["uid"]] = (r["name"], pts)
    except Exception:
        pass


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))


def point_to_segment_m(plon, plat, a_lon, a_lat, b_lon, b_lat):
    """Approximate perpendicular distance from p to segment a-b (meters)."""
    # use local equirectangular projection at p's latitude
    cos_lat = math.cos(math.radians(plat))
    M_LON = 111320 * cos_lat  # m per degree lon
    M_LAT = 110540            # m per degree lat
    px, py = plon * M_LON, plat * M_LAT
    ax, ay = a_lon * M_LON, a_lat * M_LAT
    bx, by = b_lon * M_LON, b_lat * M_LAT
    dx, dy = bx - ax, by - ay
    L2 = dx*dx + dy*dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px-ax)*dx + (py-ay)*dy) / L2))
    qx, qy = ax + t*dx, ay + t*dy
    return math.hypot(px - qx, py - qy)


def polyline_dist_m(plon, plat, polyline):
    """Min perpendicular distance from p to any segment of polyline."""
    best = float("inf")
    for i in range(len(polyline) - 1):
        a, b = polyline[i], polyline[i+1]
        d = point_to_segment_m(plon, plat, a[0], a[1], b[0], b[1])
        if d < best:
            best = d
    return best


def polyline_centroid(polyline):
    if not polyline:
        return None, None
    mid = polyline[len(polyline) // 2]
    return mid[0], mid[1]


print("[Sampler] Computing polyline-aware ground truth for each edge...")
enriched = []
for i, e in enumerate(edges):
    if i % 1000 == 0:
        print(f"  {i:,} / {len(edges):,}")
    if e["r_uid"] not in road_polylines:
        continue
    _, plyline = road_polylines[e["r_uid"]]
    centroid_dist = haversine_m(
        e["b_lon"], e["b_lat"],
        *polyline_centroid(plyline)
    )
    poly_dist = polyline_dist_m(e["b_lon"], e["b_lat"], plyline)

    # find true top-1 polyline-nearest road across ALL roads (slow but ok for sample)
    best_uid, best_name, best_dist = e["r_uid"], e["r_name"], poly_dist
    for ruid, (rname, ply) in road_polylines.items():
        if ruid == e["r_uid"]:
            continue
        d = polyline_dist_m(e["b_lon"], e["b_lat"], ply)
        if d < best_dist:
            best_uid, best_name, best_dist = ruid, rname, d

    # algorithmic verdict
    if best_uid == e["r_uid"] and poly_dist <= 200:
        verdict = "OK_NEAREST"
    elif poly_dist <= 200:
        verdict = "OK_NOT_NEAREST"
    else:
        verdict = "FAIL"

    enriched.append({
        "b_uid": e["b_uid"], "b_pnu": e["b_pnu"], "b_addr": e["b_addr"],
        "b_lon": e["b_lon"], "b_lat": e["b_lat"],
        "r_uid": e["r_uid"], "r_name": e["r_name"],
        "centroid_dist_m": round(centroid_dist, 1),
        "polyline_dist_m": round(poly_dist, 1),
        "top1_road_uid": best_uid,
        "top1_road_name": best_name,
        "top1_dist_m": round(best_dist, 1),
        "verdict": verdict,
    })

print(f"[Sampler] {len(enriched)} enriched (skipped {len(edges)-len(enriched)} without polyline)")

# Verdict distribution
from collections import Counter
vc = Counter(r["verdict"] for r in enriched)
print(f"[Sampler] Verdict distribution: {dict(vc)}")

# Stratified 50 + Random 50 sampling
by_verdict = {"OK_NEAREST": [], "OK_NOT_NEAREST": [], "FAIL": []}
for r in enriched:
    by_verdict[r["verdict"]].append(r)

stratified = []
for v in ["OK_NEAREST", "OK_NOT_NEAREST", "FAIL"]:
    pool = by_verdict[v]
    random.shuffle(pool)
    take = min(17, len(pool))
    for r in pool[:take]:
        r["sample_kind"] = "stratified"
        stratified.append(r)

# Random 50 (population-representative)
random_pool = list(enriched)
random.shuffle(random_pool)
existing_uids = {r["b_uid"] for r in stratified}
random_sample = []
for r in random_pool:
    if r["b_uid"] in existing_uids:
        continue
    r["sample_kind"] = "random"
    random_sample.append(r)
    if len(random_sample) >= 50:
        break

all_samples = stratified + random_sample
print(f"[Sampler] Final: {len(stratified)} stratified + {len(random_sample)} random = {len(all_samples)}")

out_dir = os.path.join(os.path.dirname(__file__), "..", "backend", "experiments", "results")
os.makedirs(out_dir, exist_ok=True)
out_csv = os.path.join(out_dir, "fronts_road_visual_sample.csv")
with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[
        "sample_kind", "verdict",
        "b_uid", "b_pnu", "b_addr", "b_lon", "b_lat",
        "r_uid", "r_name",
        "centroid_dist_m", "polyline_dist_m",
        "top1_road_uid", "top1_road_name", "top1_dist_m",
    ])
    w.writeheader()
    w.writerows(all_samples)
print(f"[Sampler] Wrote → {out_csv}")

# Summary precision estimates
print("\n=== Algorithmic ground-truth proxy precision ===")
n_ok = sum(1 for r in enriched if r["verdict"] == "OK_NEAREST")
n_lenient = sum(1 for r in enriched if r["verdict"] in ("OK_NEAREST", "OK_NOT_NEAREST"))
n_total = len(enriched)
print(f"  Strict (top-1 polyline + ≤200m): {n_ok:,} / {n_total:,} = {n_ok/n_total*100:.1f}%")
print(f"  Lenient (top-3 ≤200m or any ≤200m): {n_lenient:,} / {n_total:,} = {n_lenient/n_total*100:.1f}%")

# Wilson 95% CI for both
def wilson(k, n):
    if n == 0:
        return (0, 0)
    p = k/n
    z = 1.96
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n)) / denom
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    return (centre - half, centre + half)

lo, hi = wilson(n_ok, n_total)
print(f"  Strict 95% CI: [{lo:.3f}, {hi:.3f}]")
lo, hi = wilson(n_lenient, n_total)
print(f"  Lenient 95% CI: [{lo:.3f}, {hi:.3f}]")
