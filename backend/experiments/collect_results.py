"""
Collect analysis case study results for k-GeoKG paper.

Experiment 4: Urban Analysis Case Studies
  - Case 1: Dong comparison (행정동별 인프라 점수)
  - Case 2: Coverage dead-zone analysis (대피시설/교통/공원 사각지대)
  - Case 3: Road closure impact simulation
  - Case 4: Safety profile examples
  - Case 5: Road network vs spatial path comparison
  - Case 6: KG connectivity analysis

Usage:
  python -m backend.experiments.collect_results
"""

import sys
import os
import json
import time
import requests
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:8000/api/kg"
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def _save_result(name, data):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"{name}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  Result saved: {path}")
    return path


def _get(endpoint, params=None):
    """GET request to local API."""
    url = f"{BASE_URL}{endpoint}"
    try:
        t0 = time.time()
        resp = requests.get(url, params=params, timeout=120)
        elapsed = time.time() - t0
        resp.raise_for_status()
        return resp.json(), elapsed
    except Exception as e:
        print(f"  ERROR: {url} → {e}")
        return None, 0


# ═══════════════════════════════════════════════════════════════════
# Case 1: Dong Comparison (행정동별 인프라 비교)
# ═══════════════════════════════════════════════════════════════════

def case_1_dong_comparison():
    print("\n[Case 1] Dong Comparison (행정동별 인프라 비교)...")
    data, elapsed = _get("/analytics/dong_comparison")
    if not data:
        return None

    dongs = data.get("dongs", [])
    result = {
        "case": "1_dong_comparison",
        "api_endpoint": "/analytics/dong_comparison",
        "response_time_s": round(elapsed, 3),
        "total_buildings": data.get("total_buildings", 0),
        "dong_count": len(dongs),
        "city_avg": data.get("city_avg", {}),
        "data": data,
    }

    # Print summary
    print(f"  Response time: {elapsed:.2f}s")
    print(f"  Total buildings: {data.get('total_buildings', 0)}")
    print(f"  Dongs analyzed: {len(dongs)}")
    if dongs:
        print(f"  {'Dong':<15} {'Total':>7} {'Score':>7} {'Transit':>8} {'Shelter':>8} {'Park':>7}")
        print(f"  {'─' * 58}")
        for d in sorted(dongs, key=lambda x: -x.get("overall_score", 0)):
            print(f"  {d.get('dong', '?'):<15} "
                  f"{d.get('total', 0):>7} "
                  f"{d.get('overall_score', 0):>7.1f} "
                  f"{d.get('transit_pct', 0):>7.1f}% "
                  f"{d.get('shelter_pct', 0):>7.1f}% "
                  f"{d.get('park_pct', 0):>6.1f}%")
    if data.get("city_avg"):
        ca = data["city_avg"]
        print(f"\n  City avg: score={ca.get('overall_score', 0):.1f}, "
              f"transit={ca.get('transit_pct', 0):.1f}%, "
              f"shelter={ca.get('shelter_pct', 0):.1f}%, "
              f"park={ca.get('park_pct', 0):.1f}%")

    return _save_result("exp4_case1_dong", result)


# ═══════════════════════════════════════════════════════════════════
# Case 2: Coverage Dead-Zone Analysis (사각지대 식별)
# ═══════════════════════════════════════════════════════════════════

def case_2_coverage_deadzone():
    print("\n[Case 2] Coverage Dead-Zone Analysis (사각지대 식별)...")
    facility_types = ["shelter", "transit", "park"]
    results = {}

    for ftype in facility_types:
        data, elapsed = _get("/coverage", params={"facility_type": ftype})
        if data:
            summary = data.get("summary", {})
            total = summary.get("total", 0)
            gap_count = summary.get("gap_count", 0)
            gap_ratio = summary.get("gap_ratio", 0)
            covered = total - gap_count
            coverage_pct = round(100 - gap_ratio, 1)

            results[ftype] = {
                "response_time_s": round(elapsed, 3),
                "total_buildings": total,
                "covered_buildings": covered,
                "uncovered_buildings": gap_count,
                "coverage_pct": coverage_pct,
                "gap_ratio_pct": gap_ratio,
                "by_dong": data.get("by_dong", []),
            }
            print(f"  {ftype}: {coverage_pct:.1f}% coverage "
                  f"({covered}/{total}, gap={gap_count}), "
                  f"{elapsed:.2f}s")

    result = {
        "case": "2_coverage_deadzone",
        "api_endpoint": "/coverage",
        "facility_types_tested": facility_types,
        "results": results,
    }
    return _save_result("exp4_case2_coverage", result)


