"""
GeoKG Builder - Constructs the knowledge graph from ontology schema and data.

Implements the paper's knowledge graph construction process:
1. Entity recognition and attribute extraction
2. Relationship analysis (spatial, semantic, topological)
3. Knowledge fusion into Neo4j graph
"""

import json
import time
from py2neo import Node
from backend.db.neo4j_client import db
from backend.geokg.ontology import SCHEMA_CONSTRAINTS, SCHEMA_INDEXES


class GeoKGBuilder:
    def __init__(self):
        self.db = db

    def initialize(self):
        """Set up database schema (constraints + indexes)."""
        self.db.init_schema(SCHEMA_CONSTRAINTS + SCHEMA_INDEXES)

    def clear(self):
        """Clear all data in the graph."""
        self.db.clear_all()

    def build_from_data(self, scene_data: dict):
        """
        Build the full GeoKG from structured scene data.

        scene_data should have keys: zones, buildings, roads, parking_lots,
        parking_spaces, vehicles, sensors, cameras, trees, facilities,
        states, relationships

        Returns:
            dict: Timing report with data preparation and rule engine timings.
        """
        builder_timing = {}
        t_build_start = time.time()

        # --- Create Entity Domain nodes ---
        # Buildings use batch insertion for performance (can be 58K+)
        t0 = time.time()
        buildings = scene_data.get("buildings", [])
        if buildings:
            print(f"  [Builder] Batch inserting {len(buildings)} buildings...")
            self.db.batch_create_nodes("Building", buildings, batch_size=500)
            print(f"  [Builder] Buildings done.")
        builder_timing["node_building_insert"] = time.time() - t0

        # Parcels: batch insertion for performance (can be 71K+)
        t0 = time.time()
        parcels = scene_data.get("parcels", [])
        if parcels:
            print(f"  [Builder] Batch inserting {len(parcels)} parcels...")
            self.db.batch_create_nodes("Parcel", parcels, batch_size=500)
            print(f"  [Builder] Parcels done.")
        builder_timing["node_parcel_insert"] = time.time() - t0

        # Other entity types: small counts, use merge_node
        t0 = time.time()
        for zone in scene_data.get("zones", []):
            self.db.merge_node("Zone", "uid", zone["uid"], **zone)

        for road in scene_data.get("roads", []):
            props = {**road}
            if "coordinates" in props and isinstance(props["coordinates"], list):
                props["coordinates"] = json.dumps(props["coordinates"])
            self.db.merge_node("Road", "uid", road["uid"], **props)

        for pl in scene_data.get("parking_lots", []):
            self.db.merge_node("ParkingLot", "uid", pl["uid"], **pl)

        for ps in scene_data.get("parking_spaces", []):
            self.db.merge_node("ParkingSpace", "uid", ps["uid"], **ps)

        for v in scene_data.get("vehicles", []):
            self.db.merge_node("Vehicle", "uid", v["uid"], **v)

        for s in scene_data.get("sensors", []):
            self.db.merge_node("Sensor", "uid", s["uid"], **s)

        for c in scene_data.get("cameras", []):
            self.db.merge_node("Camera", "uid", c["uid"], **c)

        for t in scene_data.get("trees", []):
            self.db.merge_node("Tree", "uid", t["uid"], **t)

        for f in scene_data.get("facilities", []):
            self.db.merge_node("Facility", "uid", f["uid"], **f)

        for iot in scene_data.get("iot_addresses", []):
            self.db.merge_node("ThingsAddr", "uid", iot["uid"], **iot)
        builder_timing["node_other_insert"] = time.time() - t0

        # Road Intersections (교차로)
        t0 = time.time()
        intersections = scene_data.get("intersections", [])
        if intersections:
            print(f"  [Builder] Batch inserting {len(intersections)} intersections...")
            self.db.batch_create_nodes("RoadIntersection", intersections, batch_size=500)
            print(f"  [Builder] Intersections done.")
        builder_timing["node_intersection_insert"] = time.time() - t0

        # Auto Road Links (TN_RODWAY_LINK national base-map road network, v2)
        t0 = time.time()
        auto_road_links = scene_data.get("auto_road_links", [])
        if auto_road_links:
            print(f"  [Builder] Batch inserting {len(auto_road_links)} auto road links...")
            self.db.batch_create_nodes("AutoRoadLink", auto_road_links, batch_size=500)
            print(f"  [Builder] AutoRoadLinks done.")
        builder_timing["node_autoroadlink_insert"] = time.time() - t0

        # --- Create State Domain nodes ---
        t0 = time.time()
        for state in scene_data.get("states", []):
            label = state.pop("_label")
            self.db.merge_node(label, "uid", state["uid"], **state)
            state["_label"] = label  # restore
        builder_timing["node_state_insert"] = time.time() - t0

        # --- Create Relationships (optimized with label hints) ---
        t0 = time.time()
        relationships = scene_data.get("relationships", [])
        if relationships:
            print(f"  [Builder] Batch inserting {len(relationships)} relationships...")
            self._create_relationships_fast(relationships)
            print(f"  [Builder] Relationships done.")
        builder_timing["seed_relationship_insert"] = time.time() - t0

        # --- Enrich relationships from attributes (address, type, proximity) ---
        t0 = time.time()
        from backend.data.relationship_enrichment import enrich_relationships
        rule_engine_timing = enrich_relationships(self.db)
        builder_timing["rule_engine_total"] = time.time() - t0

        builder_timing["build_total"] = time.time() - t_build_start

        # Print builder timing summary
        print(f"\n[Builder] ═══ Timing Report (Neo4j Build) ═══")
        for key, elapsed in sorted(builder_timing.items()):
            print(f"  {key}: {elapsed:.2f}s")
        print()

        return {
            "data_preparation": scene_data.get("_timing", {}),
            "neo4j_build": builder_timing,
            "rule_engine": rule_engine_timing,
        }

    # ── uid prefix → label mapping for fast relationship creation ──
    _UID_LABEL_MAP = {
        "zone-": "Zone",
        "bldg-": "Building",
        "road-": "Road",
        "veh-": "Vehicle",
        "sensor-": "Sensor",
        "cam-": "Camera",
        "tree-": "Tree",
        "fac-": "Facility",
        "park-": "ParkingLot",
        "ps-": "ParkingSpace",     # parking space uid: park-X-sNN
        "parcel-": "Parcel",
        "iot-": "ThingsAddr",
        "intersection-": "RoadIntersection",
        "ts-": "TrafficState",
        "es-": "EnvironmentState",
        "os-": "OccupancyState",
        "ws-": "WeatherState",
        "eq-": "EquipmentState",
        "pc-": "PositionChange",
        "sc-": "StatusChange",
        "ec-": "EnvironmentChange",
        "me-": "MaintenanceEvent",
        "ped-": "Pedestrian",
    }

    def _uid_to_label(self, uid: str) -> str | None:
        """Infer Neo4j label from uid prefix."""
        for prefix, label in self._UID_LABEL_MAP.items():
            if uid.startswith(prefix):
                return label
        return None

    def _create_relationships_fast(self, relationships: list[dict]):
        """Create relationships using label-aware Cypher for index utilization.

        Groups by (from_label, to_label, rel_type) and uses UNWIND with labeled MATCH
        to leverage unique uid constraints/indexes. Falls back to label-less MATCH
        for unrecognized uid prefixes.
        """
        from collections import defaultdict

        # Group by (from_label, to_label, rel_type)
        grouped = defaultdict(list)
        fallback = defaultdict(list)

        for r in relationships:
            fl = r.get("fl") or self._uid_to_label(r["from"])
            tl = r.get("tl") or self._uid_to_label(r["to"])
            if fl and tl:
                key = (fl, tl, r["type"])
                grouped[key].append({"from_uid": r["from"], "to_uid": r["to"]})
            else:
                fallback[r["type"]].append({"from_uid": r["from"], "to_uid": r["to"]})

        batch_size = 1000
        total = 0

        # Fast path: labeled MATCH (uses uid indexes)
        for (fl, tl, rel_type), items in grouped.items():
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                self.db.graph.run(
                    f"UNWIND $batch AS rel "
                    f"MATCH (a:{fl} {{uid: rel.from_uid}}) "
                    f"MATCH (b:{tl} {{uid: rel.to_uid}}) "
                    f"CREATE (a)-[:{rel_type}]->(b)",
                    batch=batch,
                )
                total += len(batch)
            if len(items) > 100:
                print(f"    [{fl}]-[:{rel_type}]->[{tl}]: {len(items)}")

        # Slow fallback: no label (for unknown uid prefixes)
        for rel_type, items in fallback.items():
            for i in range(0, len(items), 500):
                batch = items[i:i + 500]
                self.db.graph.run(
                    f"UNWIND $batch AS rel "
                    f"MATCH (a {{uid: rel.from_uid}}) "
                    f"MATCH (b {{uid: rel.to_uid}}) "
                    f"CREATE (a)-[:{rel_type}]->(b)",
                    batch=batch,
                )
                total += len(batch)
            if items:
                print(f"    [?]-[:{rel_type}]->[?]: {len(items)} (fallback)")

        print(f"  [Builder] Total {total} relationships created.")

    def add_entity(self, label: str, properties: dict) -> Node:
        return self.db.merge_node(label, "uid", properties["uid"], **properties)

    def add_relationship(self, from_uid: str, from_label: str, rel_type: str, to_uid: str, to_label: str, **props):
        src = self.db.get_node(from_label, uid=from_uid)
        tgt = self.db.get_node(to_label, uid=to_uid)
        if src and tgt:
            return self.db.create_relationship(src, rel_type, tgt, **props)
        return None

    def get_graph_summary(self) -> dict:
        return self.db.stats()
