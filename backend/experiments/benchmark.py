"""
Benchmark script for k-GeoKG paper experiments.

Experiments:
  1. KG construction time breakdown (full pipeline)
  2. Rule engine scalability test (10/25/50/75/100% data subsets)
  3. Per-strategy performance comparison

Usage:
  python -m backend.experiments.benchmark --experiment 1   # Full construction timing
  python -m backend.experiments.benchmark --experiment 2   # Scalability test
  python -m backend.experiments.benchmark --experiment 3   # Per-strategy comparison
  python -m backend.experiments.benchmark --experiment all # Run all
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime

# Ensure project root on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def _save_result(name, data):
    """Save experiment result to JSON file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"{name}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[Benchmark] Result saved: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════
# Experiment 1: Full KG Construction Time Breakdown
# ═══════════════════════════════════════════════════════════════════

def experiment_1_construction_timing():
    """Measure end-to-end KG construction time with phase breakdown.

    Phases measured:
      - Data preparation (SHP loading, coordinate transforms, enrichment)
      - Neo4j node insertion (per entity type)
      - Seed relationship insertion
      - Rule engine execution (per rule)
    """
    print("=" * 70)
    print("  Experiment 1: KG Construction Time Breakdown")
    print("=" * 70)

    from backend.db.neo4j_client import db
    from backend.geokg.builder import GeoKGBuilder
    from backend.data.seed_data import generate_scene_data

    t_total = time.time()

    # Phase 1: Data Preparation
    print("\n[Phase 1] Data Preparation...")
    t0 = time.time()
    scene_data = generate_scene_data()
    data_prep_time = time.time() - t0
    data_prep_timing = scene_data.get("_timing", {})

    # Collect entity counts
    entity_counts = {
        "Building": len(scene_data.get("buildings", [])),
        "Parcel": len(scene_data.get("parcels", [])),
        "Road": len(scene_data.get("roads", [])),
        "RoadIntersection": len(scene_data.get("intersections", [])),
        "ThingsAddr": len(scene_data.get("iot_addresses", [])),
        "Vehicle": len(scene_data.get("vehicles", [])),
        "Sensor": len(scene_data.get("sensors", [])),
        "Camera": len(scene_data.get("cameras", [])),
        "Tree": len(scene_data.get("trees", [])),
        "Facility": len(scene_data.get("facilities", [])),
        "ParkingLot": len(scene_data.get("parking_lots", [])),
        "ParkingSpace": len(scene_data.get("parking_spaces", [])),
        "Zone": len(scene_data.get("zones", [])),
    }
    total_nodes = sum(entity_counts.values())
    total_seed_rels = len(scene_data.get("relationships", []))

    print(f"\n[Phase 1] Data prepared: {total_nodes} nodes, "
          f"{total_seed_rels} seed relationships in {data_prep_time:.2f}s")

    # Phase 2: Neo4j Build (clear + schema + insert + rule engine)
    print("\n[Phase 2] Neo4j Build...")
    builder = GeoKGBuilder()
    builder.clear()
    builder.initialize()

    t0 = time.time()
    build_timing = builder.build_from_data(scene_data)
    neo4j_build_time = time.time() - t0

    total_elapsed = time.time() - t_total

    # Final DB stats
    final_stats = db.stats()

    result = {
        "experiment": "1_construction_timing",
        "timestamp": datetime.now().isoformat(),
        "total_elapsed_s": round(total_elapsed, 3),
        "entity_counts": entity_counts,
        "total_nodes": total_nodes,
        "total_seed_relationships": total_seed_rels,
        "final_db_stats": final_stats,
        "data_preparation": {
            "total_s": round(data_prep_time, 3),
            "phases": data_prep_timing,
        },
        "neo4j_build": build_timing.get("neo4j_build", {}),
        "rule_engine": build_timing.get("rule_engine", {}),
    }

    print(f"\n{'=' * 70}")
    print(f"  Experiment 1 Complete")
    print(f"  Total: {total_elapsed:.2f}s")
    print(f"  Nodes: {final_stats.get('node_count', '?')}")
    print(f"  Relationships: {final_stats.get('relationship_count', '?')}")
    print(f"{'=' * 70}")

    return _save_result("exp1_construction", result)


# ═══════════════════════════════════════════════════════════════════
# Experiment 2: Rule Engine Scalability Test
# ═══════════════════════════════════════════════════════════════════

