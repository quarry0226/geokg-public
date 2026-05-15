"""Scene data API routes - provides fusion-applied scene data for Cesium rendering."""

import os
import json
import glob
from fastapi import APIRouter, HTTPException
from backend.fusion.object_level import get_scene_objects_with_fusion
from backend.fusion.attribute_level import apply_attribute_fusion
from backend.fusion.scene_level import apply_scene_fusion
from backend.config import SAMPLE_CENTER_LON, SAMPLE_CENTER_LAT
from backend.db.neo4j_client import db

router = APIRouter(prefix="/api/scene", tags=["Scene"])


@router.get("/visual_validation_samples")
def get_visual_validation_samples():
    """Serve the most recent stratified subset for FRONTS_ROAD visual validation."""
    pattern = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "experiments", "results", "visual_validation_subset_*.json",
    )
    files = sorted(glob.glob(pattern))
    if not files:
        raise HTTPException(404, "No visual_validation_subset found. Generate first.")
    with open(files[-1], "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/models")
def get_scene_models(exclude_label: str = None):
    """
    Get all scene models with full 3-level fusion applied:
    1. Object-level: spatial position and orientation
    2. Attribute-level: visual styling
    3. Scene-level: LOD and scale optimization

    Use exclude_label to skip a label (e.g. exclude_label=Building for 3D Tiles mode).
    """
    # Step 1: Object-level fusion (spatial matching)
    objects = get_scene_objects_with_fusion()

    # Filter out excluded label
    if exclude_label:
        objects = [o for o in objects if o["label"] != exclude_label]

    # Step 2: Attribute-level fusion (styling)
    for obj in objects:
        obj["style"] = apply_attribute_fusion(obj["label"], obj["properties"])

    # Step 3: Scene-level fusion (LOD, scale)
    fused = apply_scene_fusion(objects, SAMPLE_CENTER_LON, SAMPLE_CENTER_LAT)

    return fused


@router.get("/buildings")
def get_buildings():
    """Get building data with fusion applied."""
    rows = db.query(
        "MATCH (b:Building) RETURN properties(b) as props"
    )
    result = []
    for r in rows:
        props = r["props"]
        style = apply_attribute_fusion("Building", props)
        result.append({**props, "style": style})
    return result


@router.get("/roads")
def get_roads():
    """Get road data."""
    rows = db.query("MATCH (r:Road) RETURN properties(r) as props")
    result = []
    for r in rows:
        props = r["props"]
        style = apply_attribute_fusion("Road", props)
        result.append({**props, "style": style})
    return result


@router.get("/vehicles")
def get_vehicles():
    """Get current vehicle positions."""
    rows = db.query("MATCH (v:Vehicle) RETURN properties(v) as props")
    result = []
    for r in rows:
        props = r["props"]
        style = apply_attribute_fusion("Vehicle", props)
        result.append({**props, "style": style})
    return result


@router.get("/sensors")
def get_sensors():
    """Get sensor data with symbolization."""
    rows = db.query("MATCH (s:Sensor) RETURN properties(s) as props")
    result = []
    for r in rows:
        props = r["props"]
        style = apply_attribute_fusion("Sensor", props)
        result.append({**props, "style": style})
    return result


@router.get("/parking")
def get_parking():
    """Get parking lot status."""
    rows = db.query("MATCH (p:ParkingLot) RETURN properties(p) as props")
    result = []
    for r in rows:
        props = r["props"]
        style = apply_attribute_fusion("ParkingLot", props)
        result.append({**props, "style": style})
    return result


@router.get("/cameras")
def get_cameras():
    """Get camera data."""
    rows = db.query("MATCH (c:Camera) RETURN properties(c) as props")
    result = []
    for r in rows:
        props = r["props"]
        style = apply_attribute_fusion("Camera", props)
        result.append({**props, "style": style})
    return result


@router.get("/dashboard")
def get_dashboard():
    """Get aggregated dashboard data."""
    stats = {}

    # Parking summary
    parking = db.query("MATCH (p:ParkingLot) RETURN p.name as name, p.capacity as cap, p.occupied as occ")
    stats["parking"] = [{"name": p["name"], "capacity": p["cap"], "occupied": p["occ"],
                          "rate": round(p["occ"] / p["cap"] * 100, 1) if p["cap"] > 0 else 0} for p in parking]

    # Environment summary
    env = db.query("MATCH (s:Sensor) RETURN s.sensor_type as type, avg(s.value) as avg_val")
    stats["environment"] = {e["type"]: round(e["avg_val"], 1) for e in env}

    # Vehicle count
    vcount = db.query("MATCH (v:Vehicle) RETURN count(v) as c")
    stats["vehicle_count"] = vcount[0]["c"] if vcount else 0

    # Camera status
    cam_stats = db.query("MATCH (c:Camera) RETURN c.status as status, count(c) as cnt")
    stats["cameras"] = {c["status"]: c["cnt"] for c in cam_stats}

    # Total entities
    total = db.query("MATCH (n) RETURN count(n) as c")
    stats["total_entities"] = total[0]["c"] if total else 0

    return stats
