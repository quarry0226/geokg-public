"""
Attribute-Level Fusion Modeling.

Implements the paper's attribute fusion:
1. Static attribute constraints: hierarchical coloring, facility type styling
2. Dynamic attribute symbolization: sensor data binding, real-time status display
"""

from backend.db.neo4j_client import db


# Static attribute style mappings
BUILDING_TYPE_COLORS = {
    "office": "#4A90D9",       # blue
    "residential": "#7BC67E",  # green
    "commercial": "#E8A838",   # orange
    "industrial": "#C0C0C0",   # gray
    "hotel": "#C27BA0",        # pink/mauve
    "library": "#8E7CC3",      # purple
    "education": "#6E9FD9",    # light blue
    "medical": "#E85B5B",      # red
    "religious": "#B088C9",    # lavender
    "apartment": "#5BAF5B",    # dark green
}

ROAD_TYPE_STYLES = {
    "main": {"color": "#FFFFFF", "width": 8},
    "secondary": {"color": "#CCCCCC", "width": 5},
    "path": {"color": "#999999", "width": 2},
}

SENSOR_TYPE_ICONS = {
    "temperature": {"icon": "thermometer", "color": "#FF6B6B"},
    "humidity": {"icon": "droplet", "color": "#5BC0EB"},
    "aqi": {"icon": "wind", "color": "#9BC53D"},
    "noise": {"icon": "volume", "color": "#FDE74C"},
    "pm25": {"icon": "cloud", "color": "#B0BEC5"},
}

VEHICLE_TYPE_COLORS = {
    "car": "#3498DB",
    "truck": "#E74C3C",
    "bus": "#F39C12",
}

OCCUPANCY_COLORS = {
    "low": "#2ECC71",     # <50%
    "medium": "#F1C40F",  # 50-80%
    "high": "#E74C3C",    # >80%
}

EQUIPMENT_STATUS_COLORS = {
    "active": "#2ECC71",
    "online": "#2ECC71",
    "inactive": "#95A5A6",
    "offline": "#E74C3C",
    "maintenance": "#F39C12",
}


def get_building_style(building: dict) -> dict:
    """Apply static attribute coloring for a building based on its type and floors."""
    btype = building.get("building_type", "office")
    base_color = BUILDING_TYPE_COLORS.get(btype, "#808080")
    floors = building.get("floors", 1)
    alpha = min(1.0, 0.5 + floors * 0.05)
    return {
        "color": base_color,
        "alpha": round(alpha, 2),
        "outline": True,
        "outline_color": "#333333",
    }


def get_road_style(road: dict) -> dict:
    """Apply road type styling."""
    rtype = road.get("road_type", "secondary")
    return ROAD_TYPE_STYLES.get(rtype, ROAD_TYPE_STYLES["secondary"])


def get_sensor_symbol(sensor: dict) -> dict:
    """Symbolize a sensor for visual display."""
    stype = sensor.get("sensor_type", "temperature")
    symbol = SENSOR_TYPE_ICONS.get(stype, {"icon": "circle", "color": "#808080"})
    value = sensor.get("value", 0)
    unit = sensor.get("unit", "")
    return {
        **symbol,
        "value": value,
        "unit": unit,
        "label": f"{value}{unit}",
    }


def get_vehicle_style(vehicle: dict) -> dict:
    """Style a vehicle marker."""
    vtype = vehicle.get("vehicle_type", "car")
    return {
        "color": VEHICLE_TYPE_COLORS.get(vtype, "#3498DB"),
        "size": 10 if vtype == "car" else 14,
    }


def get_parking_style(parking_lot: dict) -> dict:
    """Compute occupancy-based styling for parking lot."""
    cap = parking_lot.get("capacity", 1)
    occ = parking_lot.get("occupied", 0)
    rate = occ / cap if cap > 0 else 0
    if rate < 0.5:
        level = "low"
    elif rate < 0.8:
        level = "medium"
    else:
        level = "high"
    return {
        "color": OCCUPANCY_COLORS[level],
        "occupancy_rate": round(rate * 100, 1),
        "level": level,
        "label": f"{occ}/{cap}",
    }


def get_camera_style(camera: dict) -> dict:
    """Style for camera visualization."""
    status = camera.get("status", "active")
    return {
        "color": EQUIPMENT_STATUS_COLORS.get(status, "#95A5A6"),
        "icon": "camera",
        "fov_visible": status == "active",
    }


def apply_attribute_fusion(label: str, properties: dict) -> dict:
    """
    Apply attribute-level fusion to produce visualization parameters.
    Maps entity properties to visual styling rules.
    """
    style_map = {
        "Building": get_building_style,
        "Road": get_road_style,
        "Sensor": get_sensor_symbol,
        "Vehicle": get_vehicle_style,
        "ParkingLot": get_parking_style,
        "Camera": get_camera_style,
    }
    fn = style_map.get(label)
    if fn:
        return fn(properties)
    return {"color": "#808080", "alpha": 0.8}


def get_all_attribute_styles() -> list[dict]:
    """Query all styled entities from KG with attribute fusion applied."""
    query = """
    MATCH (n)
    WHERE n.longitude IS NOT NULL AND n.latitude IS NOT NULL
    RETURN labels(n)[0] as label, properties(n) as props
    """
    rows = db.query(query)
    result = []
    for row in rows:
        style = apply_attribute_fusion(row["label"], row["props"])
        result.append({
            "uid": row["props"].get("uid"),
            "label": row["label"],
            "style": style,
        })
    return result
