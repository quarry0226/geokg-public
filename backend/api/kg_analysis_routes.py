"""KG Analysis API - demonstrates what GeoKG can DO.

Provides graph-powered analytics:
1. Shortest path between entities
2. Impact analysis (what's affected if X changes, with typed filtering)
3. Spatial neighborhood queries
4. Anomaly-related entity lookup
5. Custom Cypher query execution
6. Coverage dead zone analysis (shelter/transit/park gaps)
7. Road closure impact simulation
8. Safety profile (comprehensive score per entity)
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.db.neo4j_client import db

router = APIRouter(prefix="/api/kg", tags=["KG Analysis"])


def _resolve_uid(identifier: str) -> str:
    """Resolve an identifier (uid, gsid, or nf_id/고유번호) to uid for Cypher matching."""
    ident = identifier.strip()

    # 1) GSID format: "XX-8hex-G0-00-..." (has many dashes)
    if len(ident) >= 20 and ident.count('-') >= 5:
        rows = db.query(
            "MATCH (n {gsid: $gsid}) RETURN n.uid AS uid LIMIT 1",
            gsid=ident.upper(),
        )
        if rows:
            return rows[0]["uid"]

    # 2) 고유번호 format: "BLD..." (building unique ID from MOLIT)
    if ident.upper().startswith("BLD"):
        rows = db.query(
            "MATCH (n {nf_id: $nf}) RETURN n.uid AS uid LIMIT 1",
            nf=ident,
        )
        if rows:
            return rows[0]["uid"]

    # 3) Direct uid match
    return ident


# Spatial relationship types used for path finding.
# Excludes logical/hierarchical types like CONTAINS, HAS_STATE, MONITORS, SERVES.
SPATIAL_REL_TYPES = [
    "ADJACENT_TO", "NEAR", "ON_ROAD", "ALONG", "CONNECTED_TO",
    "ON_STREET", "FRONTS_ROAD", "SAME_DONG", "SAME_USAGE",
    "ON_PARCEL", "NEAR_BUILDING",
    "NEAREST_SHELTER", "ACCESSIBLE_BY_TRANSIT", "NEAR_PARK",
    "NEAR_FACILITY", "ALONG_ROAD", "COLOCATED",
    "MEETS_AT",
]
_SPATIAL_REL_FILTER = "|".join(SPATIAL_REL_TYPES)

# Road-network-only: physical node-link topology (Road ↔ RoadIntersection)
ROAD_LINK_RELS = ["MEETS_AT"]
_ROAD_LINK_FILTER = "|".join(ROAD_LINK_RELS)

# Relations that connect non-road entities to their nearest road
ENTITY_TO_ROAD_RELS = ["ON_STREET", "FRONTS_ROAD", "ALONG_ROAD", "ALONG"]


def _get_entity_info(uid: str) -> dict:
    """Get basic entity info for display."""
    rows = db.query(
        "MATCH (n {uid: $uid}) "
        "RETURN n.uid AS uid, n.gsid AS gsid, labels(n)[0] AS label, n.name AS name",
        uid=uid,
    )
    return rows[0] if rows else {"uid": uid, "gsid": None, "label": None, "name": None}


def _find_nearest_road(entity_uid: str) -> dict:
    """Resolve entity to the nearest Road node on the physical road network.
    Returns {"road_uid": ..., "is_self": True/False}.
    - is_self=True means the entity itself is already a Road.
    """
    # 1) Entity is already a Road
    rows = db.query(
        "MATCH (n {uid: $uid}) WHERE n:Road RETURN n.uid AS uid", uid=entity_uid
    )
    if rows:
        return {"road_uid": entity_uid, "is_self": True}

    # 2) Entity is a RoadIntersection → pick any attached Road
    rows = db.query(
        "MATCH (n {uid: $uid})-[:MEETS_AT]-(r:Road) "
        "WHERE n:RoadIntersection RETURN r.uid AS uid LIMIT 1",
        uid=entity_uid,
    )
    if rows:
        return {"road_uid": rows[0]["uid"], "is_self": False}

    # 3) Entity is Building / Facility / etc → find road via relationship
    rows = db.query(
        "MATCH (n {uid: $uid})-[:ON_STREET|FRONTS_ROAD|ALONG_ROAD|ALONG]-(r:Road) "
        "RETURN r.uid AS uid LIMIT 1",
        uid=entity_uid,
    )
    if rows:
        return {"road_uid": rows[0]["uid"], "is_self": False}

    return {"road_uid": None, "is_self": False}


def _road_network_path(from_uid: str, to_uid: str):
    """Physical road node-link network path.
    1. Resolve start/end entities to nearest Road.
    2. Find shortest path through Road ↔ RoadIntersection (MEETS_AT only).
    3. Return structured result so frontend can draw dashed connectors + solid road route.
    """
    from_info = _get_entity_info(from_uid)
    to_info = _get_entity_info(to_uid)

    from_road = _find_nearest_road(from_uid)
    to_road = _find_nearest_road(to_uid)

    if not from_road["road_uid"]:
        raise HTTPException(404, f"출발지({from_uid})에 연결된 도로가 없습니다")
    if not to_road["road_uid"]:
        raise HTTPException(404, f"도착지({to_uid})에 연결된 도로가 없습니다")

    fr_uid = from_road["road_uid"]
    tr_uid = to_road["road_uid"]

    # Same road → direct
    if fr_uid == tr_uid:
        road_info = _get_entity_info(fr_uid)
        return {
            "mode": "road_network",
            "from_entity": from_info,
            "to_entity": to_info,
            "from_road": None if from_road["is_self"] else fr_uid,
            "to_road": None if to_road["is_self"] else tr_uid,
            "road_path": [road_info],
            "relationships": [],
            "hops": 0,
        }

    # Shortest path through physical road network (MEETS_AT only)
    rows = db.query(
        f"""
        MATCH path = shortestPath(
            (a {{uid: $fr}})-[:{_ROAD_LINK_FILTER} *..40]-(b {{uid: $tr}})
        )
        RETURN [n IN nodes(path) | {{uid: n.uid, gsid: n.gsid, label: labels(n)[0], name: n.name}}] AS nodes,
               [r IN relationships(path) | {{type: type(r), from: startNode(r).uid, to: endNode(r).uid}}] AS rels,
               length(path) AS hops
        """,
        fr=fr_uid, tr=tr_uid,
    )
    if not rows:
        raise HTTPException(
            404,
            f"도로 네트워크 경로를 찾을 수 없습니다: {from_info.get('name',from_uid)} → {to_info.get('name',to_uid)} "
            f"(try mode=spatial)",
        )

    r = rows[0]
    return {
        "mode": "road_network",
        "from_entity": from_info,
        "to_entity": to_info,
        "from_road": None if from_road["is_self"] else fr_uid,
        "to_road": None if to_road["is_self"] else tr_uid,
        "road_path": r["nodes"],
        "relationships": r["rels"],
        "hops": r["hops"],
    }


# ── 1. Shortest Path ────────────────────────────────────────────
@router.get("/path")
def find_shortest_path(from_uid: str, to_uid: str, mode: str = "spatial"):
    """Find shortest path between two entities.
    mode=road_network → physical road node-link network (MEETS_AT only)
    mode=spatial      → spatial relations (ADJACENT_TO, NEAR, ON_ROAD, etc.)
    mode=all          → all relation types
    """
    resolved_from = _resolve_uid(from_uid)
    resolved_to = _resolve_uid(to_uid)

    # ── Road network mode: physical topology ──
    if mode == "road_network":
        return _road_network_path(resolved_from, resolved_to)

    # ── KG-based modes ──
    if mode == "spatial":
        rel_clause = f"[r:{_SPATIAL_REL_FILTER} *..15]"
    else:
        rel_clause = "[*..15]"

    rows = db.query(
        f"""
        MATCH path = shortestPath(
            (a {{uid: $from_uid}})-{rel_clause}-(b {{uid: $to_uid}})
        )
        RETURN [n IN nodes(path) | {{uid: n.uid, gsid: n.gsid, label: labels(n)[0], name: n.name}}] AS nodes,
               [r IN relationships(path) | {{type: type(r), from: startNode(r).uid, to: endNode(r).uid}}] AS rels,
               length(path) AS hops
        """,
        from_uid=resolved_from, to_uid=resolved_to,
    )
    if not rows:
        hint = " (try mode=all to include all relation types)" if mode == "spatial" else ""
        raise HTTPException(404, f"No path found between {from_uid} and {to_uid}{hint}")
    r = rows[0]
    return {
        "nodes": r["nodes"],
        "relationships": r["rels"],
        "hops": r["hops"],
        "mode": mode,
    }


# ── 2. Impact Analysis (with typed filtering) ────────────────────
IMPACT_TYPE_RELS = {
    "infrastructure": "ON_STREET|FRONTS_ROAD|ALONG_ROAD|CONNECTED_TO|ALONG|ON_ROAD",
    "safety": "NEAREST_SHELTER|ACCESSIBLE_BY_TRANSIT|NEAR_PARK|MONITORS|NEAR_BUILDING",
    "administrative": "SAME_DONG|ON_PARCEL|COLOCATED|SAME_USAGE|CONTAINS",
}


@router.get("/impact")
def impact_analysis(uid: str, depth: int = 2, impact_type: str = "all"):
    """
    Find all entities affected by a change to the given entity.
    Traverses up to `depth` hops in all directions.
    impact_type: all | infrastructure | safety | administrative
    """
    resolved = _resolve_uid(uid)
    d = min(depth, 5)

    rel_filter = IMPACT_TYPE_RELS.get(impact_type)
    if rel_filter:
        rel_clause = f"[:{rel_filter} *1..{d}]"
    else:
        rel_clause = f"[*1..{d}]"

    rows = db.query(
        f"""
        MATCH path = (source {{uid: $uid}})-{rel_clause}-(affected)
        WHERE affected.uid <> $uid AND affected.uid IS NOT NULL
        WITH affected, labels(affected)[0] AS label,
             min(length(path)) AS distance,
             [r IN relationships(path) | type(r)] AS rel_chain
        RETURN affected.uid AS uid, affected.gsid AS gsid, affected.name AS name, label,
               distance, collect(DISTINCT rel_chain[0]) AS via_relations
        ORDER BY distance, label
        """,
        uid=resolved,
    )
    # Group by distance for visualization
    layers = {}
    by_label = {}
    for r in rows:
        dist = r["distance"]
        if dist not in layers:
            layers[dist] = []
        layers[dist].append({
            "uid": r["uid"],
            "gsid": r["gsid"],
            "name": r["name"],
            "label": r["label"],
            "via": r["via_relations"],
        })
        lbl = r["label"] or "Unknown"
        by_label[lbl] = by_label.get(lbl, 0) + 1

    return {
        "source": uid,
        "depth": depth,
        "impact_type": impact_type,
        "total_affected": len(rows),
        "by_label": by_label,
        "layers": layers,
    }


# ── 3. Spatial Neighbors ─────────────────────────────────────────
@router.get("/nearby")
def find_nearby(uid: str, radius_m: float = 200):
    """Find entities within radius_m meters of the given entity (Euclidean approx)."""
    resolved = _resolve_uid(uid)
    delta = radius_m / 111000  # approximate degree offset
    rows = db.query(
        """
        MATCH (origin {uid: $uid})
        WHERE origin.longitude IS NOT NULL
        WITH origin
        MATCH (n)
        WHERE n.uid <> origin.uid
          AND n.longitude IS NOT NULL
          AND abs(n.longitude - origin.longitude) < $delta
          AND abs(n.latitude - origin.latitude) < $delta
        RETURN n.uid AS uid, n.gsid AS gsid, n.name AS name, labels(n)[0] AS label,
               n.longitude AS lon, n.latitude AS lat,
               sqrt((n.longitude - origin.longitude)^2 + (n.latitude - origin.latitude)^2) * 111000 AS dist_m
        ORDER BY dist_m
        LIMIT 30
        """,
        uid=resolved, delta=delta,
    )
    return {"origin": uid, "radius_m": radius_m, "results": rows}


# ── 4. Anomaly Scenario: What's connected to anomalous sensors? ──
@router.get("/anomaly")
def anomaly_trace(
    sensor_uid: str = None,
    threshold_type: str = "high",
    temp: float = None,
    noise: float = None,
    aqi: float = None,
    humidity: float = None,
):
    """
    Given a sensor (or all sensors above threshold), trace what buildings/zones
    are affected via MONITORS relationships.
    Custom thresholds: ?temp=25&noise=60&aqi=80&humidity=75
    """
    if sensor_uid:
        where_clause = "WHERE s.uid = $sensor_uid"
    elif any(v is not None for v in [temp, noise, aqi, humidity]):
        # Custom thresholds from user sliders
        conditions = []
        if temp is not None:
            conditions.append(f"(s.sensor_type = 'temperature' AND s.value > {float(temp)})")
        if noise is not None:
            conditions.append(f"(s.sensor_type = 'noise' AND s.value > {float(noise)})")
        if aqi is not None:
            conditions.append(f"(s.sensor_type = 'aqi' AND s.value > {float(aqi)})")
        if humidity is not None:
            conditions.append(f"(s.sensor_type = 'humidity' AND s.value > {float(humidity)})")
        where_clause = "WHERE " + " OR ".join(conditions)
    else:
        # Preset thresholds
        if threshold_type == "high":
            where_clause = """
            WHERE (s.sensor_type = 'temperature' AND s.value > 30)
               OR (s.sensor_type = 'aqi' AND s.value > 100)
               OR (s.sensor_type = 'noise' AND s.value > 70)
               OR (s.sensor_type = 'humidity' AND s.value > 80)
            """
        else:
            where_clause = """
            WHERE (s.sensor_type = 'temperature' AND s.value < 5)
               OR (s.sensor_type = 'aqi' AND s.value > 100)
            """

    rows = db.query(
        f"""
        MATCH (s:Sensor)
        {where_clause}
        OPTIONAL MATCH (s)-[:MONITORS]->(zone)
        OPTIONAL MATCH (zone)<-[:CONTAINS]-(parent)
        OPTIONAL MATCH (zone)-[:CONTAINS]->(child)
        RETURN s.uid AS sensor_uid, s.gsid AS sensor_gsid, s.sensor_type AS sensor_type,
               s.value AS sensor_value, s.unit AS unit,
               collect(DISTINCT {{uid: zone.uid, gsid: zone.gsid, label: labels(zone)[0], name: zone.name}}) AS monitored_zones,
               collect(DISTINCT {{uid: child.uid, gsid: child.gsid, label: labels(child)[0], name: child.name}}) AS affected_entities
        """,
        sensor_uid=sensor_uid,
    )
    return {
        "thresholds": {"temp": temp, "noise": noise, "aqi": aqi, "humidity": humidity},
        "anomalies": rows,
    }


# ── 5. Entity Statistics / Profile ───────────────────────────────
@router.get("/profile/{uid}")
def entity_profile(uid: str):
    """Get a full profile of an entity: properties, neighbors, states, statistics."""
    resolved = _resolve_uid(uid)
    # Basic info
    node_rows = db.query(
        "MATCH (n {uid: $resolved}) RETURN labels(n)[0] AS label, properties(n) AS props",
        resolved=resolved,
    )
    if not node_rows:
        raise HTTPException(404, "Entity not found")

    node = node_rows[0]

    # Relationships summary
    rels = db.query(
        """
        MATCH (n {uid: $uid})-[r]-(m)
        RETURN type(r) AS rel_type,
               CASE WHEN startNode(r).uid = $uid THEN 'outgoing' ELSE 'incoming' END AS direction,
               labels(m)[0] AS neighbor_label,
               m.uid AS neighbor_uid,
               m.name AS neighbor_name
        """,
        uid=resolved,
    )

    # States
    states = db.query(
        """
        MATCH (n {uid: $uid})-[:HAS_STATE]->(s)
        RETURN labels(s)[0] AS state_type, properties(s) AS state_props
        """,
        uid=resolved,
    )

    return {
        "uid": resolved,
        "label": node["label"],
        "properties": node["props"],
        "relationships": rels,
        "relationship_count": len(rels),
        "states": [{"type": s["state_type"], **s["state_props"]} for s in states],
    }


# ── 6. Custom Cypher Query (read-only) ───────────────────────────
class CypherQuery(BaseModel):
    query: str


@router.post("/cypher")
def run_cypher(data: CypherQuery):
    """Run a read-only Cypher query. Only MATCH queries allowed."""
    q = data.query.strip()
    # Safety: only allow read queries
    first_word = q.split()[0].upper() if q else ""
    if first_word not in ("MATCH", "RETURN", "CALL", "WITH"):
        raise HTTPException(400, "Only read queries (MATCH) are allowed")
    if any(kw in q.upper() for kw in ["DELETE", "REMOVE", "SET ", "CREATE", "MERGE", "DROP", "DETACH"]):
        raise HTTPException(400, "Write operations are not allowed")

    try:
        rows = db.query(q)
        return {"results": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(400, f"Query error: {str(e)}")


# ── 7. Nearest Building by Coordinates ────────────────────────────
@router.get("/nearest_building")
def find_nearest_building(lon: float, lat: float, radius_m: float = 50):
    """Find the nearest building to given WGS84 coordinates."""
    delta = radius_m / 111000
    rows = db.query(
        """
        MATCH (b:Building)
        WHERE abs(b.longitude - $lon) < $delta AND abs(b.latitude - $lat) < $delta
        RETURN b.uid AS uid, b.name AS name, b.nf_id AS nf_id,
               labels(b)[0] AS label, properties(b) AS props,
               sqrt((b.longitude - $lon)^2 + (b.latitude - $lat)^2) * 111000 AS dist_m
        ORDER BY dist_m LIMIT 1
        """,
        lon=lon, lat=lat, delta=delta,
    )
    if not rows:
        return {"found": False}
    r = rows[0]
    return {"found": True, "uid": r["uid"], "name": r["name"], "nf_id": r["nf_id"],
            "label": r["label"], "properties": r["props"], "dist_m": round(r["dist_m"], 1)}


# ── 8. Building Colors by Type (for map overlay) ─────
@router.get("/building_colors")
def building_colors():
    """Return building positions with type-based colors and attributes for map overlay.
    Includes depth, floors, usage info for proper rendering and info popups."""
    from backend.fusion.attribute_level import BUILDING_TYPE_COLORS

    rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL
        RETURN b.uid AS uid, b.longitude AS lon, b.latitude AS lat,
               b.height AS h, b.width AS w, b.depth AS d,
               b.heading AS hdg,
               b.building_type AS btype, b.name AS name,
               b.floors AS fl, b.underground_floors AS ufl,
               b.usage_name AS uname, b.structure_type AS stype,
               b.building_area AS barea, b.gross_floor_area AS gfa,
               b.road_address AS raddr, b.approval_date AS adate,
               b.nf_id AS nfid, b.gsid AS gsid, b.boundary AS boundary,
               b.pnu AS pnu, b.zipcode AS zip, b.admin_dong_name AS adname
        """
    )

    buildings = []
    for r in rows:
        btype = r["btype"] or "commercial"
        color = BUILDING_TYPE_COLORS.get(btype, "#808080")
        buildings.append({
            "uid": r["uid"],
            "lon": r["lon"],
            "lat": r["lat"],
            "h": r["h"] or 3,
            "w": r["w"] or 5,
            "d": r["d"] or r["w"] or 5,  # depth, fallback to width
            "hd": r["hdg"] or 0,  # heading (degrees CW from north)
            "c": color,
            "t": btype,
            "n": r["name"] or "",
            "fl": r["fl"] or 0,
            "ufl": r["ufl"] or 0,
            "un": r["uname"] or "",
            "st": r["stype"] or "",
            "ba": r["barea"] or 0,
            "gfa": r["gfa"] or 0,
            "ra": r["raddr"] or "",
            "ad": r["adate"] or "",
            "nf": r["nfid"] or "",
            "gsid": r["gsid"] or "",
            "bnd": r["boundary"] or "",
            "pnu": r["pnu"] or "",
            "zip": r["zip"] or "",
            "adn": r["adname"] or "",
        })

    return {"count": len(buildings), "buildings": buildings}


