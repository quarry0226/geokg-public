"""GeoKG API routes - CRUD for knowledge graph nodes and relationships."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.db.neo4j_client import db
from backend.geokg.builder import GeoKGBuilder
from backend.geokg.ontology import ALL_LABELS

router = APIRouter(prefix="/api/geokg", tags=["GeoKG"])
builder = GeoKGBuilder()


class NodeCreate(BaseModel):
    label: str
    properties: dict


class RelationshipCreate(BaseModel):
    from_uid: str
    from_label: str
    rel_type: str
    to_uid: str
    to_label: str
    properties: dict = {}


@router.get("/stats")
def get_stats():
    """Get graph database statistics."""
    return db.stats()


@router.get("/nodes")
def get_nodes(label: str = None, limit: int = 200):
    """Get all nodes, optionally filtered by label."""
    if label:
        if label not in ALL_LABELS:
            raise HTTPException(400, f"Unknown label: {label}")
        cypher = f"MATCH (n:{label}) RETURN labels(n)[0] as label, properties(n) as props LIMIT $limit"
    else:
        cypher = "MATCH (n) RETURN labels(n)[0] as label, properties(n) as props LIMIT $limit"
    rows = db.query(cypher, limit=limit)
    return [{"label": r["label"], **r["props"]} for r in rows]


def _resolve_uid(identifier: str) -> str:
    """Resolve an identifier (uid or gsid) to uid."""
    if len(identifier) >= 20 and identifier.count('-') >= 5:
        rows = db.query(
            "MATCH (n {gsid: $gsid}) RETURN n.uid AS uid LIMIT 1",
            gsid=identifier.upper(),
        )
        if rows:
            return rows[0]["uid"]
    return identifier


@router.get("/nodes/{uid}")
def get_node(uid: str):
    """Get a single node by uid or gsid."""
    resolved = _resolve_uid(uid)
    rows = db.query("MATCH (n {uid: $uid}) RETURN labels(n)[0] as label, properties(n) as props", uid=resolved)
    if not rows:
        raise HTTPException(404, "Node not found")
    r = rows[0]
    return {"label": r["label"], **r["props"]}


@router.get("/nodes/{uid}/neighbors")
def get_neighbors(uid: str):
    """Get all neighbors of a node (1-hop)."""
    resolved = _resolve_uid(uid)
    rows = db.query(
        "MATCH (n {uid: $uid})-[r]-(m) RETURN labels(m)[0] as label, properties(m) as props, type(r) as rel_type",
        uid=resolved,
    )
    return [{"label": r["label"], "rel_type": r["rel_type"], **r["props"]} for r in rows]


@router.get("/nodes/{uid}/connections")
def get_connections(uid: str, max_per_type: int = 50, max_total: int = 150):
    """Get connected entities with coordinates for 3D visualization.
    Returns connections grouped by relationship type with distance info.
    All relationship types included for logical completeness."""
    resolved = _resolve_uid(uid)

    # Get source coordinates
    src_rows = db.query(
        "MATCH (n {uid: $uid}) RETURN n.longitude AS lon, n.latitude AS lat, "
        "COALESCE(n.height, 5) AS height, labels(n)[0] AS label",
        uid=resolved,
    )
    source = src_rows[0] if src_rows else None

    # Get ALL neighbors (bidirectional) with optional distance calculation
    rows = db.query(
        """
        MATCH (n {uid: $uid})-[r]-(m)
        WITH type(r) AS rel_type, labels(m)[0] AS label, m, n,
             CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END AS direction,
             CASE WHEN n.longitude IS NOT NULL AND n.latitude IS NOT NULL
                   AND m.longitude IS NOT NULL AND m.latitude IS NOT NULL
                  THEN toInteger(point.distance(
                    point({longitude: n.longitude, latitude: n.latitude}),
                    point({longitude: m.longitude, latitude: m.latitude})
                  ))
                  ELSE null END AS dist_m
        ORDER BY rel_type, dist_m
        RETURN rel_type, label,
               m.uid AS uid,
               COALESCE(m.name, m.iot_type_name, m.plate, m.sensor_type, m.jibun, m.uid) AS name,
               m.longitude AS longitude,
               m.latitude AS latitude,
               COALESCE(m.height, 5) AS height,
               direction,
               dist_m
        LIMIT 2000
        """,
        uid=resolved,
    )

    # Group by type, limit per type, ordered by distance (closest first)
    grouped = {}
    total_counts = {}
    for r in rows:
        rt = r["rel_type"]
        if rt not in total_counts:
            total_counts[rt] = 0
        total_counts[rt] += 1
        if rt not in grouped:
            grouped[rt] = []
        if len(grouped[rt]) < max_per_type:
            grouped[rt].append(r)

    # Flatten with total cap
    connections = []
    for items in grouped.values():
        connections.extend(items)
    # If over max_total, prioritize by closest distance
    if len(connections) > max_total:
        connections.sort(key=lambda c: c.get("dist_m") or 0)
        connections = connections[:max_total]

    return {
        "source": source,
        "connections": connections,
        "summary": total_counts,
    }


@router.post("/nodes")
def create_node(data: NodeCreate):
    """Create a new node."""
    if data.label not in ALL_LABELS:
        raise HTTPException(400, f"Unknown label: {data.label}")
    node = builder.add_entity(data.label, data.properties)
    return {"status": "created", "uid": data.properties.get("uid")}


@router.post("/relationships")
def create_relationship(data: RelationshipCreate):
    """Create a new relationship."""
    rel = builder.add_relationship(
        data.from_uid, data.from_label, data.rel_type,
        data.to_uid, data.to_label, **data.properties,
    )
    if rel is None:
        raise HTTPException(404, "One or both nodes not found")
    return {"status": "created"}


@router.get("/relationships")
def get_relationships(rel_type: str = None, limit: int = 500):
    """Get relationships, optionally filtered by type."""
    if rel_type:
        cypher = (
            "MATCH (a)-[r:" + rel_type + "]->(b) "
            "RETURN a.uid as from_uid, type(r) as rel_type, b.uid as to_uid, "
            "labels(a)[0] as from_label, labels(b)[0] as to_label "
            "LIMIT $limit"
        )
    else:
        cypher = (
            "MATCH (a)-[r]->(b) "
            "RETURN a.uid as from_uid, type(r) as rel_type, b.uid as to_uid, "
            "labels(a)[0] as from_label, labels(b)[0] as to_label "
            "LIMIT $limit"
        )
    return db.query(cypher, limit=limit)


@router.get("/graph")
def get_full_graph(limit: int = 300):
    """Get nodes and relationships for graph visualization.
    Samples evenly across relationship types so diverse connections appear."""
    import random
    total_links = limit * 2
    # Get all relationship types
    rel_types = db.query("MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")
    type_names = [r["t"] for r in rel_types]
    n_types = max(len(type_names), 1)
    per_type_target = max(5, total_links // n_types)

    # Fetch a pool from each type, then take exactly per_type_target
    all_rels = []
    for t in type_names:
        rows = db.query(
            "MATCH (a)-[r:" + t + "]->(b) "
            "WHERE a.uid IS NOT NULL AND b.uid IS NOT NULL "
            "RETURN a.uid AS source, type(r) AS type, b.uid AS target "
            "LIMIT $per",
            per=per_type_target * 2,
        )
        if rows:
            random.shuffle(rows)
            all_rels.extend(rows[:per_type_target])

    # Final shuffle (already balanced per type)
    random.shuffle(all_rels)
    rels = all_rels[:total_links]

    # Collect all unique node UIDs from relationships
    node_uids = set()
    for r in rels:
        node_uids.add(r["source"])
        node_uids.add(r["target"])
    uid_list = list(node_uids)
    # Fetch only the connected nodes
    if uid_list:
        nodes = db.query(
            "UNWIND $uids AS uid "
            "MATCH (n {uid: uid}) "
            "RETURN labels(n)[0] as label, properties(n) as props",
            uids=uid_list,
        )
    else:
        nodes = []
    return {
        "nodes": [{"id": n["props"]["uid"], "label": n["label"], **n["props"]} for n in nodes],
        "links": rels,
    }


@router.get("/spatial_relations")
def get_spatial_relations(limit: int = 5000):
    """Get spatial relationships with endpoint coordinates for 3D map visualization.
    Excludes CONTAINS (logical hierarchy) - only returns spatial relations like
    ADJACENT_TO, CONNECTED_TO, ON_ROAD, ALONG, HAS_STATE."""
    rows = db.query(
        """
        MATCH (a)-[r]->(b)
        WHERE type(r) <> 'CONTAINS'
          AND a.uid IS NOT NULL AND b.uid IS NOT NULL
        RETURN a.uid AS src, b.uid AS tgt, type(r) AS type,
               a.longitude AS src_lon, a.latitude AS src_lat, a.height AS src_h,
               b.longitude AS tgt_lon, b.latitude AS tgt_lat, b.height AS tgt_h
        LIMIT $limit
        """,
        limit=limit,
    )
    return {"count": len(rows), "relations": rows}