# ═══════════════════════════════════════════════════════════════════
# Case 3: Road Closure Impact Simulation
# ═══════════════════════════════════════════════════════════════════

def case_3_road_impact():
    print("\n[Case 3] Road Closure Impact Simulation...")

    from backend.db.neo4j_client import db

    # Find top 5 roads by relationship count (most connected)
    top_roads = db.query(
        """
        MATCH (r:Road)
        OPTIONAL MATCH (r)<-[rel]-()
        WITH r, count(rel) AS degree
        ORDER BY degree DESC
        LIMIT 5
        RETURN r.uid AS uid, r.name AS name, degree
        """
    )

    if not top_roads:
        print("  No roads found.")
        return None

    road_impacts = []
    for road in top_roads:
        data, elapsed = _get("/road_impact", params={"road_uid": road["uid"]})
        if data:
            by_label = data.get("by_label", {})
            total_affected = data.get("total_affected", 0)
            critical_count = data.get("critical_count", 0)

            impact = {
                "road_uid": road["uid"],
                "road_name": road["name"],
                "degree": road["degree"],
                "response_time_s": round(elapsed, 3),
                "total_affected": total_affected,
                "critical_count": critical_count,
                "by_label": by_label,
            }
            road_impacts.append(impact)
            bldg = by_label.get("Building", 0)
            iot = by_label.get("ThingsAddr", 0)
            ri = by_label.get("RoadIntersection", 0)
            print(f"  {road['name']}: total={total_affected}, "
                  f"buildings={bldg}, IoT={iot}, intersections={ri}, "
                  f"critical={critical_count}, {elapsed:.2f}s")

    result = {
        "case": "3_road_impact",
        "api_endpoint": "/road_impact",
        "roads_tested": len(road_impacts),
        "impacts": road_impacts,
    }
    return _save_result("exp4_case3_road_impact", result)


# ═══════════════════════════════════════════════════════════════════
# Case 4: Safety Profile Examples
# ═══════════════════════════════════════════════════════════════════

def case_4_safety_profile():
    print("\n[Case 4] Safety Profile Examples...")

    from backend.db.neo4j_client import db

    # Sample diverse buildings (different types/dongs)
    sample_buildings = db.query(
        """
        MATCH (b:Building)
        WHERE b.admin_dong IS NOT NULL AND b.building_type IS NOT NULL
              AND b.longitude IS NOT NULL
        WITH b.admin_dong AS dong, collect(b.uid)[0] AS uid
        RETURN uid
        LIMIT 5
        """
    )

    if not sample_buildings:
        # Fallback: just get any 5 buildings
        sample_buildings = db.query(
            "MATCH (b:Building) WHERE b.longitude IS NOT NULL RETURN b.uid AS uid LIMIT 5"
        )

    profiles = []
    for bldg in sample_buildings:
        data, elapsed = _get(f"/safety_profile", params={"uid": bldg["uid"]})
        if data:
            scores = data.get("scores", {})
            entity = data.get("entity", {})
            profile = {
                "uid": bldg["uid"],
                "name": entity.get("name", ""),
                "response_time_s": round(elapsed, 3),
                "overall_score": scores.get("overall", 0),
                "scores": scores,
            }
            profiles.append(profile)
            print(f"  {bldg['uid']}: overall={scores.get('overall', 0):.1f}, "
                  f"shelter={scores.get('shelter', 0)}, "
                  f"transit={scores.get('transit', 0)}, "
                  f"park={scores.get('park', 0)}, "
                  f"road={scores.get('road', 0)}, "
                  f"{elapsed:.2f}s")

    result = {
        "case": "4_safety_profile",
        "api_endpoint": "/safety_profile",
        "buildings_tested": len(profiles),
        "profiles": profiles,
    }
    return _save_result("exp4_case4_safety", result)