# ── 9. Graph Summary / Analytics ─────────────────────────────────
@router.get("/analytics")
def graph_analytics():
    """Compute graph-level analytics: degree distribution, centrality hints, clusters."""
    # Degree distribution
    degree = db.query(
        """
        MATCH (n)
        WHERE n.uid IS NOT NULL
        OPTIONAL MATCH (n)-[r]-()
        WITH n, labels(n)[0] AS label, count(r) AS degree
        RETURN label, avg(degree) AS avg_degree, max(degree) AS max_degree,
               count(n) AS node_count
        ORDER BY avg_degree DESC
        """
    )

    # Top connected nodes
    hubs = db.query(
        """
        MATCH (n)-[r]-()
        WHERE n.uid IS NOT NULL
        WITH n, labels(n)[0] AS label, count(r) AS degree
        ORDER BY degree DESC
        LIMIT 10
        RETURN n.uid AS uid, n.gsid AS gsid, n.name AS name, label, degree
        """
    )

    # Relationship type distribution
    rel_dist = db.query(
        """
        MATCH ()-[r]->()
        RETURN type(r) AS rel_type, count(r) AS count
        ORDER BY count DESC
        """
    )

    return {
        "degree_by_label": degree,
        "top_hubs": hubs,
        "relationship_distribution": rel_dist,
    }


