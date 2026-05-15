"""
GeoKG Ontology Schema Definition.

Based on the paper's three-domain model:
  Ont = <CO, EO, SO | Relations | Properties>
  - EO (Entity/Object Domain): physical urban elements
  - SO (State Domain): immediate conditions of objects
  - CO (Change Domain): dynamics and transitions
"""

# =============================================================================
# Node Labels (by Domain)
# =============================================================================

ENTITY_DOMAIN_LABELS = [
    "Building",
    "Road",
    "Vehicle",
    "ParkingLot",
    "ParkingSpace",
    "Sensor",
    "Camera",
    "Tree",
    "Facility",
    "Pedestrian",
    "Zone",         # spatial zone / area
    "Parcel",              # cadastral parcel (필지)
    "ThingsAddr",          # IoT address entities (사물주소)
    "RoadIntersection",    # road intersection (교차로)
]

STATE_DOMAIN_LABELS = [
    "TrafficState",
    "EnvironmentState",
    "OccupancyState",
    "EquipmentState",
    "WeatherState",
]

CHANGE_DOMAIN_LABELS = [
    "PositionChange",
    "StatusChange",
    "EnvironmentChange",
    "MaintenanceEvent",
]

ALL_LABELS = ENTITY_DOMAIN_LABELS + STATE_DOMAIN_LABELS + CHANGE_DOMAIN_LABELS

# =============================================================================
# Relationship Types
# =============================================================================

RELATIONSHIPS = {
    # Spatial relationships
    "CONTAINS":      "Entity contains another entity (e.g., Zone -> Building)",
    "ADJACENT_TO":   "Entity is adjacent to another entity",
    "LOCATED_IN":    "Entity is located in a zone/area",
    "CONNECTED_TO":  "Road/path connection between entities",
    "NEAR":          "Entity is near another entity (proximity-based)",
    "ON_ROAD":       "Vehicle is on/traveling along a road",
    "ALONG":         "Facility/Camera is positioned along a road",

    # Road-address relationships (도로명주소 기반)
    "ON_STREET":     "Building is on a named street (도로명 매칭)",
    "FRONTS_ROAD":   "Building fronts the nearest road (최근접 도로)",

    # Road network relationships (도로 네트워크)
    "MEETS_AT":      "Road passes through an intersection (도로-교차로 연결)",

    # Attribute-based relationships (속성 기반)
    "SAME_DONG":     "Buildings in the same administrative district (같은 행정동)",
    "SAME_USAGE":    "Buildings of the same usage type in proximity (같은 용도 클러스터)",

    # Parcel relationships (필지 기반)
    "ON_PARCEL":     "Building sits on a cadastral parcel (필지 위 건물)",

    # IoT Address relationships (사물주소 기반)
    "NEAR_BUILDING": "Entity is near a building (100m 이내)",

    # Accessibility relationships (접근성)
    "NEAREST_SHELTER": "Building to nearest shelter (대피시설 500m)",
    "ACCESSIBLE_BY_TRANSIT": "Building to nearest transit stop (대중교통 300m)",
    "NEAR_PARK": "Building to nearest park/playground (공원 500m)",

    # Facility-to-facility relationships (시설 간)
    "NEAR_FACILITY": "IoT facility near another IoT facility (200m)",
    "ALONG_ROAD": "Non-building entity along nearest road (도로변 시설)",
    "COLOCATED": "Entities sharing the same parcel (같은 필지 공존)",
    "SAME_ROAD_SEGMENT": "Entities within 100m along the same road (같은 도로 구간, 선형참조)",

    # Monitoring relationships
    "MONITORS":      "Sensor/Camera monitors an entity or area",
    "SERVES":        "Facility serves a zone or building",

    # State relationships
    "HAS_STATE":     "Entity has a current state",
    "PREVIOUS_STATE": "State transition chain",

    # Change relationships
    "TRIGGERS_CHANGE": "An event triggers a change",
    "CAUSED_BY":     "A state change is caused by an event",
    "AFFECTS":       "A change affects an entity",

    # Data-model relationships
    "HAS_MODEL":     "Entity has an associated 3D model",
    "HAS_SENSOR_DATA": "Entity has associated sensor data",
}

