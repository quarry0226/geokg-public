"""
Relationship Enrichment Module for GeoKG.

Creates meaningful relationships between entities based on:
- Road address (도로명주소) matching
- Spatial proximity
- Administrative district (행정동)
- Building usage type clustering
- Sensor/Camera/Facility service area

All operations use server-side Cypher for efficiency with 58K+ buildings.
"""

import json
import math
import re
from collections import defaultdict


def enrich_relationships(db):
    """Run all relationship enrichment steps after base graph is built.

    Delegates to the rule-based engine defined in relationship_rules.py.
    The engine automatically determines which rules apply based on
    existing node labels and their properties.

    Args:
        db: Neo4jClient instance with an active connection.

    Returns:
        dict: Timing report from the rule engine, or None on error.
    """
    from backend.geokg.relationship_engine import enrich_by_rules, _clear_label_cache
    _clear_label_cache()
    return enrich_by_rules(db)


# ── 0. ON_PARCEL: Building → Parcel (PNU 매칭) ──────────────────

def _enrich_on_parcel(db):
    """Connect buildings to their underlying cadastral parcels via PNU matching.
    Buildings have a 'pnu' property matching the Parcel node's 'pnu' property.
    Uses indexed PNU lookup (no spatial cross-join)."""
    # Check if Parcel nodes exist
    parcel_count = db.query("MATCH (p:Parcel) RETURN count(p) AS cnt")
    if not parcel_count or parcel_count[0]["cnt"] == 0:
        print("[Enrichment] ON_PARCEL: No Parcel nodes found, skipping.")
        return

    # Indexed PNU matching: Building.pnu → Parcel.pnu (uses Parcel.pnu index)
    result = db.query(
        """
        MATCH (b:Building)
        WHERE b.pnu IS NOT NULL AND b.pnu <> ''
        WITH b
        MATCH (p:Parcel {pnu: b.pnu})
        CREATE (b)-[:ON_PARCEL]->(p)
        RETURN count(*) AS cnt
        """
    )
    count = result[0]["cnt"] if result else 0
    print(f"[Enrichment] ON_PARCEL: {count} relationships created (PNU index match).")


# ── 1. ON_STREET: Building → Road (도로명 매칭) ──────────────────

def _enrich_on_street(db):
    """Connect buildings to Road nodes by KAIS RN_CD attribute join.

    The KAIS road-name address master ships ADR_MNG_NO[8:15] = RN_CD,
    which is guaranteed by the upstream publication contract to resolve
    to a TL_SPRD_MANAGE road segment with the same RN_CD value. We use
    that exact join here (100% of road-name-addressed buildings, by
    construction).
    """
    # Group roads by rn_cd
    roads = db.query("MATCH (r:Road) RETURN r.uid AS uid, r.rn_cd AS rn_cd, r.name AS name")
    by_rncd: dict[str, list[str]] = defaultdict(list)
    for r in roads:
        rn = (r["rn_cd"] or "").strip()
        if rn:
            by_rncd[rn].append(r["uid"])

    if not by_rncd:
        # Fall back to road_address CONTAINS matching (legacy)
        road_map = {r["name"]: r["uid"] for r in roads if r["name"]}
        count = 0
        for road_name, road_uid in road_map.items():
            result = db.query(
                """
                MATCH (b:Building), (r:Road {uid: $road_uid})
                WHERE b.road_address CONTAINS $road_name
                CREATE (b)-[:ON_STREET]->(r)
                RETURN count(*) AS cnt
                """,
                road_name=road_name, road_uid=road_uid,
            )
            count += result[0]["cnt"] if result else 0
        print(f"[Enrichment] ON_STREET (legacy road_name CONTAINS): {count} relationships created.")
        return

    # RN_CD-keyed exact join (v2 path)
    # building.nf_id (= ADR_MNG_NO) positions [8:15] are the road's RN_CD
    count = 0
    for rn_cd, road_uids in by_rncd.items():
        # Each road of this RN_CD gets the same building set; use the first road as anchor
        anchor = road_uids[0]
        result = db.query(
            """
            MATCH (b:Building), (r:Road {uid: $anchor})
            WHERE size(b.nf_id) >= 15 AND substring(b.nf_id, 8, 7) = $rn_cd
            CREATE (b)-[:ON_STREET]->(r)
            RETURN count(*) AS cnt
            """,
            anchor=anchor, rn_cd=rn_cd,
        )
        n = result[0]["cnt"] if result else 0
        count += n
    print(f"[Enrichment] ON_STREET (RN_CD join): {count} relationships created.")