def experiment_2_scalability():
    """Test rule engine performance at different data scales.

    Runs rule engine on 10%, 25%, 50%, 75%, 100% of building data
    and measures: execution time, relationship count, memory patterns.
    """
    print("=" * 70)
    print("  Experiment 2: Rule Engine Scalability Test")
    print("=" * 70)

    from backend.db.neo4j_client import db
    from backend.geokg.builder import GeoKGBuilder
    from backend.data.seed_data import generate_scene_data
    from backend.geokg.relationship_engine import enrich_by_rules, _clear_label_cache

    # Generate full data once
    print("\n[Step 1] Generating full dataset...")
    t0 = time.time()
    full_data = generate_scene_data()
    data_gen_time = time.time() - t0
    print(f"  Data generated in {data_gen_time:.2f}s")

    all_buildings = full_data["buildings"]
    all_parcels = full_data["parcels"]
    all_iot = full_data["iot_addresses"]
    total_buildings = len(all_buildings)
    total_parcels = len(all_parcels)
    total_iot = len(all_iot)

    percentages = [10, 25, 50, 75, 100]
    scale_results = []

    for pct in percentages:
        print(f"\n{'─' * 60}")
        print(f"  Scale test: {pct}% of data")
        print(f"{'─' * 60}")

        # Sample data
        n_bldg = max(1, int(total_buildings * pct / 100))
        n_parcels = max(1, int(total_parcels * pct / 100))
        n_iot = max(1, int(total_iot * pct / 100))

        subset = {**full_data}
        subset["buildings"] = all_buildings[:n_bldg]
        subset["parcels"] = all_parcels[:n_parcels]
        subset["iot_addresses"] = all_iot[:n_iot]

        # Rebuild relationships based on subset (filter by uid existence)
        valid_uids = set()
        for key in ["buildings", "parcels", "iot_addresses", "roads", "intersections",
                     "zones", "vehicles", "sensors", "cameras", "trees", "facilities",
                     "parking_lots", "parking_spaces"]:
            for item in subset.get(key, []):
                valid_uids.add(item["uid"])
        # Filter relationships
        subset["relationships"] = [
            r for r in full_data["relationships"]
            if r["from"] in valid_uids and r["to"] in valid_uids
        ]

        print(f"  Buildings: {n_bldg}, Parcels: {n_parcels}, IoT: {n_iot}")
        print(f"  Seed rels: {len(subset['relationships'])}")

        # Clear and rebuild
        builder = GeoKGBuilder()
        builder.clear()
        builder.initialize()

        # Insert nodes only (no rule engine)
        t_node = time.time()
        # Use a modified build that skips rule engine
        _build_nodes_only(builder, subset)
        node_insert_time = time.time() - t_node

        # Get node count
        pre_stats = db.stats()
        print(f"  Nodes inserted: {pre_stats.get('node_count', '?')} in {node_insert_time:.2f}s")

        # Run rule engine with timing
        t_rule = time.time()
        _clear_label_cache()
        rule_timing = enrich_by_rules(db)
        rule_time = time.time() - t_rule

        # Final stats
        post_stats = db.stats()
        total_rels = post_stats.get("relationship_count", 0)

        scale_results.append({
            "percentage": pct,
            "buildings": n_bldg,
            "parcels": n_parcels,
            "iot_addresses": n_iot,
            "total_nodes": pre_stats.get("node_count", 0),
            "seed_relationships": len(subset["relationships"]),
            "node_insert_time_s": round(node_insert_time, 3),
            "rule_engine_time_s": round(rule_time, 3),
            "total_relationships": total_rels,
            "rule_created_relationships": total_rels - len(subset["relationships"]),
            "rule_engine_detail": rule_timing,
        })

        print(f"  Rule engine: {rule_time:.2f}s → {total_rels} total relationships")

    result = {
        "experiment": "2_scalability",
        "timestamp": datetime.now().isoformat(),
        "total_buildings_100pct": total_buildings,
        "total_parcels_100pct": total_parcels,
        "total_iot_100pct": total_iot,
        "scale_results": scale_results,
    }

    # Print summary table
    print(f"\n{'=' * 70}")
    print(f"  Experiment 2 Summary: Scalability")
    print(f"{'=' * 70}")
    print(f"  {'%':>5} {'Nodes':>8} {'Rels':>10} {'Seed(s)':>8} {'Rule(s)':>8} {'Total(s)':>9}")
    print(f"  {'─' * 55}")
    for r in scale_results:
        print(f"  {r['percentage']:>4}% {r['total_nodes']:>8,} {r['total_relationships']:>10,} "
              f"{r['node_insert_time_s']:>8.2f} {r['rule_engine_time_s']:>8.2f} "
              f"{r['node_insert_time_s'] + r['rule_engine_time_s']:>9.2f}")

    return _save_result("exp2_scalability", result)