# ── 10. Parcel Boundaries (for map overlay) ──────────────────────
@router.get("/parcel_boundaries")
def parcel_boundaries(
    min_lon: float = None, max_lon: float = None,
    min_lat: float = None, max_lat: float = None,
    land_category: str = None,
    limit: int = 5000,
):
    """Return parcel boundaries with attributes for map overlay.
    Optionally filter by bounding box and/or land category.

    Returns parcels with either an explicit polygon ``boundary`` (Yuseong's
    cadastral SHP) or just a centroid (Sejong's synthesized parcels). The
    caller is responsible for rendering point-only parcels as a small marker.
    """
    # Only require positional coordinates; boundary may be null for synthesized
    # parcels (e.g., Sejong, where parcels are recovered from CSV jibun fields).
    conditions = ["p.longitude IS NOT NULL"]
    params = {"limit": min(limit, 80000)}

    if min_lon is not None:
        conditions.append("p.longitude >= $min_lon")
        params["min_lon"] = min_lon
    if max_lon is not None:
        conditions.append("p.longitude <= $max_lon")
        params["max_lon"] = max_lon
    if min_lat is not None:
        conditions.append("p.latitude >= $min_lat")
        params["min_lat"] = min_lat
    if max_lat is not None:
        conditions.append("p.latitude <= $max_lat")
        params["max_lat"] = max_lat
    if land_category:
        conditions.append("p.land_category = $land_category")
        params["land_category"] = land_category

    where_clause = " AND ".join(conditions)
    rows = db.query(
        f"""
        MATCH (p:Parcel)
        WHERE {where_clause}
        RETURN p.uid AS uid, p.pnu AS pnu,
               COALESCE(p.jibun,
                        CASE WHEN p.jibun_bon IS NOT NULL THEN
                          (CASE WHEN p.mountain = '1' THEN '산 ' ELSE '' END +
                           p.jibun_bon +
                           CASE WHEN p.jibun_bu IS NOT NULL AND p.jibun_bu <> '' AND p.jibun_bu <> '0'
                                THEN '-' + p.jibun_bu ELSE '' END)
                        ELSE NULL END) AS jibun,
               COALESCE(p.land_category, 'building_site') AS cat,
               COALESCE(p.land_cat_code, '') AS cat_code,
               COALESCE(p.area_sq_m, 0) AS area,
               p.boundary AS boundary,
               p.longitude AS lon, p.latitude AS lat,
               COALESCE(p.gsid, '') AS gsid,
               COALESCE(p.subtype, '') AS subtype,
               COALESCE(p.building_count, 0) AS building_count
        LIMIT $limit
        """,
        **params,
    )

    # Land category color mapping
    cat_colors = {
        "building_site": "#E8D44D",
        "paddy": "#88CC88",
        "field": "#AADD66",
        "forest": "#228B22",
        "road": "#AAAAAA",
        "river": "#4488CC",
        "ditch": "#5599BB",
        "embankment": "#998877",
        "miscellaneous": "#CCAA88",
        "factory": "#CC6644",
        "school": "#9966CC",
        "parking": "#888888",
        "religious": "#CC88CC",
        "sports": "#44AACC",
        "recreation": "#FFAA44",
        "park": "#66BB66",
        "pasture": "#77BB44",
        "orchard": "#CCDD44",
        "cemetery": "#887766",
        "mineral": "#6688AA",
        "salt": "#BBCCDD",
        "aquaculture": "#3399AA",
        "waterway": "#5577BB",
        "warehouse": "#AA7744",
        "historic": "#CC9966",
        "rail": "#7788AA",
        "gas_station": "#FF8866",
        "unknown": "#DDDDDD",
    }

    parcels = []
    for r in rows:
        cat = r["cat"] or "unknown"
        parcels.append({
            "uid": r["uid"],
            "pnu": r["pnu"],
            "jibun": r["jibun"] or "",
            "cat": cat,
            "cat_code": r["cat_code"] or "",
            "area": r["area"] or 0,
            "color": cat_colors.get(cat, "#DDDDDD"),
            "boundary": r["boundary"],
            "lon": r["lon"],
            "lat": r["lat"],
            "gsid": r["gsid"] or "",
            "subtype": r["subtype"] or "",
            "building_count": r.get("building_count", 0),
            "is_point": not bool(r["boundary"]),  # marks synthesized point-only parcels
        })

    return {"count": len(parcels), "parcels": parcels}


