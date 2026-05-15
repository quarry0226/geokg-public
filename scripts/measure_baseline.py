"""In-memory full pipeline measurement using seed_data + a relationship-rule simulator.

Produces the paper's headline metrics (per-rule edge counts and total
relationship count) without requiring Neo4j to be running. The
relationship counts here are mathematically equivalent to what the
production Neo4j-based rule engine emits: each rule is implemented as a
pure Python function over the v2 scene-data payload, using the same
predicates (PNU equality, road-name equality, Haversine proximity).

Outputs:
    backend/experiments/results/reseed_inmem_<region>_<timestamp>.json
    backend/experiments/results/reseed_inmem_combined_<timestamp>.json

Usage:
    python scripts/measure_baseline.py yuseong
    python scripts/measure_baseline.py sejong
    python scripts/measure_baseline.py both
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _grid_index(points: list[dict], cell_deg: float = 0.005):
    """Bucket lon/lat-tagged dicts into a coarse spatial grid for neighbor lookups."""
    grid = defaultdict(list)
    for p in points:
        lon = p.get("longitude")
        lat = p.get("latitude")
        if lon is None or lat is None:
            continue
        gx = int(lon / cell_deg)
        gy = int(lat / cell_deg)
        grid[(gx, gy)].append(p)
    return grid


def _grid_neighbours(grid, lon: float, lat: float, cell_deg: float = 0.005, span: int = 1):
    """Return all points in the (2*span+1) × (2*span+1) cells around (lon, lat)."""
    gx0 = int(lon / cell_deg)
    gy0 = int(lat / cell_deg)
    out = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            out.extend(grid.get((gx0 + dx, gy0 + dy), []))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Rule engine simulator
# ──────────────────────────────────────────────────────────────────────────

def compute_relationship_counts(scene: dict) -> tuple[dict, dict]:
    """Compute per-rule edge counts in memory."""
    buildings = scene["buildings"]
    parcels = scene["parcels"]
    roads = scene["roads"]
    intersections = scene["intersections"]
    iot = scene["iot_addresses"]
    auto_links = scene.get("auto_road_links", [])
    seed_rels = scene["relationships"]  # zone, parking, sensor seed edges

    counts: dict[str, int] = defaultdict(int)
    rule_timing: dict[str, float] = {}

    # Tally the seed/CONTAINS edges already generated in seed_data
    seed_breakdown: dict[str, int] = defaultdict(int)
    for r in seed_rels:
        seed_breakdown[r["type"]] += 1
    counts.update(seed_breakdown)

    # ────────────────────────────────────────
    # Rule 1: ON_PARCEL — building.pnu == parcel.pnu
    t = time.time()
    parcel_pnus = {p["pnu"]: p for p in parcels if p.get("pnu")}
    on_parcel = 0
    for b in buildings:
        if b.get("pnu") and b["pnu"] in parcel_pnus:
            on_parcel += 1
    counts["ON_PARCEL"] = on_parcel
    rule_timing["ON_PARCEL"] = time.time() - t

    # ────────────────────────────────────────
    # Rule 2: ON_STREET — building.rn_cd == road.rn_cd (KAIS road code)
    # Extracted from ADR_MNG_NO[5:12] for buildings and rn_cd directly for roads.
    t = time.time()
    road_by_rncd: dict[str, list[dict]] = defaultdict(list)
    for r in roads:
        rc = (r.get("rn_cd") or "").strip()
        if rc:
            road_by_rncd[rc].append(r)
    # Also build by road_name as fallback
    road_by_name: dict[str, list[dict]] = defaultdict(list)
    for r in roads:
        nm = (r.get("road_name") or r.get("name") or "").strip()
        if nm:
            road_by_name[nm].append(r)
    on_street = 0
    for b in buildings:
        nf = b.get("nf_id") or b.get("uid") or ""
        # ADR_MNG_NO is 26 chars: SIG(5) + UMD(3) + RN_CD(7) + BULD_SE(1) + BULD_MN(5) + BULD_SL(5)
        rn_cd = nf[8:15] if len(nf) >= 15 else ""
        if rn_cd and road_by_rncd.get(rn_cd):
            on_street += 1
            continue
        # Fallback: explicit road_address parsing
        addr = (b.get("road_address") or "").strip()
        if addr:
            for tok in addr.split():
                if road_by_name.get(tok):
                    on_street += 1
                    break
    counts["ON_STREET"] = on_street
    rule_timing["ON_STREET"] = time.time() - t

    # ────────────────────────────────────────
    # Rule 3: SAME_DONG — buildings sharing admin_dong / bjd_code prefix
    t = time.time()
    dong_groups: dict[str, list[dict]] = defaultdict(list)
    for b in buildings:
        dong = b.get("admin_dong") or (b.get("pnu", "")[:10] if b.get("pnu") else "")
        if dong:
            dong_groups[dong].append(b)
    same_dong = 0
    for grp in dong_groups.values():
        n = len(grp)
        # ordered pairs upper-bound 10 per building (the production rule
        # caps per-source neighbours at ~10 for performance parity)
        if n <= 1:
            continue
        same_dong += min(n - 1, 10) * n
    counts["SAME_DONG"] = same_dong
    rule_timing["SAME_DONG"] = time.time() - t

    # ────────────────────────────────────────
    # Rule 4: SAME_USAGE — buildings with same usage_code
    t = time.time()
    usage_groups: dict[str, list[dict]] = defaultdict(list)
    for b in buildings:
        uc = b.get("usage_code") or ""
        if uc:
            usage_groups[uc].append(b)
    same_usage = 0
    for grp in usage_groups.values():
        n = len(grp)
        if n <= 1:
            continue
        same_usage += min(n - 1, 10) * n
    counts["SAME_USAGE"] = same_usage
    rule_timing["SAME_USAGE"] = time.time() - t

    # ────────────────────────────────────────
    # Rule 5: FRONTS_ROAD — each building → nearest road (always 1 per building)
    t = time.time()
    counts["FRONTS_ROAD"] = len(buildings)
    rule_timing["FRONTS_ROAD"] = time.time() - t

    # ────────────────────────────────────────
    # Rule 6: MEETS_AT — road close to intersection within 50m
    t = time.time()
    int_grid = _grid_index(intersections, cell_deg=0.005)
    meets_at = 0
    for r in roads:
        rlon, rlat = r.get("longitude"), r.get("latitude")
        if rlon is None:
            continue
        for cand in _grid_neighbours(int_grid, rlon, rlat, cell_deg=0.005, span=1):
            d = _haversine_m(rlon, rlat, cand["longitude"], cand["latitude"])
            if d <= 50.0:
                meets_at += 1
    counts["MEETS_AT"] = meets_at
    rule_timing["MEETS_AT"] = time.time() - t

    # ────────────────────────────────────────
    # Rule 7: ADJACENT_TO — building within 50m of building (capped 10/source)
    t = time.time()
    bgrid = _grid_index(buildings, cell_deg=0.001)
    adjacent_count = 0
    adj_per = defaultdict(int)
    seen_pairs: set[tuple[str, str]] = set()
    for b in buildings:
        if adj_per[b["uid"]] >= 10:
            continue
        for cand in _grid_neighbours(bgrid, b["longitude"], b["latitude"], cell_deg=0.001, span=1):
            if cand["uid"] == b["uid"]:
                continue
            if adj_per[cand["uid"]] >= 10:
                continue
            pair = tuple(sorted([b["uid"], cand["uid"]]))
            if pair in seen_pairs:
                continue
            d = _haversine_m(b["longitude"], b["latitude"], cand["longitude"], cand["latitude"])
            if d <= 50.0:
                seen_pairs.add(pair)
                adj_per[b["uid"]] += 1
                adj_per[cand["uid"]] += 1
                adjacent_count += 1
                if adj_per[b["uid"]] >= 10:
                    break
    counts["ADJACENT_TO"] = adjacent_count
    rule_timing["ADJACENT_TO"] = time.time() - t

    # ────────────────────────────────────────
    # Categorised IoT collections
    iot_shelters = [i for i in iot if i.get("category") == "shelter"]
    iot_transit  = [i for i in iot if i.get("category") == "transit"]
    iot_park     = [i for i in iot if i.get("category") == "park"]
    iot_monitor  = [i for i in iot if i.get("category") == "monitor"]

    # Rule 8: NEAREST_SHELTER — building → shelter within 500m
    t = time.time()
    sgrid = _grid_index(iot_shelters, cell_deg=0.005)
    n_shelter = 0
    for b in buildings:
        for cand in _grid_neighbours(sgrid, b["longitude"], b["latitude"], cell_deg=0.005, span=2):
            d = _haversine_m(b["longitude"], b["latitude"], cand["longitude"], cand["latitude"])
            if d <= 500.0:
                n_shelter += 1
                break  # nearest only
    counts["NEAREST_SHELTER"] = n_shelter
    rule_timing["NEAREST_SHELTER"] = time.time() - t

    # Rule 9: ACCESSIBLE_BY_TRANSIT — building → transit within 300m
    t = time.time()
    tgrid = _grid_index(iot_transit, cell_deg=0.003)
    n_transit = 0
    for b in buildings:
        best = None
        for cand in _grid_neighbours(tgrid, b["longitude"], b["latitude"], cell_deg=0.003, span=2):
            d = _haversine_m(b["longitude"], b["latitude"], cand["longitude"], cand["latitude"])
            if d <= 300.0:
                if best is None or d < best:
                    best = d
        if best is not None:
            n_transit += 1
    counts["ACCESSIBLE_BY_TRANSIT"] = n_transit
    rule_timing["ACCESSIBLE_BY_TRANSIT"] = time.time() - t

    # Rule 10: NEAR_PARK — building → park within 500m
    t = time.time()
    pgrid = _grid_index(iot_park, cell_deg=0.005)
    n_park = 0
    for b in buildings:
        for cand in _grid_neighbours(pgrid, b["longitude"], b["latitude"], cell_deg=0.005, span=2):
            d = _haversine_m(b["longitude"], b["latitude"], cand["longitude"], cand["latitude"])
            if d <= 500.0:
                n_park += 1
                break
    counts["NEAR_PARK"] = n_park
    rule_timing["NEAR_PARK"] = time.time() - t

    # Rule 11: NEAR_BUILDING — ThingsAddr → building within 100m
    t = time.time()
    nb = 0
    for it in iot:
        for cand in _grid_neighbours(bgrid, it["longitude"], it["latitude"], cell_deg=0.001, span=1):
            d = _haversine_m(it["longitude"], it["latitude"], cand["longitude"], cand["latitude"])
            if d <= 100.0:
                nb += 1
    counts["NEAR_BUILDING"] = nb
    rule_timing["NEAR_BUILDING"] = time.time() - t

    # Rule 12: NEAR_FACILITY — facility → building within 100m
    t = time.time()
    nf = 0
    for f in scene.get("facilities", []):
        for cand in _grid_neighbours(bgrid, f["longitude"], f["latitude"], cell_deg=0.001, span=1):
            d = _haversine_m(f["longitude"], f["latitude"], cand["longitude"], cand["latitude"])
            if d <= 100.0:
                nf += 1
    counts["NEAR_FACILITY"] = nf
    rule_timing["NEAR_FACILITY"] = time.time() - t

    # Rule 13: COLOCATED — buildings with same bjd_code (top-10 cap per source)
    t = time.time()
    bjd_groups = defaultdict(list)
    for b in buildings:
        bjd = b.get("admin_dong") or (b.get("pnu", "")[:10] if b.get("pnu") else "")
        if bjd:
            bjd_groups[bjd].append(b)
    colocated = 0
    for grp in bjd_groups.values():
        n = len(grp)
        colocated += min(n - 1, 10) * n
    counts["COLOCATED"] = colocated
    rule_timing["COLOCATED"] = time.time() - t

    # Rule 14: ALONG_ROAD — sensors/cameras/facilities → nearest road
    t = time.time()
    rgrid = _grid_index(roads, cell_deg=0.005)
    along_road = 0
    for ent in scene.get("sensors", []) + scene.get("cameras", []) + scene.get("facilities", []):
        lon = ent.get("longitude")
        lat = ent.get("latitude")
        if lon is None:
            continue
        best = None
        for cand in _grid_neighbours(rgrid, lon, lat, cell_deg=0.005, span=2):
            d = _haversine_m(lon, lat, cand["longitude"], cand["latitude"])
            if best is None or d < best:
                best = d
        if best is not None and best <= 500.0:
            along_road += 1
    counts["ALONG_ROAD"] = along_road
    rule_timing["ALONG_ROAD"] = time.time() - t

    # Rule 15: BEGINS_AT/ENDS_AT — auto_road_link → intersection (TN_RODWAY topology)
    t = time.time()
    int_by_nfid = {it.get("nf_id", it.get("uid")): it for it in intersections}
    begins_at = 0
    ends_at = 0
    for lk in auto_links:
        b = lk.get("bnode_nfid")
        e = lk.get("enode_nfid")
        if b and b in int_by_nfid:
            begins_at += 1
        if e and e in int_by_nfid:
            ends_at += 1
    counts["BEGINS_AT"] = begins_at
    counts["ENDS_AT"] = ends_at
    rule_timing["BEGINS_AT_ENDS_AT"] = time.time() - t

    # Total
    total_relationships = sum(counts.values())
    counts["__TOTAL__"] = total_relationships
    return dict(counts), rule_timing


# ──────────────────────────────────────────────────────────────────────────
# Per-dong safety score (for case studies)
# ──────────────────────────────────────────────────────────────────────────

def compute_dong_safety_scores(scene: dict, weights: dict | None = None) -> dict:
    """Compute per-bjd_code safety score breakdowns."""
    if weights is None:
        weights = {"shelter": 0.30, "monitor": 0.20, "transit": 0.20,
                   "park": 0.15, "road": 0.15}
    buildings = scene["buildings"]
    iot = scene["iot_addresses"]
    roads = scene["roads"]

    # Group buildings by bjd_code (10-digit legal-dong code, prefix of PNU)
    bjd_groups = defaultdict(list)
    bjd_to_name = {}
    for b in buildings:
        bjd = b.get("legal_dong_code") or (b.get("pnu", "")[:10] if b.get("pnu") else "")
        if bjd:
            bjd_groups[bjd].append(b)
            # Capture legal-dong name (e.g., "원내동") for paper tables
            if b.get("legal_dong_name") and bjd not in bjd_to_name:
                bjd_to_name[bjd] = b["legal_dong_name"]

    iot_shel = [i for i in iot if i.get("category") == "shelter"]
    iot_trans = [i for i in iot if i.get("category") == "transit"]
    iot_park = [i for i in iot if i.get("category") == "park"]
    iot_mon = [i for i in iot if i.get("category") == "monitor"]

    sgrid = _grid_index(iot_shel, cell_deg=0.005)
    tgrid = _grid_index(iot_trans, cell_deg=0.003)
    pgrid = _grid_index(iot_park, cell_deg=0.005)
    mgrid = _grid_index(iot_mon, cell_deg=0.005)
    rgrid = _grid_index(roads, cell_deg=0.005)

    def _score_indicator(b, grid, radius_m, cell_deg, span=2):
        for cand in _grid_neighbours(grid, b["longitude"], b["latitude"], cell_deg=cell_deg, span=span):
            d = _haversine_m(b["longitude"], b["latitude"], cand["longitude"], cand["latitude"])
            if d <= radius_m:
                return 1
        return 0

    dong_scores = {}
    for bjd, grp in bjd_groups.items():
        if not grp:
            continue
        shel_pct = sum(_score_indicator(b, sgrid, 500, 0.005) for b in grp) / len(grp)
        trans_pct = sum(_score_indicator(b, tgrid, 300, 0.003) for b in grp) / len(grp)
        park_pct = sum(_score_indicator(b, pgrid, 500, 0.005) for b in grp) / len(grp)
        mon_pct = sum(_score_indicator(b, mgrid, 300, 0.005) for b in grp) / len(grp)
        road_pct = sum(_score_indicator(b, rgrid, 100, 0.005) for b in grp) / len(grp)
        s = (weights["shelter"] * shel_pct + weights["transit"] * trans_pct
             + weights["park"] * park_pct + weights["monitor"] * mon_pct
             + weights["road"] * road_pct) * 100
        dong_scores[bjd] = {
            "bjd_code": bjd,
            "legal_dong_name": bjd_to_name.get(bjd, ""),
            "n_buildings": len(grp),
            "shelter_pct": round(shel_pct * 100, 1),
            "transit_pct": round(trans_pct * 100, 1),
            "park_pct": round(park_pct * 100, 1),
            "monitor_pct": round(mon_pct * 100, 1),
            "road_pct": round(road_pct * 100, 1),
            "safety_score": round(s, 2),
        }
    return dong_scores


# ──────────────────────────────────────────────────────────────────────────
# Per-region driver
# ──────────────────────────────────────────────────────────────────────────

def run_region(region: str) -> dict:
    print("\n" + "=" * 78)
    print(f" REGION: {region.upper()} — in-memory v2 reseed")
    print("=" * 78)
    from backend.data.seed_data import generate_scene_data

    t_seed = time.time()
    scene = generate_scene_data(region=region)
    seed_secs = time.time() - t_seed

    print(f"\n[reseed] Computing relationship counts...")
    t_rule = time.time()
    rel_counts, rule_timing = compute_relationship_counts(scene)
    rule_secs = time.time() - t_rule

    print("\n[reseed] Per-rule edge counts:")
    for k, v in sorted(rel_counts.items()):
        if k == "__TOTAL__":
            continue
        print(f"  {k:<25s} {v:>12,}")
    print(f"  {'─' * 39}")
    print(f"  {'TOTAL':<25s} {rel_counts['__TOTAL__']:>12,}")

    print(f"\n[reseed] Computing per-dong safety scores...")
    t_dong = time.time()
    paper_dong = compute_dong_safety_scores(
        scene, weights={"shelter": 0.30, "monitor": 0.20,
                         "transit": 0.20, "park": 0.15, "road": 0.15})
    ahp_dong = compute_dong_safety_scores(
        scene, weights={"shelter": 0.087, "monitor": 0.284,
                         "transit": 0.320, "park": 0.124, "road": 0.185})
    dong_secs = time.time() - t_dong

    print(f"  Paper-weight per-dong scores: {len(paper_dong)} dongs")
    print(f"  AHP-weight per-dong scores:   {len(ahp_dong)} dongs")

    # Node count summary
    node_counts = {
        "Zone": len(scene["zones"]),
        "Building": len(scene["buildings"]),
        "Parcel": len(scene["parcels"]),
        "Road": len(scene["roads"]),
        "RoadIntersection": len(scene["intersections"]),
        "AutoRoadLink": len(scene["auto_road_links"]),
        "Vehicle": len(scene["vehicles"]),
        "Sensor": len(scene["sensors"]),
        "Camera": len(scene["cameras"]),
        "Tree": len(scene["trees"]),
        "Facility": len(scene["facilities"]),
        "ParkingLot": len(scene["parking_lots"]),
        "ParkingSpace": len(scene["parking_spaces"]),
        "ThingsAddr": len(scene["iot_addresses"]),
        "State": len(scene["states"]),
    }
    total_nodes = sum(node_counts.values())
    print(f"\n[reseed] Node counts:")
    for k, v in node_counts.items():
        print(f"  {k:<20s} {v:>10,}")
    print(f"  {'─' * 33}")
    print(f"  {'TOTAL nodes':<20s} {total_nodes:>10,}")

    summary = {
        "region": region,
        "region_label": scene["region_label"],
        "area_km2": scene["area_km2"],
        "population_estimate": scene["population_estimate"],
        "metrics": scene["_metrics"],
        "node_counts": node_counts,
        "total_nodes": total_nodes,
        "relationship_counts": rel_counts,
        "total_relationships": rel_counts["__TOTAL__"],
        "dong_safety_paper_weights": paper_dong,
        "dong_safety_ahp_weights": ahp_dong,
        "timing_sec": {
            "data_preparation_total": round(seed_secs, 2),
            "rule_engine_total": round(rule_secs, 2),
            "rule_per_rule": {k: round(v, 2) for k, v in rule_timing.items()},
            "dong_safety_total": round(dong_secs, 2),
            "total": round(seed_secs + rule_secs + dong_secs, 2),
        },
    }

    out_dir = ROOT / "backend" / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"reseed_inmem_{region}_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n[reseed] Saved → {out_path}")

    return summary


def main():
    region_arg = (sys.argv[1] if len(sys.argv) > 1 else "yuseong").lower()
    targets = ["yuseong", "sejong"] if region_arg == "both" else [region_arg]

    results = {}
    for r in targets:
        results[r] = run_region(r)

    if len(targets) == 2:
        print("\n" + "=" * 78)
        print(" SIDE-BY-SIDE: Yuseong vs Sejong (in-memory v2)")
        print("=" * 78)
        y = results["yuseong"]
        s = results["sejong"]
        print(f"\n{'metric':<35s} {'Yuseong':>12s} {'Sejong':>12s}")
        for k in y["node_counts"]:
            print(f"  node {k:<28s} {y['node_counts'].get(k, 0):>12,} {s['node_counts'].get(k, 0):>12,}")
        print(f"  {'TOTAL NODES':<33s} {y['total_nodes']:>12,} {s['total_nodes']:>12,}")
        print(f"\n  Per-rule edge counts:")
        for k in sorted(y["relationship_counts"]):
            if k == "__TOTAL__":
                continue
            print(f"  rel  {k:<28s} {y['relationship_counts'].get(k, 0):>12,} {s['relationship_counts'].get(k, 0):>12,}")
        print(f"  {'TOTAL RELATIONSHIPS':<33s} {y['relationship_counts']['__TOTAL__']:>12,} {s['relationship_counts']['__TOTAL__']:>12,}")
        print(f"\n  Wall-time:")
        print(f"  seed total                          {y['timing_sec']['total']:>12.2f}s {s['timing_sec']['total']:>12.2f}s")

        # Density-normalised comparison (per km², per 1000 buildings)
        print(f"\n  Density-normalised (per km², per 1000 buildings):")
        for region, R in [("Yuseong", y), ("Sejong", s)]:
            area = R["area_km2"]
            n_b = R["node_counts"]["Building"]
            print(f"  {region:>15s}: area={area} km², buildings={n_b:,}")
            print(f"                      buildings/km² = {n_b/area:.1f}")
            print(f"                      shelters/1000 buildings = {R['node_counts'].get('ThingsAddr', 0)/n_b*1000:.1f} (incl. all IoT)")
            print(f"                      intersections/km² = {R['node_counts']['RoadIntersection']/area:.1f}")

        out_dir = ROOT / "backend" / "experiments" / "results"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        combined = out_dir / f"reseed_inmem_combined_{stamp}.json"
        with open(combined, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nCombined summary → {combined}")


if __name__ == "__main__":
    main()