def _build_nodes_only(builder, scene_data):
    """Insert nodes and seed relationships WITHOUT rule engine."""
    import json as _json

    # Batch insert large collections
    buildings = scene_data.get("buildings", [])
    if buildings:
        builder.db.batch_create_nodes("Building", buildings, batch_size=500)

    parcels = scene_data.get("parcels", [])
    if parcels:
        builder.db.batch_create_nodes("Parcel", parcels, batch_size=500)

    for zone in scene_data.get("zones", []):
        builder.db.merge_node("Zone", "uid", zone["uid"], **zone)

    for road in scene_data.get("roads", []):
        props = {**road}
        if "coordinates" in props and isinstance(props["coordinates"], list):
            props["coordinates"] = _json.dumps(props["coordinates"])
        builder.db.merge_node("Road", "uid", road["uid"], **props)

    for pl in scene_data.get("parking_lots", []):
        builder.db.merge_node("ParkingLot", "uid", pl["uid"], **pl)

    for ps in scene_data.get("parking_spaces", []):
        builder.db.merge_node("ParkingSpace", "uid", ps["uid"], **ps)

    for v in scene_data.get("vehicles", []):
        builder.db.merge_node("Vehicle", "uid", v["uid"], **v)

    for s in scene_data.get("sensors", []):
        builder.db.merge_node("Sensor", "uid", s["uid"], **s)

    for c in scene_data.get("cameras", []):
        builder.db.merge_node("Camera", "uid", c["uid"], **c)

    for t in scene_data.get("trees", []):
        builder.db.merge_node("Tree", "uid", t["uid"], **t)

    for f in scene_data.get("facilities", []):
        builder.db.merge_node("Facility", "uid", f["uid"], **f)

    for iot in scene_data.get("iot_addresses", []):
        builder.db.merge_node("ThingsAddr", "uid", iot["uid"], **iot)

    intersections = scene_data.get("intersections", [])
    if intersections:
        builder.db.batch_create_nodes("RoadIntersection", intersections, batch_size=500)

    # State nodes
    for state in scene_data.get("states", []):
        label = state.pop("_label")
        builder.db.merge_node(label, "uid", state["uid"], **state)
        state["_label"] = label

    # Seed relationships
    relationships = scene_data.get("relationships", [])
    if relationships:
        builder._create_relationships_fast(relationships)


# ═══════════════════════════════════════════════════════════════════
# Experiment 3: Per-Strategy Performance Comparison
# ═══════════════════════════════════════════════════════════════════