# ── 11. Road List (for dropdown) ──────────────────────────────────
@router.get("/roads")
def list_roads():
    """Return all roads with uid and name for UI dropdowns."""
    rows = db.query(
        "MATCH (r:Road) RETURN r.uid AS uid, r.name AS name ORDER BY r.name"
    )
    return {"roads": rows}


# ── 12. Coverage Dead Zone Analysis ───────────────────────────────
@router.get("/coverage")
def coverage_analysis(facility_type: str = "all", admin_dong: str = None, limit: int = 200):
    """Find buildings lacking shelter/transit/park access (coverage dead zones).

    facility_type: shelter | transit | park | all
    admin_dong: filter by administrative dong name (optional)
    """
    # Map facility type to relationship
    rel_map = {
        "shelter": "NEAREST_SHELTER",
        "transit": "ACCESSIBLE_BY_TRANSIT",
        "park": "NEAR_PARK",
    }

    # ── Dong-level summary statistics ──
    dong_cond = "AND b.admin_dong_name = $dong" if admin_dong else ""
    dong_params = {"dong": admin_dong} if admin_dong else {}

    summary_rows = db.query(
        f"""
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL AND b.admin_dong_name IS NOT NULL {dong_cond}
        WITH b.admin_dong_name AS dong, count(b) AS total,
             sum(CASE WHEN NOT (b)-[:NEAREST_SHELTER]->() THEN 1 ELSE 0 END) AS no_shelter,
             sum(CASE WHEN NOT (b)-[:ACCESSIBLE_BY_TRANSIT]->() THEN 1 ELSE 0 END) AS no_transit,
             sum(CASE WHEN NOT (b)-[:NEAR_PARK]->() THEN 1 ELSE 0 END) AS no_park
        RETURN dong, total, no_shelter, no_transit, no_park
        ORDER BY dong
        """,
        **dong_params,
    )

    # Calculate totals and gap ratio
    total_all = sum(r["total"] for r in summary_rows)
    gap_key = {"shelter": "no_shelter", "transit": "no_transit", "park": "no_park"}.get(facility_type, "no_shelter")
    total_gap = sum(r[gap_key] for r in summary_rows) if facility_type != "all" else sum(r["no_shelter"] for r in summary_rows)

    by_dong = []
    for r in summary_rows:
        entry = {
            "dong": r["dong"],
            "total": r["total"],
            "no_shelter": r["no_shelter"],
            "no_transit": r["no_transit"],
            "no_park": r["no_park"],
        }
        if facility_type == "all":
            entry["gap_ratio"] = round(r["no_shelter"] / r["total"] * 100, 1) if r["total"] > 0 else 0
        else:
            entry["gap_ratio"] = round(r[gap_key] / r["total"] * 100, 1) if r["total"] > 0 else 0
        by_dong.append(entry)
    by_dong.sort(key=lambda x: x["gap_ratio"], reverse=True)

    # ── Dead zone building list ──
    if facility_type in rel_map:
        rel = rel_map[facility_type]
        not_clause = f"AND NOT (b)-[:{rel}]->()"
    else:
        # 'all' — buildings lacking ALL three
        not_clause = "AND NOT (b)-[:NEAREST_SHELTER]->() AND NOT (b)-[:ACCESSIBLE_BY_TRANSIT]->() AND NOT (b)-[:NEAR_PARK]->()"

    bldg_dong_cond = f"AND b.admin_dong_name = $dong" if admin_dong else ""
    buildings = db.query(
        f"""
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL {not_clause} {bldg_dong_cond}
        RETURN b.uid AS uid, b.gsid AS gsid, b.name AS name,
               b.longitude AS lon, b.latitude AS lat,
               b.building_type AS btype, b.admin_dong_name AS dong,
               b.road_address AS addr
        ORDER BY b.admin_dong_name, b.name
        LIMIT $limit
        """,
        limit=limit, **dong_params,
    )

    return {
        "facility_type": facility_type,
        "summary": {
            "total": total_all,
            "gap_count": total_gap if facility_type != "all" else len(buildings),
            "gap_ratio": round(total_gap / total_all * 100, 1) if total_all > 0 and facility_type != "all" else None,
        },
        "by_dong": by_dong,
        "buildings": buildings,
    }