# ═══════════════════════════════════════════════════════════════════
# Case 5: Path Comparison (Road Network vs Spatial)
# ═══════════════════════════════════════════════════════════════════

def case_5_path_comparison():
    print("\n[Case 5] Path Comparison (road_network vs spatial)...")

    from backend.db.neo4j_client import db

    # Find two buildings that are connected to roads
    pairs = db.query(
        """
        MATCH (b1:Building)-[:ON_STREET]->(r1:Road)
        MATCH (b2:Building)-[:ON_STREET]->(r2:Road)
        WHERE b1.uid <> b2.uid AND r1.uid <> r2.uid
              AND b1.longitude IS NOT NULL AND b2.longitude IS NOT NULL
        WITH b1, b2,
             point.distance(
                point({longitude: b1.longitude, latitude: b1.latitude}),
                point({longitude: b2.longitude, latitude: b2.latitude})
             ) AS dist_m
        WHERE dist_m > 500 AND dist_m < 3000
        RETURN b1.uid AS from_uid, b2.uid AS to_uid, dist_m
        LIMIT 3
        """
    )

    if not pairs:
        print("  No suitable building pairs found.")
        return None

    comparisons = []
    for pair in pairs:
        from_uid = pair["from_uid"]
        to_uid = pair["to_uid"]

        # Spatial path
        spatial_data, spatial_time = _get("/path", params={
            "from_uid": from_uid, "to_uid": to_uid, "mode": "spatial"
        })

        # Road network path
        road_data, road_time = _get("/path", params={
            "from_uid": from_uid, "to_uid": to_uid, "mode": "road_network"
        })

        # All relationships path
        all_data, all_time = _get("/path", params={
            "from_uid": from_uid, "to_uid": to_uid, "mode": "all"
        })

        comp = {
            "from_uid": from_uid,
            "to_uid": to_uid,
            "euclidean_dist_m": round(pair["dist_m"], 1),
            "spatial": {
                "response_time_s": round(spatial_time, 3),
                "hops": spatial_data.get("hops", 0) if spatial_data else None,
                "path_found": bool(spatial_data and spatial_data.get("hops", 0) > 0),
            },
            "road_network": {
                "response_time_s": round(road_time, 3),
                "hops": road_data.get("hops", 0) if road_data else None,
                "from_road": road_data.get("from_road", "") if road_data else None,
                "to_road": road_data.get("to_road", "") if road_data else None,
                "road_path": road_data.get("road_path", []) if road_data else [],
                "path_found": bool(road_data and road_data.get("hops", 0) > 0),
            },
            "all_relationships": {
                "response_time_s": round(all_time, 3),
                "hops": all_data.get("hops", 0) if all_data else None,
                "path_found": bool(all_data and all_data.get("hops", 0) > 0),
            },
        }
        comparisons.append(comp)
        print(f"  {from_uid} → {to_uid} (eucl={pair['dist_m']:.0f}m): "
              f"spatial={comp['spatial']['hops']} hops, "
              f"road_net={comp['road_network']['hops']} hops, "
              f"all={comp['all_relationships']['hops']} hops")

    result = {
        "case": "5_path_comparison",
        "api_endpoint": "/path",
        "comparisons": comparisons,
    }
    return _save_result("exp4_case5_path", result)


# ═══════════════════════════════════════════════════════════════════
# Case 6: KG Connectivity Analysis
# ═══════════════════════════════════════════════════════════════════