def experiment_3_strategy_comparison():
    """Compare the 7 rule engine strategies individually.

    Measures per-strategy: execution time, relationships created,
    average time per relationship.

    Requires existing full KG (runs Experiment 1 first if DB is empty).
    """
    print("=" * 70)
    print("  Experiment 3: Per-Strategy Performance Comparison")
    print("=" * 70)

    from backend.db.neo4j_client import db

    # Check if DB has data
    stats = db.stats()
    if stats.get("node_count", 0) == 0:
        print("\n[Step 0] DB empty, running Experiment 1 first...")
        experiment_1_construction_timing()

    # Read the latest rule engine timing from the last full run
    # If we just ran Exp 1, the timing is in the last_reseed_timing.json
    timing_path = os.path.join(os.path.dirname(__file__), "last_reseed_timing.json")
    latest_results_dir = os.path.join(RESULTS_DIR)

    # Find most recent exp1 result
    exp1_files = sorted([
        f for f in os.listdir(latest_results_dir)
        if f.startswith("exp1_construction_")
    ]) if os.path.exists(latest_results_dir) else []

    if exp1_files:
        exp1_path = os.path.join(latest_results_dir, exp1_files[-1])
        with open(exp1_path, "r", encoding="utf-8") as f:
            exp1_data = json.load(f)

        rule_engine = exp1_data.get("rule_engine", {})
        strategies = rule_engine.get("strategies", {})
        rules = rule_engine.get("rules", [])

        result = {
            "experiment": "3_strategy_comparison",
            "timestamp": datetime.now().isoformat(),
            "source": exp1_path,
            "strategies": {},
            "rules": rules,
        }

        print(f"\n  {'Strategy':<30} {'Rules':>5} {'Rels':>10} {'Time(s)':>10} {'ms/rel':>10}")
        print(f"  {'─' * 70}")
        for strat_name, info in sorted(strategies.items(), key=lambda x: -x[1].get("elapsed", 0)):
            elapsed = info.get("elapsed", 0)
            rels = info.get("rels_created", 0)
            avg_ms = (elapsed / rels * 1000) if rels > 0 else 0
            rules_count = info.get("rule_count", 0)
            print(f"  {strat_name:<30} {rules_count:>5} {rels:>10,} {elapsed:>10.2f} {avg_ms:>10.4f}")

            result["strategies"][strat_name] = {
                "rule_count": rules_count,
                "rels_created": rels,
                "elapsed_s": round(elapsed, 3),
                "avg_ms_per_rel": round(avg_ms, 4),
            }

        total_rels = sum(s.get("rels_created", 0) for s in strategies.values())
        total_time = rule_engine.get("total_elapsed", 0)
        print(f"  {'─' * 70}")
        print(f"  {'TOTAL':<30} {len(rules):>5} {total_rels:>10,} {total_time:>10.2f}")

        result["total_rels"] = total_rels
        result["total_time_s"] = total_time

        return _save_result("exp3_strategy", result)
    else:
        print("\n[ERROR] No Experiment 1 results found. Run experiment 1 first.")
        return None


# ═══════════════════════════════════════════════════════════════════
# Experiment 5: Road Network Validation
# ═══════════════════════════════════════════════════════════════════