# ── 13. Road Closure Impact ──────────────────────────────────────
@router.get("/road_impact")
def road_impact(road_uid: str):
    """Analyze impact of closing a road: affected entities + alternative roads."""
    resolved = _resolve_uid(road_uid)

    # Get road info
    road_rows = db.query(
        "MATCH (r:Road {uid: $uid}) RETURN r.name AS name, r.coordinates AS coords",
        uid=resolved,
    )
    if not road_rows:
        raise HTTPException(404, f"Road not found: {road_uid}")
    road_info = {"uid": resolved, "name": road_rows[0]["name"], "coordinates": road_rows[0]["coords"]}

    # All affected entities + alternative roads
    rows = db.query(
        """
        MATCH (r:Road {uid: $uid})
        MATCH (entity)-[:ON_STREET|FRONTS_ROAD|ALONG_ROAD|ALONG]->(r)
        WHERE entity.uid IS NOT NULL
        WITH r, entity, labels(entity)[0] AS label
        OPTIONAL MATCH (entity)-[:ON_STREET|FRONTS_ROAD|ALONG_ROAD|ALONG]->(alt:Road)
        WHERE alt.uid <> r.uid
        RETURN entity.uid AS uid, entity.gsid AS gsid, entity.name AS name,
               label, entity.longitude AS lon, entity.latitude AS lat,
               collect(DISTINCT {uid: alt.uid, name: alt.name}) AS alt_roads
        ORDER BY label, entity.name
        """,
        uid=resolved,
    )

    # Categorize
    critical = []
    all_affected = []
    by_label = {}
    for r in rows:
        has_alt = len(r["alt_roads"]) > 0 and r["alt_roads"][0]["uid"] is not None
        alt_list = [a for a in r["alt_roads"] if a["uid"] is not None] if has_alt else []
        entry = {
            "uid": r["uid"], "gsid": r["gsid"], "name": r["name"],
            "label": r["label"], "lon": r["lon"], "lat": r["lat"],
            "alt_roads": alt_list, "has_alternative": bool(alt_list),
        }
        all_affected.append(entry)
        if not alt_list:
            critical.append(entry)
        lbl = r["label"] or "Unknown"
        by_label[lbl] = by_label.get(lbl, 0) + 1

    return {
        "road": road_info,
        "total_affected": len(all_affected),
        "critical_count": len(critical),
        "by_label": by_label,
        "critical": critical,
        "all_affected": all_affected,
    }


