"""
Scene-Level Fusion Modeling.

Implements the paper's:
1. Spatially Optimal Scale (Eq. 7): SF = (Utarget / Usource) * (1 + ATF_model_type)
2. Stereoscopic Scene Constraints / LOD Optimization (Eqs. 8-9):
   LODi = f(Imp_metric), Imp_metric = Σ(ωj * mj)
"""

from backend.db.neo4j_client import db


# Adjustment factors per model type (ATF_model_type)
ATF_FACTORS = {
    "Building": 0.0,
    "Road": 0.0,
    "Tree": -0.1,
    "Vehicle": 0.05,
    "Facility": -0.05,
    "ParkingLot": 0.0,
    "Camera": 0.1,
    "Sensor": 0.15,
}

# LOD constraint weights (ωj)
LOD_WEIGHTS = {
    "zone_importance": 0.3,    # core area vs peripheral
    "visibility": 0.25,        # above-ground vs underground
    "interaction_frequency": 0.2,
    "detail_requirement": 0.15,
    "distance_to_center": 0.1,
}

# LOD level thresholds
LOD_THRESHOLDS = [
    (0.75, 0),   # LOD0: highest detail (Imp >= 0.75)
    (0.50, 1),   # LOD1
    (0.25, 2),   # LOD2
    (0.0, 3),    # LOD3: lowest detail
]


def compute_scale_factor(source_unit: str, target_unit: str, model_type: str) -> float:
    """
    Compute spatially optimal scale factor (Eq. 7).

    SF = (Utarget / Usource) * (1 + ATF_model_type)
    """
    unit_conversion = {
        "meters": 1.0,
        "kilometers": 1000.0,
        "feet": 0.3048,
        "inches": 0.0254,
        "centimeters": 0.01,
    }
    u_source = unit_conversion.get(source_unit, 1.0)
    u_target = unit_conversion.get(target_unit, 1.0)
    atf = ATF_FACTORS.get(model_type, 0.0)
    return (u_target / u_source) * (1 + atf)


def compute_importance_metric(entity: dict, center_lon: float, center_lat: float) -> float:
    """
    Compute importance metric (Eq. 9): Imp_metric = Σ(ωj * mj)

    Constraint metrics (mj):
    - zone_importance: 1.0 for core, 0.5 for peripheral, 0.2 for underground
    - visibility: 1.0 for above-ground outdoor, 0.5 for indoor, 0.2 for underground
    - interaction_frequency: based on entity type
    - detail_requirement: based on entity type
    - distance_to_center: inverse of normalized distance
    """
    # Zone importance
    zone_type = entity.get("zone_type", "core")
    zone_scores = {"core": 1.0, "peripheral": 0.5, "underground": 0.2}
    m_zone = zone_scores.get(zone_type, 0.5)

    # Visibility
    m_visibility = 1.0  # default: above-ground

    # Interaction frequency by entity type
    label = entity.get("_label", "")
    interaction_scores = {
        "Building": 0.8, "Vehicle": 0.9, "ParkingLot": 0.7,
        "Sensor": 0.6, "Camera": 0.7, "Road": 0.5,
        "Tree": 0.3, "Facility": 0.4,
    }
    m_interaction = interaction_scores.get(label, 0.5)

    # Detail requirement
    detail_scores = {
        "Building": 0.9, "Vehicle": 0.7, "ParkingLot": 0.6,
        "Sensor": 0.4, "Camera": 0.5, "Road": 0.6,
        "Tree": 0.2, "Facility": 0.3,
    }
    m_detail = detail_scores.get(label, 0.5)

    # Distance to center (normalized)
    lon = entity.get("longitude", center_lon)
    lat = entity.get("latitude", center_lat)
    dist = ((lon - center_lon) ** 2 + (lat - center_lat) ** 2) ** 0.5
    max_dist = 0.05  # ~5km at this latitude (covers Yuseong-gu area)
    m_distance = max(0, 1.0 - dist / max_dist)

    imp = (
        LOD_WEIGHTS["zone_importance"] * m_zone
        + LOD_WEIGHTS["visibility"] * m_visibility
        + LOD_WEIGHTS["interaction_frequency"] * m_interaction
        + LOD_WEIGHTS["detail_requirement"] * m_detail
        + LOD_WEIGHTS["distance_to_center"] * m_distance
    )
    return round(imp, 3)


def determine_lod_level(imp_metric: float) -> int:
    """Map importance metric to LOD level (Eq. 8): LODi = f(Imp_metric)."""
    for threshold, level in LOD_THRESHOLDS:
        if imp_metric >= threshold:
            return level
    return 3


def apply_scene_fusion(entities: list[dict], center_lon: float, center_lat: float) -> list[dict]:
    """
    Apply scene-level fusion to all entities.

    Returns entities augmented with scale_factor, importance, and lod_level.
    """
    result = []
    for e in entities:
        label = e.get("label", "")
        props = e.get("properties", {})
        props["_label"] = label

        sf = compute_scale_factor("meters", "meters", label)
        imp = compute_importance_metric(props, center_lon, center_lat)
        lod = determine_lod_level(imp)

        result.append({
            **e,
            "scene_fusion": {
                "scale_factor": round(sf, 4),
                "importance": imp,
                "lod_level": lod,
            },
        })
    return result