# =============================================================================
# Node Property Schemas
# =============================================================================

NODE_PROPERTIES = {
    "Building": {
        "uid": "str (unique id)",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. BL.com.office)",
        "name": "str",
        "building_type": "str (office|residential|commercial|industrial)",
        "floors": "int",
        "height": "float (meters)",
        "width": "float (meters)",
        "depth": "float (meters)",
        "longitude": "float",
        "latitude": "float",
        "altitude": "float",
        "heading": "float (degrees, yaw)",
        "pitch": "float (degrees)",
        "roll": "float (degrees)",
        "color": "str (hex color)",
        "lod_level": "int (0-3)",
        "importance": "float (0-1)",
        # Road linear reference (도로 선형참조)
        "road_name": "str (도로명, e.g. 유성대로)",
        "building_main": "int (건물본번, encodes distance from road start)",
        "building_sub": "int (건물부번)",
        "road_position": "float (normalized 0.0~1.0 position along road)",
        "road_side": "str (left|right, from odd/even building number)",
        "road_distance_m": "float (estimated meters from road start)",
    },
    "Road": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. RC.urb.major)",
        "name": "str",
        "road_type": "str (main|secondary|path)",
        "width": "float (meters)",
        "coordinates": "str (JSON array of [lon, lat] pairs)",
        "lanes": "int",
        # Linear reference profile (선형참조 프로파일)
        "profile_slope_lon": "float (lon = slope * building_main + intercept)",
        "profile_intercept_lon": "float",
        "profile_slope_lat": "float (lat = slope * building_main + intercept)",
        "profile_intercept_lat": "float",
        "profile_r_squared": "float (R² goodness of fit)",
        "profile_meters_per_unit": "float (meters per building number unit)",
        "profile_building_count": "int (data points used for fitting)",
        "profile_min_building_num": "int",
        "profile_max_building_num": "int",
    },
    "RoadIntersection": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype)",
        "name": "str (한글 교차로명)",
        "eng_name": "str (영문 교차로명)",
        "intersection_type": "str (삼거리|사거리|오거리|기타)",
        "type_code": "str (CRSRD_TYCD from SHP)",
        "longitude": "float",
        "latitude": "float",
    },
    "Vehicle": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. OM.vehicle.car)",
        "plate": "str",
        "vehicle_type": "str (car|truck|bus)",
        "longitude": "float",
        "latitude": "float",
        "heading": "float",
        "speed": "float (km/h)",
    },
    "ParkingLot": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. PK.garage)",
        "name": "str",
        "capacity": "int",
        "occupied": "int",
        "longitude": "float",
        "latitude": "float",
    },
    "ParkingSpace": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. PK.space)",
        "space_number": "int",
        "is_occupied": "bool",
        "longitude": "float",
        "latitude": "float",
    },
    "Sensor": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. OF.util.sensor.temp)",
        "sensor_type": "str (temperature|humidity|aqi|noise)",
        "longitude": "float",
        "latitude": "float",
        "value": "float",
        "unit": "str",
        "last_updated": "str (ISO datetime)",
    },
    "Camera": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. OF.safety.cctv.dome)",
        "name": "str",
        "camera_type": "str (dome|bullet)",
        "longitude": "float",
        "latitude": "float",
        "altitude": "float",
        "fov": "float (field of view degrees)",
        "status": "str (active|inactive)",
    },
    "Tree": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. NT.tree.deciduous.plane)",
        "species": "str",
        "height": "float",
        "longitude": "float",
        "latitude": "float",
    },
    "Facility": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. OF.light.led.pole)",
        "name": "str",
        "facility_type": "str (lamp|bench|sign|gate)",
        "longitude": "float",
        "latitude": "float",
    },
    "Zone": {
        "uid": "str",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. ZU.urban.core)",
        "name": "str",
        "zone_type": "str (core|peripheral|underground)",
        "boundary": "str (JSON GeoJSON polygon)",
    },
    "Parcel": {
        "uid": "str (unique id, format: parcel-{PNU})",
        "gsid": "str (GSID v7.0 identifier, optional)",
        "subtype": "str (hierarchical subtype, e.g. LP.land.building_site)",
        "pnu": "str (19-digit Parcel Unique Number)",
        "jibun": "str (lot number, e.g. 197-26)",
        "bjd_code": "str (법정동코드, 10 digits)",
        "lot_main": "int (본번)",
        "lot_sub": "int (부번)",
        "land_category": "str (building_site|paddy|field|forest|road|...)",
        "land_cat_code": "str (Korean single char: 대|답|전|임|...)",
        "is_mountain": "bool (산 여부)",
        "longitude": "float",
        "latitude": "float",
        "area_sq_m": "float (area in square meters)",
        "boundary": "str (JSON polygon in WGS84)",
    },
    "ThingsAddr": {
        "uid": "str (unique id, format: iot-{OBJ_ID})",
        "gsid": "str (GSID v7.0 identifier)",
        "subtype": "str (hierarchical subtype, e.g. IO.transport.bus_stop)",
        "iot_type": "str (BUSST|CoolingCen|CHPARK|CivilDefense|ChPlayground|EQOUT|TAXIST|SCPARK|SLEEPRA)",
        "iot_type_name": "str (Korean name, e.g. 버스정류장)",
        "name": "str (facility name)",
        "bjd_name": "str (법정동명)",
        "bjd_code": "str (법정동코드, 10 digits)",
        "road_name": "str (도로명)",
        "road_address": "str (full road address)",
        "longitude": "float",
        "latitude": "float",
        "geocode_method": "str (road_address|bjd_code|fallback)",
    },
    "TrafficState": {
        "uid": "str",
        "status": "str (smooth|congested|blocked)",
        "vehicle_count": "int",
        "avg_speed": "float",
        "timestamp": "str",
    },
    "EnvironmentState": {
        "uid": "str",
        "temperature": "float",
        "humidity": "float",
        "aqi": "int",
        "noise_level": "float",
        "timestamp": "str",
    },
    "OccupancyState": {
        "uid": "str",
        "total_spaces": "int",
        "occupied_spaces": "int",
        "occupancy_rate": "float",
        "timestamp": "str",
    },
    "EquipmentState": {
        "uid": "str",
        "status": "str (online|offline|maintenance)",
        "battery_level": "float",
        "timestamp": "str",
    },
}