# ── 2. FRONTS_ROAD: Building → nearest Road ──────────────────────

def _enrich_fronts_road(db):
    """Connect each building to its nearest Road node by distance."""
    # Get road midpoints for distance calculation
    roads = db.query("MATCH (r:Road) RETURN r.uid AS uid, r.coordinates AS coords")
    road_points = []
    for r in roads:
        coords = json.loads(r["coords"]) if r["coords"] else []
        if coords:
            mid = coords[len(coords) // 2]
            road_points.append({"uid": r["uid"], "lon": mid[0], "lat": mid[1]})

    if not road_points:
        print("[Enrichment] FRONTS_ROAD: No road coordinates available, skipping.")
        return

    # For efficiency, do this in batches via Cypher
    # Build a list of road positions to pass as parameter
    result = db.query(
        """
        UNWIND $roads AS rd
        WITH rd
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL
        WITH b, rd,
             (b.longitude - rd.lon) * 111320 * cos(radians(b.latitude)) AS dx,
             (b.latitude - rd.lat) * 110540 AS dy
        WITH b, rd, dx*dx + dy*dy AS dist_sq
        ORDER BY b.uid, dist_sq
        WITH b, collect(rd)[0] AS nearest_road
        MATCH (r:Road {uid: nearest_road.uid})
        WHERE NOT (b)-[:ON_STREET]->(r)
        CREATE (b)-[:FRONTS_ROAD]->(r)
        RETURN count(*) AS cnt
        """,
        roads=road_points,
    )
    count = result[0]["cnt"] if result else 0
    print(f"[Enrichment] FRONTS_ROAD: {count} relationships created.")


# ── 3. SAME_DONG: Building ↔ Building (같은 행정동, grid-sampled) ──

def _enrich_same_dong(db):
    """Connect buildings in the same administrative district (행정동).
    Uses grid sampling to avoid O(n^2) explosion."""
    # Get buildings with admin_dong and coordinates
    rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.admin_dong IS NOT NULL AND b.admin_dong <> ''
              AND b.longitude IS NOT NULL
        RETURN b.uid AS uid, b.longitude AS lon, b.latitude AS lat,
               b.admin_dong AS dong
        """
    )

    # Group by grid cell (0.002 degree ≈ 200m) + dong
    grid = defaultdict(list)
    for r in rows:
        gx = int(r["lon"] * 500)   # 0.002 degree grid
        gy = int(r["lat"] * 500)
        key = (gx, gy, r["dong"])
        grid[key].append(r["uid"])

    # Create pairs: max 3 pairs per cell
    pairs = []
    for cell_uids in grid.values():
        if len(cell_uids) < 2:
            continue
        for i in range(min(len(cell_uids) - 1, 3)):
            pairs.append({"from_uid": cell_uids[i], "to_uid": cell_uids[i + 1]})

    # Batch create
    batch_size = 500
    count = 0
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        db.graph.run(
            """
            UNWIND $batch AS rel
            MATCH (a:Building {uid: rel.from_uid}), (b:Building {uid: rel.to_uid})
            CREATE (a)-[:SAME_DONG]->(b)
            """,
            batch=batch,
        )
        count += len(batch)

    print(f"[Enrichment] SAME_DONG: {count} relationships created.")


# ── 4. SAME_USAGE: Building ↔ Building (같은 용도, grid-sampled) ──

def _enrich_same_usage(db):
    """Connect buildings of the same type within spatial proximity.
    Uses grid sampling to create local clusters."""
    rows = db.query(
        """
        MATCH (b:Building)
        WHERE b.building_type IS NOT NULL AND b.longitude IS NOT NULL
        RETURN b.uid AS uid, b.longitude AS lon, b.latitude AS lat,
               b.building_type AS btype
        """
    )

    # Group by grid cell (0.003 degree ≈ 300m) + building_type
    grid = defaultdict(list)
    for r in rows:
        gx = int(r["lon"] / 0.003)
        gy = int(r["lat"] / 0.003)
        key = (gx, gy, r["btype"])
        grid[key].append(r["uid"])

    # Create pairs: max 3 pairs per cell
    pairs = []
    for cell_uids in grid.values():
        if len(cell_uids) < 2:
            continue
        for i in range(min(len(cell_uids) - 1, 3)):
            pairs.append({"from_uid": cell_uids[i], "to_uid": cell_uids[i + 1]})

    # Batch create
    batch_size = 500
    count = 0
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        db.graph.run(
            """
            UNWIND $batch AS rel
            MATCH (a:Building {uid: rel.from_uid}), (b:Building {uid: rel.to_uid})
            CREATE (a)-[:SAME_USAGE]->(b)
            """,
            batch=batch,
        )
        count += len(batch)

    print(f"[Enrichment] SAME_USAGE: {count} relationships created.")


# ── 5. MONITORS: Sensor/Camera → nearby Buildings ────────────────

def _enrich_monitors(db):
    """Connect sensors and cameras to nearby buildings they monitor."""
    # Radius in degrees (approx 100m)
    radius_deg = 100.0 / 111320.0  # ~0.0009 degrees

    result = db.query(
        """
        MATCH (s)
        WHERE (s:Sensor OR s:Camera) AND s.longitude IS NOT NULL
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL
              AND abs(b.longitude - s.longitude) < $radius
              AND abs(b.latitude - s.latitude) < $radius
        WITH s, b,
             (b.longitude - s.longitude) * 111320 * cos(radians(s.latitude)) AS dx,
             (b.latitude - s.latitude) * 110540 AS dy
        WITH s, b, sqrt(dx*dx + dy*dy) AS dist_m
        WHERE dist_m < 100
        WITH s, b, dist_m
        ORDER BY s.uid, dist_m
        With s, collect(b)[..10] AS nearby
        UNWIND nearby AS nb
        CREATE (s)-[:MONITORS]->(nb)
        RETURN count(*) AS cnt
        """,
        radius=radius_deg,
    )
    count = result[0]["cnt"] if result else 0
    print(f"[Enrichment] MONITORS: {count} relationships created.")


# ── 6. SERVES: Facility/ParkingLot → nearby Buildings ────────────

def _enrich_serves(db):
    """Connect facilities and parking lots to nearby buildings they serve."""
    radius_deg = 200.0 / 111320.0  # ~0.0018 degrees

    result = db.query(
        """
        MATCH (f)
        WHERE (f:Facility OR f:ParkingLot) AND f.longitude IS NOT NULL
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL
              AND abs(b.longitude - f.longitude) < $radius
              AND abs(b.latitude - f.latitude) < $radius
        WITH f, b,
             (b.longitude - f.longitude) * 111320 * cos(radians(f.latitude)) AS dx,
             (b.latitude - f.latitude) * 110540 AS dy
        WITH f, b, sqrt(dx*dx + dy*dy) AS dist_m
        WHERE dist_m < 200
        With f, b, dist_m
        ORDER BY f.uid, dist_m
        WITH f, collect(b)[..10] AS nearby
        UNWIND nearby AS nb
        CREATE (f)-[:SERVES]->(nb)
        RETURN count(*) AS cnt
        """,
        radius=radius_deg,
    )
    count = result[0]["cnt"] if result else 0
    print(f"[Enrichment] SERVES: {count} relationships created.")


# ── 7. NEAR: Tree → nearby Buildings ─────────────────────────────

def _enrich_near(db):
    """Connect trees to nearby buildings."""
    radius_deg = 50.0 / 111320.0  # ~0.00045 degrees

    result = db.query(
        """
        MATCH (t:Tree)
        WHERE t.longitude IS NOT NULL
        MATCH (b:Building)
        WHERE b.longitude IS NOT NULL
              AND abs(b.longitude - t.longitude) < $radius
              AND abs(b.latitude - t.latitude) < $radius
        WITH t, b,
             (b.longitude - t.longitude) * 111320 * cos(radians(t.latitude)) AS dx,
             (b.latitude - t.latitude) * 110540 AS dy
        WITH t, b, sqrt(dx*dx + dy*dy) AS dist_m
        WHERE dist_m < 50
        WITH t, b, dist_m
        ORDER BY t.uid, dist_m
        WITH t, collect(b)[..10] AS nearby
        UNWIND nearby AS nb
        CREATE (t)-[:NEAR]->(nb)
        RETURN count(*) AS cnt
        """,
        radius=radius_deg,
    )
    count = result[0]["cnt"] if result else 0
    print(f"[Enrichment] NEAR: {count} relationships created.")


# ── 8. NEAR_BUILDING: ThingsAddr → nearby Buildings (100m, max 5) ──

def _enrich_iot_near_building(db):
    """Connect IoT address entities to nearby buildings within 100m.
    Processes in batches to avoid Neo4j memory limits."""
    # Check if ThingsAddr nodes exist
    iot_count = db.query("MATCH (i:ThingsAddr) RETURN count(i) AS cnt")
    if not iot_count or iot_count[0]["cnt"] == 0:
        print("[Enrichment] NEAR_BUILDING: No ThingsAddr nodes found, skipping.")
        return

    # Get all IoT addresses with coordinates
    iot_nodes = db.query(
        "MATCH (i:ThingsAddr) WHERE i.longitude IS NOT NULL "
        "RETURN i.uid AS uid, i.longitude AS lon, i.latitude AS lat"
    )

    radius_deg = 100.0 / 111320.0  # ~0.0009 degrees
    batch_size = 200
    count = 0

    for i in range(0, len(iot_nodes), batch_size):
        batch_uids = [n["uid"] for n in iot_nodes[i:i + batch_size]]
        result = db.query(
            """
            UNWIND $uids AS iot_uid
            MATCH (i:ThingsAddr {uid: iot_uid})
            MATCH (b:Building)
            WHERE b.longitude IS NOT NULL
                  AND abs(b.longitude - i.longitude) < $radius
                  AND abs(b.latitude - i.latitude) < $radius
            WITH i, b,
                 (b.longitude - i.longitude) * 111320 * cos(radians(i.latitude)) AS dx,
                 (b.latitude - i.latitude) * 110540 AS dy
            WITH i, b, sqrt(dx*dx + dy*dy) AS dist_m
            WHERE dist_m < 100
            WITH i, b, dist_m
            ORDER BY i.uid, dist_m
            WITH i, collect(b)[..5] AS nearby
            UNWIND nearby AS nb
            CREATE (i)-[:NEAR_BUILDING]->(nb)
            RETURN count(*) AS cnt
            """,
            uids=batch_uids,
            radius=radius_deg,
        )
        count += result[0]["cnt"] if result else 0

    print(f"[Enrichment] NEAR_BUILDING: {count} relationships created (ThingsAddr → Building).")


# ── 9. ON_PARCEL: ThingsAddr → Parcel (법정동코드 + 좌표 근접) ──

def _enrich_iot_on_parcel(db):
    """Connect IoT address entities to the nearest parcel in the same 법정동.
    Processes in batches to avoid Neo4j memory limits."""
    # Check if both ThingsAddr and Parcel nodes exist
    check = db.query(
        """
        MATCH (i:ThingsAddr) WITH count(i) AS ic
        MATCH (p:Parcel) WITH ic, count(p) AS pc
        RETURN ic, pc
        """
    )
    if not check or check[0]["ic"] == 0 or check[0]["pc"] == 0:
        print("[Enrichment] IoT ON_PARCEL: No ThingsAddr or Parcel nodes found, skipping.")
        return

    # Get all ThingsAddr nodes with bjd_code
    iot_nodes = db.query(
        "MATCH (i:ThingsAddr) WHERE i.longitude IS NOT NULL "
        "AND i.bjd_code IS NOT NULL AND i.bjd_code <> '' "
        "RETURN i.uid AS uid"
    )

    radius_deg = 200.0 / 111320.0  # ~0.0018 degrees
    batch_size = 10  # Small batches: bjd_code cross-join with 71K parcels is memory-heavy
    count = 0

    for i in range(0, len(iot_nodes), batch_size):
        batch_uids = [n["uid"] for n in iot_nodes[i:i + batch_size]]
        try:
            result = db.query(
                """
                UNWIND $uids AS iot_uid
                MATCH (i:ThingsAddr {uid: iot_uid})
                MATCH (p:Parcel)
                WHERE p.bjd_code IS NOT NULL
                      AND left(p.bjd_code, 10) = left(i.bjd_code, 10)
                      AND p.longitude IS NOT NULL
                      AND abs(p.longitude - i.longitude) < $radius
                      AND abs(p.latitude - i.latitude) < $radius
                WITH i, p,
                     (p.longitude - i.longitude) * 111320 * cos(radians(i.latitude)) AS dx,
                     (p.latitude - i.latitude) * 110540 AS dy
                WITH i, p, sqrt(dx*dx + dy*dy) AS dist_m
                WHERE dist_m < 200
                WITH i, p, dist_m
                ORDER BY i.uid, dist_m
                WITH i, collect(p)[0] AS nearest_parcel
                WHERE nearest_parcel IS NOT NULL
                CREATE (i)-[:ON_PARCEL]->(nearest_parcel)
                RETURN count(*) AS cnt
                """,
                uids=batch_uids,
                radius=radius_deg,
            )
            count += result[0]["cnt"] if result else 0
        except Exception as e:
            if "MemoryPool" in str(e):
                # If batch of 10 is too large, process one at a time
                for uid in batch_uids:
                    try:
                        result = db.query(
                            """
                            MATCH (i:ThingsAddr {uid: $uid})
                            MATCH (p:Parcel)
                            WHERE p.bjd_code IS NOT NULL
                                  AND left(p.bjd_code, 10) = left(i.bjd_code, 10)
                                  AND p.longitude IS NOT NULL
                                  AND abs(p.longitude - i.longitude) < $radius
                                  AND abs(p.latitude - i.latitude) < $radius
                            WITH i, p,
                                 (p.longitude - i.longitude) * 111320 * cos(radians(i.latitude)) AS dx,
                                 (p.latitude - i.latitude) * 110540 AS dy
                            With i, p, sqrt(dx*dx + dy*dy) AS dist_m
                            WHERE dist_m < 200
                            WITH i, p, dist_m
                            ORDER BY dist_m
                            WITH i, collect(p)[0] AS nearest_parcel
                            WHERE nearest_parcel IS NOT NULL
                            CREATE (i)-[:ON_PARCEL]->(nearest_parcel)
                            RETURN count(*) AS cnt
                            """,
                            uid=uid,
                            radius=radius_deg,
                        )
                        count += result[0]["cnt"] if result else 0
                    except Exception:
                        pass  # Skip if single item also fails
            else:
                raise

    print(f"[Enrichment] IoT ON_PARCEL: {count} relationships created (ThingsAddr → Parcel).")