# ── 14. Safety Profile ───────────────────────────────────────────
@router.get("/safety_profile")
def safety_profile(uid: str):
    """Comprehensive safety profile of an entity using all KG relationships."""
    resolved = _resolve_uid(uid)

    rows = db.query(
        """
        MATCH (t {uid: $uid})
        OPTIONAL MATCH (t)-[:NEAREST_SHELTER]->(sh:ThingsAddr)
        WITH t, collect(DISTINCT {uid: sh.uid, name: sh.name, type: sh.iot_type, type_name: sh.iot_type_name}) AS shelters
        OPTIONAL MATCH (t)-[:ACCESSIBLE_BY_TRANSIT]->(tr:ThingsAddr)
        WITH t, shelters, collect(DISTINCT {uid: tr.uid, name: tr.name, type: tr.iot_type, type_name: tr.iot_type_name}) AS transit
        OPTIONAL MATCH (t)-[:NEAR_PARK]->(pk:ThingsAddr)
        WITH t, shelters, transit, collect(DISTINCT {uid: pk.uid, name: pk.name, type: pk.iot_type, type_name: pk.iot_type_name}) AS parks
        OPTIONAL MATCH (sen:Sensor)-[:MONITORS]->(t)
        WITH t, shelters, transit, parks,
             collect(DISTINCT {uid: sen.uid, type: sen.sensor_type, value: sen.value, unit: sen.unit}) AS sensors
        OPTIONAL MATCH (cam:Camera)-[:MONITORS]->(t)
        WITH t, shelters, transit, parks, sensors,
             collect(DISTINCT {uid: cam.uid, name: cam.name, status: cam.status}) AS cameras
        OPTIONAL MATCH (t)-[:ON_STREET|FRONTS_ROAD]->(rd:Road)
        WITH t, shelters, transit, parks, sensors, cameras,
             collect(DISTINCT {uid: rd.uid, name: rd.name}) AS roads
        RETURN t.uid AS uid, t.gsid AS gsid, labels(t)[0] AS label, t.name AS name,
               t.longitude AS lon, t.latitude AS lat,
               shelters, transit, parks, sensors, cameras, roads
        """,
        uid=resolved,
    )
    if not rows:
        raise HTTPException(404, f"Entity not found: {uid}")
    r = rows[0]

    # Filter out null entries from OPTIONAL MATCH
    def _clean(lst):
        return [x for x in lst if x.get("uid") is not None]

    shelters = _clean(r["shelters"])
    transit = _clean(r["transit"])
    parks = _clean(r["parks"])
    sensors = _clean(r["sensors"])
    cameras = _clean(r["cameras"])
    roads = _clean(r["roads"])

    # Compute safety scores
    def _score(count):
        if count == 0: return 0
        if count == 1: return 50
        return 100

    shelter_score = _score(len(shelters))
    transit_score = _score(len(transit))
    park_score = _score(len(parks))
    has_sensor = len(sensors) > 0
    has_camera = len(cameras) > 0
    if has_sensor and has_camera:
        monitoring_score = 100
    elif has_sensor or has_camera:
        monitoring_score = 50
    else:
        monitoring_score = 0
    road_score = _score(len(roads))

    overall = (
        shelter_score * 0.30
        + transit_score * 0.20
        + park_score * 0.15
        + monitoring_score * 0.20
        + road_score * 0.15
    )

    return {
        "entity": {
            "uid": r["uid"], "gsid": r["gsid"], "label": r["label"],
            "name": r["name"], "lon": r["lon"], "lat": r["lat"],
        },
        "scores": {
            "shelter": shelter_score,
            "transit": transit_score,
            "park": park_score,
            "monitoring": monitoring_score,
            "road": road_score,
            "overall": round(overall, 1),
        },
        "details": {
            "shelters": shelters,
            "transit": transit,
            "parks": parks,
            "sensors": sensors,
            "cameras": cameras,
            "roads": roads,
        },
    }