# =============================================================================
# Neo4j Constraints & Indexes
# =============================================================================

SCHEMA_CONSTRAINTS = []
for label in ALL_LABELS:
    SCHEMA_CONSTRAINTS.append(
        f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.uid IS UNIQUE"
    )

SCHEMA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS FOR (n:Building) ON (n.name)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Building) ON (n.pnu)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Road) ON (n.name)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Vehicle) ON (n.plate)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Sensor) ON (n.sensor_type)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Zone) ON (n.zone_type)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Parcel) ON (n.pnu)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Parcel) ON (n.land_category)",
    "CREATE INDEX IF NOT EXISTS FOR (n:ThingsAddr) ON (n.iot_type)",
    "CREATE INDEX IF NOT EXISTS FOR (n:ThingsAddr) ON (n.bjd_code)",
    # Road linear reference indexes
    "CREATE INDEX IF NOT EXISTS FOR (n:Building) ON (n.road_name)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Building) ON (n.building_main)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Road) ON (n.profile_r_squared)",
]

# GSID-specific indexes (gsid is nullable, so we use indexes not unique constraints)
for _label in ENTITY_DOMAIN_LABELS:
    SCHEMA_INDEXES.append(f"CREATE INDEX IF NOT EXISTS FOR (n:{_label}) ON (n.gsid)")
    SCHEMA_INDEXES.append(f"CREATE INDEX IF NOT EXISTS FOR (n:{_label}) ON (n.subtype)")