def case_6_connectivity():
    print("\n[Case 6] KG Connectivity Analysis...")

    from backend.db.neo4j_client import db

    # Directly query connectivity stats from Neo4j
    result = {
        "case": "6_connectivity",
        "timestamp": datetime.now().isoformat(),
    }

    t0 = time.time()

    # Total stats
    stats = db.stats()
    result["total_nodes"] = stats.get("node_count", 0)
    result["total_relationships"] = stats.get("relationship_count", 0)

    # Isolated nodes (no relationships)
    isolated = db.query("MATCH (n) WHERE NOT (n)--() RETURN count(n) AS cnt")
    result["isolated_nodes"] = isolated[0]["cnt"] if isolated else 0

    # Average degree
    avg_deg = db.query(
        "MATCH (n) WITH n, size([(n)--() | 1]) AS deg "
        "RETURN avg(deg) AS avg_deg, max(deg) AS max_deg, min(deg) AS min_deg"
    )
    if avg_deg:
        result["avg_degree"] = round(avg_deg[0]["avg_deg"], 2) if avg_deg[0]["avg_deg"] else 0
        result["max_degree"] = avg_deg[0]["max_deg"]
        result["min_degree"] = avg_deg[0]["min_deg"]

    # Degree distribution by label
    label_degrees = {}
    for label in stats.get("labels", []):
        deg = db.query(
            f"MATCH (n:{label}) WITH n, size([(n)--() | 1]) AS deg "
            f"RETURN avg(deg) AS avg_deg, count(n) AS cnt"
        )
        if deg and deg[0]["cnt"] > 0:
            label_degrees[label] = {
                "count": deg[0]["cnt"],
                "avg_degree": round(deg[0]["avg_deg"], 2) if deg[0]["avg_deg"] else 0,
            }
    result["label_degrees"] = label_degrees

    elapsed = time.time() - t0
    result["elapsed_s"] = round(elapsed, 3)

    print(f"  Elapsed: {elapsed:.2f}s")
    print(f"  Total nodes: {result['total_nodes']:,}")
    print(f"  Total relationships: {result['total_relationships']:,}")
    print(f"  Isolated nodes: {result['isolated_nodes']}")
    print(f"  Avg degree: {result.get('avg_degree', '?')}")
    print(f"  Max degree: {result.get('max_degree', '?')}")

    if label_degrees:
        print(f"\n  {'Label':<25} {'Count':>8} {'Avg Degree':>12}")
        print(f"  {'─' * 48}")
        for label, info in sorted(label_degrees.items(), key=lambda x: -x[1]["avg_degree"]):
            print(f"  {label:<25} {info['count']:>8,} {info['avg_degree']:>12.2f}")

    return _save_result("exp4_case6_connectivity", result)


# ═══════════════════════════════════════════════════════════════════
# Supplementary: KG Statistics Summary
# ═══════════════════════════════════════════════════════════════════

def collect_kg_statistics():
    """Collect comprehensive KG statistics for paper tables."""
    print("\n[Stats] Collecting KG statistics...")

    from backend.db.neo4j_client import db

    result = {
        "timestamp": datetime.now().isoformat(),
        "db_stats": db.stats(),
        "node_counts_by_label": {},
        "relationship_counts_by_type": {},
    }

    # Node counts by label
    labels = db.query("CALL db.labels() YIELD label RETURN label ORDER BY label")
    for row in (labels or []):
        label = row["label"]
        cnt = db.query(f"MATCH (n:{label}) RETURN count(n) AS c")
        result["node_counts_by_label"][label] = cnt[0]["c"] if cnt else 0

    # Relationship counts by type
    rel_types = db.query("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType")
    for row in (rel_types or []):
        rt = row["relationshipType"]
        cnt = db.query(f"MATCH ()-[r:{rt}]->() RETURN count(r) AS c")
        result["relationship_counts_by_type"][rt] = cnt[0]["c"] if cnt else 0

    # Print
    print(f"\n  Node counts by label:")
    total_nodes = 0
    for label, cnt in sorted(result["node_counts_by_label"].items(), key=lambda x: -x[1]):
        print(f"    {label:<25} {cnt:>10,}")
        total_nodes += cnt
    print(f"    {'TOTAL':<25} {total_nodes:>10,}")

    print(f"\n  Relationship counts by type:")
    total_rels = 0
    for rt, cnt in sorted(result["relationship_counts_by_type"].items(), key=lambda x: -x[1]):
        print(f"    {rt:<30} {cnt:>10,}")
        total_rels += cnt
    print(f"    {'TOTAL':<30} {total_rels:>10,}")

    return _save_result("kg_statistics", result)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  k-GeoKG Paper: Analysis Case Study Data Collection")
    print("=" * 70)

    t_start = time.time()

    # Collect KG stats first
    collect_kg_statistics()

    # Run all cases
    case_1_dong_comparison()
    case_2_coverage_deadzone()
    case_3_road_impact()
    case_4_safety_profile()
    case_5_path_comparison()
    case_6_connectivity()

    total = time.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"  All case studies completed in {total:.1f}s")
    print(f"  Results saved to: {RESULTS_DIR}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
