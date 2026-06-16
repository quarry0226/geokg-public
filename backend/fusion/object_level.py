"""
Object-Level Fusion Modeling.

Implements the paper's:
1. Spatial Quick Matching (Eqs. 2-5): rotation matrix to Euler angles,
   position mapping to virtual space
2. Spatial Topological Constraints (Eq. 6): nine-intersection model via Shapely DE-9IM
"""

import math
import json
import numpy as np
from shapely.geometry import Polygon, Point, LineString
from backend.db.neo4j_client import db


def rotation_matrix_to_euler(R: np.ndarray) -> dict:
    """
    Convert 3x3 rotation matrix to Euler angles (Eqs. 2-5 from paper).

    Returns dict with pitch, roll, yaw in degrees.
    """
    pitch = math.atan2(-R[2, 0], math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2))
    roll = math.atan2(R[2, 1], R[2, 2])
    yaw = math.atan2(R[1, 0], R[0, 0])
    return {
        "pitch": math.degrees(pitch),
        "roll": math.degrees(roll),
        "yaw": math.degrees(yaw),
    }


def euler_to_rotation_matrix(heading: float, pitch: float, roll: float) -> np.ndarray:
    """Convert Euler angles (degrees) to rotation matrix."""
    h, p, r = math.radians(heading), math.radians(pitch), math.radians(roll)

    Rz = np.array([[math.cos(h), -math.sin(h), 0],
                    [math.sin(h), math.cos(h), 0],
                    [0, 0, 1]])
    Ry = np.array([[math.cos(p), 0, math.sin(p)],
                    [0, 1, 0],
                    [-math.sin(p), 0, math.cos(p)]])
    Rx = np.array([[1, 0, 0],
                    [0, math.cos(r), -math.sin(r)],
                    [0, math.sin(r), math.cos(r)]])
    return Rz @ Ry @ Rx


def spatial_quick_match(entity_data: dict) -> dict:
    """
    Extract spatial location and pose parameters for embedding a model into virtual space.

    Input: entity data with longitude, latitude, altitude, heading, pitch, roll
    Output: Cesium-compatible positioning data
    """
    lon = entity_data.get("longitude", 0)
    lat = entity_data.get("latitude", 0)
    alt = entity_data.get("altitude", 0)
    heading = entity_data.get("heading", 0)
    pitch = entity_data.get("pitch", 0)
    roll = entity_data.get("roll", 0)

    return {
        "position": {"longitude": lon, "latitude": lat, "height": alt},
        "orientation": {
            "heading": heading,
            "pitch": pitch,
            "roll": roll,
        },
    }


def compute_nine_intersection(geom_a, geom_b) -> str:
    """
    Compute the DE-9IM (nine-intersection model) string between two geometries (Eq. 6).

    Uses Shapely's relate() which returns a 9-char DE-9IM string.
    """
    return geom_a.relate(geom_b)


def classify_topology(de9im: str) -> str:
    """Classify a DE-9IM string into a human-readable relationship."""
    if de9im[0] != "F":
        return "intersects"
    if de9im == "FF2FF1212":
        return "disjoint"
    # contains: interior of A contains all of B
    if de9im[0] == "T" and de9im[3] == "F" and de9im[6] == "F":
        return "contains"
    if de9im[0] == "F" and de9im[1] == "F" and de9im[3] != "F":
        return "within"
    if de9im[4] != "F":
        return "adjacent"
    return "other"


def verify_topological_constraints(entities: list[dict]) -> list[dict]:
    """
    Verify spatial topological constraints for all entity pairs that should have them.

    Returns a list of constraint verification results.
    """
    results = []
    geometries = {}

    for e in entities:
        uid = e["uid"]
        if "boundary" in e and e["boundary"]:
            coords = json.loads(e["boundary"])
            geometries[uid] = Polygon(coords)
        elif "coordinates" in e and e["coordinates"]:
            coords = json.loads(e["coordinates"]) if isinstance(e["coordinates"], str) else e["coordinates"]
            geometries[uid] = LineString(coords)
        elif "longitude" in e and "latitude" in e:
            geometries[uid] = Point(e["longitude"], e["latitude"])

    uids = list(geometries.keys())
    for i in range(len(uids)):
        for j in range(i + 1, len(uids)):
            a_uid, b_uid = uids[i], uids[j]
            de9im = compute_nine_intersection(geometries[a_uid], geometries[b_uid])
            topo = classify_topology(de9im)
            results.append({
                "entity_a": a_uid,
                "entity_b": b_uid,
                "de9im": de9im,
                "relationship": topo,
            })

    return results


def get_scene_objects_with_fusion() -> list[dict]:
    """
    Query all scene objects from the KG and apply object-level fusion
    (spatial matching) for Cesium rendering.

    Excludes ``Building`` (rendered via 3D Tiles / building_colors) and
    ``Parcel`` (loaded on demand via the parcel viewport endpoint). Including
    them here would force the database to materialize tens to hundreds of
    thousands of polygon properties in a single transaction — for Sejong
    (206,880 parcels) this exceeds Neo4j's default 716 MiB transaction memory
    pool and yields a ``MemoryPoolOutOfMemoryError`` 500 to the frontend.
    """
    query = """
    MATCH (n)
    WHERE NOT 'Building' IN labels(n)
      AND NOT 'Parcel'   IN labels(n)
      AND n.longitude IS NOT NULL AND n.latitude IS NOT NULL
    RETURN labels(n)[0] as label, properties(n) as props
    UNION
    MATCH (n)
    WHERE NOT 'Building' IN labels(n)
      AND NOT 'Parcel'   IN labels(n)
      AND n.coordinates IS NOT NULL AND n.longitude IS NULL
    RETURN labels(n)[0] as label, properties(n) as props
    """
    rows = db.query(query)
    result = []
    for row in rows:
        props = row["props"]
        label = row["label"]
        fusion_data = spatial_quick_match(props)
        result.append({
            "label": label,
            "uid": props.get("uid"),
            "properties": props,
            "fusion": fusion_data,
        })
    return result