# ── 15. Dong Infrastructure Comparison ────────────────────────────
@router.get("/analytics/dong_comparison")
def dong_comparison(metric: str = "overall"):
    """Compare administrative dongs by infrastructure coverage metrics."""

    # Query 1: shelter / transit / park coverage + basic stats
    base_rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.admin_dong_name IS NOT NULL AND b.longitude IS NOT NULL
        WITH b.admin_dong_name AS dong, b
        WITH dong, count(b) AS total,
             sum(CASE WHEN (b)-[:NEAREST_SHELTER]->() THEN 1 ELSE 0 END) AS has_shelter,
             sum(CASE WHEN (b)-[:ACCESSIBLE_BY_TRANSIT]->() THEN 1 ELSE 0 END) AS has_transit,
             sum(CASE WHEN (b)-[:NEAR_PARK]->() THEN 1 ELSE 0 END) AS has_park,
             avg(b.floors) AS avg_floors,
             avg(b.longitude) AS center_lon,
             avg(b.latitude) AS center_lat
        RETURN dong, total, has_shelter, has_transit, has_park,
               avg_floors, center_lon, center_lat
        ORDER BY dong
        """
    )

    # Query 2: monitoring coverage (MONITORS direction is reversed)
    mon_rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.admin_dong_name IS NOT NULL AND b.longitude IS NOT NULL
        OPTIONAL MATCH (m)-[:MONITORS]->(b) WHERE m:Sensor OR m:Camera
        WITH b.admin_dong_name AS dong, b,
             CASE WHEN m IS NOT NULL THEN 1 ELSE 0 END AS monitored
        WITH dong, count(DISTINCT b) AS total, sum(monitored) AS has_monitoring
        RETURN dong, total, has_monitoring
        ORDER BY dong
        """
    )

    # Query 3: road access
    road_rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.admin_dong_name IS NOT NULL AND b.longitude IS NOT NULL
        WITH b.admin_dong_name AS dong, b
        WITH dong, count(b) AS total,
             sum(CASE WHEN (b)-[:ON_STREET|FRONTS_ROAD]->() THEN 1 ELSE 0 END) AS has_road
        RETURN dong, total, has_road
        ORDER BY dong
        """
    )

    # Merge results by dong
    mon_map = {r["dong"]: r["has_monitoring"] for r in mon_rows}
    road_map = {r["dong"]: r["has_road"] for r in road_rows}

    dongs = []
    total_all = 0
    sum_shelter = sum_transit = sum_park = sum_mon = sum_road = 0

    for r in base_rows:
        dong = r["dong"]
        total = r["total"]
        if total == 0:
            continue
        total_all += total

        s_pct = round(r["has_shelter"] / total * 100, 1)
        t_pct = round(r["has_transit"] / total * 100, 1)
        p_pct = round(r["has_park"] / total * 100, 1)
        m_pct = round(mon_map.get(dong, 0) / total * 100, 1)
        rd_pct = round(road_map.get(dong, 0) / total * 100, 1)
        overall = round(s_pct * 0.30 + t_pct * 0.20 + p_pct * 0.15 + m_pct * 0.20 + rd_pct * 0.15, 1)

        sum_shelter += r["has_shelter"]
        sum_transit += r["has_transit"]
        sum_park += r["has_park"]
        sum_mon += mon_map.get(dong, 0)
        sum_road += road_map.get(dong, 0)

        dongs.append({
            "dong": dong,
            "total": total,
            "avg_floors": round(r["avg_floors"] or 0, 1),
            "shelter_pct": s_pct,
            "transit_pct": t_pct,
            "park_pct": p_pct,
            "monitoring_pct": m_pct,
            "road_pct": rd_pct,
            "overall_score": overall,
            "center_lon": round(r["center_lon"], 6) if r["center_lon"] else None,
            "center_lat": round(r["center_lat"], 6) if r["center_lat"] else None,
        })

    # Sort by selected metric
    metric_key = {
        "overall": "overall_score", "shelter": "shelter_pct", "transit": "transit_pct",
        "park": "park_pct", "monitoring": "monitoring_pct", "road": "road_pct",
    }.get(metric, "overall_score")
    dongs.sort(key=lambda x: x[metric_key])

    # City-wide averages
    city_avg = {}
    if total_all > 0:
        city_avg = {
            "shelter_pct": round(sum_shelter / total_all * 100, 1),
            "transit_pct": round(sum_transit / total_all * 100, 1),
            "park_pct": round(sum_park / total_all * 100, 1),
            "monitoring_pct": round(sum_mon / total_all * 100, 1),
            "road_pct": round(sum_road / total_all * 100, 1),
        }
        city_avg["overall_score"] = round(
            city_avg["shelter_pct"] * 0.30 + city_avg["transit_pct"] * 0.20
            + city_avg["park_pct"] * 0.15 + city_avg["monitoring_pct"] * 0.20
            + city_avg["road_pct"] * 0.15, 1
        )

    return {"metric": metric, "total_buildings": total_all, "dongs": dongs, "city_avg": city_avg}


# ── 16. KG Connectivity Analysis ──────────────────────────────────
@router.get("/analytics/connectivity")
def connectivity_analysis(analysis: str = "density", max_degree: int = 2, limit: int = 200):
    """Analyze KG connectivity: isolated nodes, relationship density, connection patterns."""

    if analysis == "isolated":
        # Count total isolated
        count_rows = db.query(
            """
            MATCH (n)
            WHERE n.uid IS NOT NULL AND n.longitude IS NOT NULL
            OPTIONAL MATCH (n)-[r]-()
            WITH n, labels(n)[0] AS label, count(r) AS degree
            WHERE degree <= $max_degree
            RETURN label, count(n) AS cnt
            ORDER BY cnt DESC
            """,
            max_degree=max_degree,
        )
        by_label = {r["label"]: r["cnt"] for r in count_rows}
        total_isolated = sum(by_label.values())

        # Dong breakdown
        dong_rows = db.query(
            """
            MATCH (n)
            WHERE n.uid IS NOT NULL AND n.longitude IS NOT NULL AND n.admin_dong_name IS NOT NULL
            OPTIONAL MATCH (n)-[r]-()
            WITH n, count(r) AS degree
            WHERE degree <= $max_degree
            RETURN n.admin_dong_name AS dong, count(n) AS cnt
            ORDER BY cnt DESC
            """,
            max_degree=max_degree,
        )
        by_dong = {r["dong"]: r["cnt"] for r in dong_rows}

        # Entity list
        entities = db.query(
            """
            MATCH (n)
            WHERE n.uid IS NOT NULL AND n.longitude IS NOT NULL
            OPTIONAL MATCH (n)-[r]-()
            WITH n, labels(n)[0] AS label, count(r) AS degree
            WHERE degree <= $max_degree
            RETURN n.uid AS uid, n.gsid AS gsid, n.name AS name, label,
                   degree, n.longitude AS lon, n.latitude AS lat,
                   n.admin_dong_name AS dong
            ORDER BY degree, label
            LIMIT $limit
            """,
            max_degree=max_degree,
            limit=limit,
        )

        return {
            "analysis": "isolated",
            "max_degree": max_degree,
            "total_isolated": total_isolated,
            "by_label": by_label,
            "by_dong": by_dong,
            "entities": entities,
        }

    elif analysis == "density":
        rows = db.query(
            """
            MATCH (b:Building)
            WHERE b.admin_dong_name IS NOT NULL AND b.longitude IS NOT NULL
            OPTIONAL MATCH (b)-[r]-()
            WITH b.admin_dong_name AS dong, b, count(r) AS degree
            RETURN dong, count(b) AS building_count,
                   round(avg(toFloat(degree)), 1) AS avg_degree,
                   max(degree) AS max_degree,
                   sum(CASE WHEN degree <= 2 THEN 1 ELSE 0 END) AS low_conn_count,
                   percentileDisc(degree, 0.5) AS median_degree,
                   avg(b.longitude) AS center_lon,
                   avg(b.latitude) AS center_lat
            ORDER BY avg_degree
            """
        )
        dongs = []
        for r in rows:
            dongs.append({
                "dong": r["dong"],
                "building_count": r["building_count"],
                "avg_degree": r["avg_degree"],
                "max_degree": r["max_degree"],
                "median_degree": r["median_degree"],
                "low_conn_count": r["low_conn_count"],
                "low_conn_pct": round(r["low_conn_count"] / r["building_count"] * 100, 1) if r["building_count"] else 0,
                "center_lon": round(r["center_lon"], 6) if r["center_lon"] else None,
                "center_lat": round(r["center_lat"], 6) if r["center_lat"] else None,
            })

        return {"analysis": "density", "dongs": dongs}

    elif analysis == "patterns":
        # Filter to main entity types only (skip State/Change labels)
        main_labels = {"Building", "Road", "Parcel", "ThingsAddr", "Sensor", "Camera",
                       "Zone", "Vehicle", "ParkingLot", "Facility", "Tree"}
        rows = db.query(
            """
            MATCH (a)-[r]->(b)
            WHERE a.uid IS NOT NULL AND b.uid IS NOT NULL
            WITH labels(a)[0] AS src, type(r) AS rel, labels(b)[0] AS tgt
            RETURN src, rel, tgt, count(*) AS cnt
            ORDER BY cnt DESC
            """
        )

        matrix = []
        labels_set = set()
        rel_types_set = set()
        for r in rows:
            if r["src"] in main_labels and r["tgt"] in main_labels:
                matrix.append({"src": r["src"], "rel": r["rel"], "tgt": r["tgt"], "cnt": r["cnt"]})
                labels_set.add(r["src"])
                labels_set.add(r["tgt"])
                rel_types_set.add(r["rel"])

        return {
            "analysis": "patterns",
            "matrix": matrix,
            "labels": sorted(labels_set),
            "rel_types": sorted(rel_types_set),
        }

    raise HTTPException(400, f"Unknown analysis type: {analysis}")


# =============================================================================
# Road Linear Reference (도로 선형참조)
# =============================================================================

@router.get("/road_profiles")
def road_profiles():
    """도로 선형참조 프로파일 목록 — R² 기준 내림차순."""
    rows = db.query("""
        MATCH (r:Road)
        WHERE r.profile_r_squared IS NOT NULL
        RETURN r.uid AS uid, r.name AS name, r.road_type AS road_type,
               r.profile_r_squared AS r_squared,
               r.profile_meters_per_unit AS meters_per_unit,
               r.profile_building_count AS building_count,
               r.profile_min_building_num AS min_num,
               r.profile_max_building_num AS max_num,
               r.profile_slope_lon AS slope_lon,
               r.profile_intercept_lon AS intercept_lon,
               r.profile_slope_lat AS slope_lat,
               r.profile_intercept_lat AS intercept_lat,
               r.residual_std_m AS residual_std_m
        ORDER BY r.profile_r_squared DESC
    """)
    # Classify quality
    good = sum(1 for r in rows if r["r_squared"] and r["r_squared"] > 0.7)
    moderate = sum(1 for r in rows if r["r_squared"] and 0.3 <= r["r_squared"] <= 0.7)
    poor = sum(1 for r in rows if r["r_squared"] and r["r_squared"] < 0.3)

    return {
        "count": len(rows),
        "quality": {"good": good, "moderate": moderate, "poor": poor},
        "profiles": rows,
    }


@router.get("/road_entities/{road_name}")
def road_entities(road_name: str):
    """도로 위 모든 엔티티를 건물본번 순으로 정렬 (좌/우 분리)."""
    rows = db.query("""
        MATCH (n)
        WHERE n.road_name = $road_name AND n.building_main > 0
        RETURN n.uid AS uid, labels(n)[0] AS label, n.name AS name,
               n.building_main AS building_main, n.building_sub AS building_sub,
               n.road_side AS road_side,
               n.road_distance_m AS road_distance_m,
               n.road_position AS road_position,
               n.longitude AS longitude, n.latitude AS latitude,
               n.gsid AS gsid,
               n.building_type AS btype, n.iot_type_name AS iot_type
        ORDER BY n.building_main
    """, road_name=road_name)

    left = [r for r in rows if r.get("road_side") == "left"]
    right = [r for r in rows if r.get("road_side") == "right"]

    # Road profile info
    profile_row = db.query("""
        MATCH (r:Road {name: $road_name})
        WHERE r.profile_r_squared IS NOT NULL
        RETURN r.profile_r_squared AS r_squared,
               r.profile_meters_per_unit AS meters_per_unit,
               r.profile_building_count AS building_count,
               r.profile_min_building_num AS min_num,
               r.profile_max_building_num AS max_num,
               r.coordinates AS coordinates
        LIMIT 1
    """, road_name=road_name)
    profile = profile_row[0] if profile_row else None

    return {
        "road_name": road_name,
        "total": len(rows),
        "left_count": len(left),
        "right_count": len(right),
        "left_side": left,
        "right_side": right,
        "all_entities": rows,
        "profile": profile,
    }


@router.get("/geocode")
def geocode_road_address(road_name: str, building_main: int):
    """도로명 + 건물본번으로 좌표 추정 (선형참조 모델)."""
    rows = db.query("""
        MATCH (r:Road {name: $road_name})
        WHERE r.profile_r_squared IS NOT NULL
        RETURN r.profile_slope_lon AS slope_lon,
               r.profile_intercept_lon AS int_lon,
               r.profile_slope_lat AS slope_lat,
               r.profile_intercept_lat AS int_lat,
               r.profile_r_squared AS r_squared,
               r.profile_meters_per_unit AS mpu,
               r.profile_min_building_num AS min_n,
               r.profile_max_building_num AS max_n
    """, road_name=road_name)

    if not rows:
        raise HTTPException(404, f"도로 프로파일 없음: {road_name}")

    p = rows[0]
    est_lon = p["slope_lon"] * building_main + p["int_lon"]
    est_lat = p["slope_lat"] * building_main + p["int_lat"]
    road_side = "left" if building_main % 2 == 1 else "right"
    distance_m = (building_main - p["min_n"]) * p["mpu"] if p["mpu"] else 0

    # 근처 엔티티 (건물본번 ±10 이내)
    nearby = db.query("""
        MATCH (n)
        WHERE n.road_name = $road_name AND n.building_main > 0
              AND abs(n.building_main - $num) <= 10
        RETURN n.uid AS uid, labels(n)[0] AS label, n.name AS name,
               n.building_main AS building_main,
               n.longitude AS longitude, n.latitude AS latitude
        ORDER BY abs(n.building_main - $num) LIMIT 5
    """, road_name=road_name, num=building_main)

    # Add approximate distance_m for each nearby entity
    import math
    for ne in nearby:
        if ne.get("longitude") and ne.get("latitude"):
            dx = (ne["longitude"] - est_lon) * 111320 * math.cos(math.radians(est_lat))
            dy = (ne["latitude"] - est_lat) * 110540
            ne["distance_m"] = round(math.sqrt(dx*dx + dy*dy), 1)
        else:
            ne["distance_m"] = None

    return {
        "road_name": road_name,
        "building_main": building_main,
        "estimated_lon": round(est_lon, 6),
        "estimated_lat": round(est_lat, 6),
        "road_side": road_side,
        "distance_from_start_m": round(distance_m, 1),
        "confidence": {
            "r_squared": p["r_squared"],
            "in_range": p["min_n"] <= building_main <= p["max_n"],
        },
        "nearby_entities": nearby,
    }