def experiment_5_road_network_validation():
    """Validate road network topology construction.

    Measures:
      - MEETS_AT matching accuracy (roads matched to intersections)
      - Building-road coverage (ON_STREET + FRONTS_ROAD + ALONG_ROAD)
      - Road network connectivity stats
    """
    print("=" * 70)
    print("  Experiment 5: Road Network Validation")
    print("=" * 70)

    from backend.db.neo4j_client import db

    stats = db.stats()
    if stats.get("node_count", 0) == 0:
        print("\n[ERROR] DB is empty. Run experiment 1 first.")
        return None

    result = {
        "experiment": "5_road_network_validation",
        "timestamp": datetime.now().isoformat(),
        "road_stats": {},
        "intersection_stats": {},
        "building_road_coverage": {},
        "connectivity": {},
    }

    # Road counts
    roads = db.query("MATCH (r:Road) RETURN count(r) AS cnt")
    road_count = roads[0]["cnt"] if roads else 0
    result["road_stats"]["total_roads"] = road_count

    # Intersection counts
    ints = db.query("MATCH (i:RoadIntersection) RETURN count(i) AS cnt")
    int_count = ints[0]["cnt"] if ints else 0
    result["intersection_stats"]["total_intersections"] = int_count

    # MEETS_AT relationships
    meets = db.query("MATCH ()-[r:MEETS_AT]->() RETURN count(r) AS cnt")
    meets_count = meets[0]["cnt"] if meets else 0
    result["intersection_stats"]["meets_at_count"] = meets_count

    # Roads with at least one MEETS_AT
    roads_connected = db.query(
        "MATCH (r:Road)-[:MEETS_AT]->() RETURN count(DISTINCT r) AS cnt"
    )
    roads_connected_count = roads_connected[0]["cnt"] if roads_connected else 0
    result["road_stats"]["roads_with_intersection"] = roads_connected_count
    result["road_stats"]["road_intersection_coverage_pct"] = (
        round(roads_connected_count / road_count * 100, 2) if road_count > 0 else 0
    )

    # Intersections with at least one MEETS_AT
    ints_connected = db.query(
        "MATCH ()-[:MEETS_AT]->(i:RoadIntersection) RETURN count(DISTINCT i) AS cnt"
    )
    ints_connected_count = ints_connected[0]["cnt"] if ints_connected else 0
    result["intersection_stats"]["intersections_connected"] = ints_connected_count
    result["intersection_stats"]["intersection_coverage_pct"] = (
        round(ints_connected_count / int_count * 100, 2) if int_count > 0 else 0
    )

    # Average MEETS_AT per intersection
    avg_meets = db.query(
        "MATCH ()-[:MEETS_AT]->(i:RoadIntersection) "
        "WITH i, count(*) AS deg "
        "RETURN avg(deg) AS avg_deg, min(deg) AS min_deg, max(deg) AS max_deg"
    )
    if avg_meets:
        result["intersection_stats"]["avg_degree"] = round(avg_meets[0]["avg_deg"], 2)
        result["intersection_stats"]["min_degree"] = avg_meets[0]["min_deg"]
        result["intersection_stats"]["max_degree"] = avg_meets[0]["max_deg"]

    # Building-road coverage
    total_bldg = db.query("MATCH (b:Building) RETURN count(b) AS cnt")
    total_bldg_count = total_bldg[0]["cnt"] if total_bldg else 0

    on_street = db.query("MATCH (b:Building)-[:ON_STREET]->() RETURN count(DISTINCT b) AS cnt")
    on_street_count = on_street[0]["cnt"] if on_street else 0

    fronts_road = db.query("MATCH (b:Building)-[:FRONTS_ROAD]->() RETURN count(DISTINCT b) AS cnt")
    fronts_road_count = fronts_road[0]["cnt"] if fronts_road else 0

    any_road = db.query(
        "MATCH (b:Building) WHERE (b)-[:ON_STREET]->() OR (b)-[:FRONTS_ROAD]->() "
        "RETURN count(b) AS cnt"
    )
    any_road_count = any_road[0]["cnt"] if any_road else 0

    result["building_road_coverage"] = {
        "total_buildings": total_bldg_count,
        "on_street_count": on_street_count,
        "fronts_road_count": fronts_road_count,
        "any_road_connection": any_road_count,
        "coverage_pct": round(any_road_count / total_bldg_count * 100, 2) if total_bldg_count > 0 else 0,
    }

    # Relationship type counts
    for rel_type in ["ON_STREET", "FRONTS_ROAD", "ALONG_ROAD", "MEETS_AT"]:
        r = db.query(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS cnt")
        result["connectivity"][rel_type] = r[0]["cnt"] if r else 0

    # Print summary
    print(f"\n  Road Network Statistics:")
    print(f"    Roads: {road_count}")
    print(f"    Intersections: {int_count}")
    print(f"    MEETS_AT links: {meets_count}")
    print(f"    Roads with intersection: {roads_connected_count}/{road_count} "
          f"({result['road_stats']['road_intersection_coverage_pct']}%)")
    print(f"    Intersections connected: {ints_connected_count}/{int_count} "
          f"({result['intersection_stats']['intersection_coverage_pct']}%)")
    print(f"\n  Building-Road Coverage:")
    print(f"    Total buildings: {total_bldg_count}")
    print(f"    ON_STREET: {on_street_count}")
    print(f"    FRONTS_ROAD: {fronts_road_count}")
    print(f"    Any road connection: {any_road_count}/{total_bldg_count} "
          f"({result['building_road_coverage']['coverage_pct']}%)")
    print(f"\n  Relationship Counts:")
    for rt, cnt in result["connectivity"].items():
        print(f"    {rt}: {cnt:,}")

    return _save_result("exp5_road_network", result)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="k-GeoKG Benchmark Experiments")
    parser.add_argument(
        "--experiment", "-e",
        choices=["1", "2", "3", "5", "all"],
        default="all",
        help="Which experiment to run (default: all)"
    )
    args = parser.parse_args()

    t_start = time.time()

    if args.experiment in ("1", "all"):
        experiment_1_construction_timing()

    if args.experiment in ("2",):
        experiment_2_scalability()

    if args.experiment in ("3", "all"):
        experiment_3_strategy_comparison()

    if args.experiment in ("5", "all"):
        experiment_5_road_network_validation()

    if args.experiment == "all":
        # Scalability test is expensive (~5x full construction), run last
        print("\n[NOTE] Scalability test (Exp 2) is expensive. Run separately with: -e 2")

    total = time.time() - t_start
    print(f"\n[Benchmark] All experiments completed in {total:.1f}s")


if __name__ == "__main__":
    main()
