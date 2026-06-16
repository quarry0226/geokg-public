"""Scene Data Seeding — region-agnostic with KAIS 2026/03/01 master data.

Ingests the current Korean Address Information System (KAIS) catalogue:

  - **TL_SGCO_RNADR_MST**          building footprint polygons
  - **TL_SPBD_ENTRC**              building entrance points (FRONTS_ROAD anchor)
  - **02. 총괄표제부 XLSX**          building registry attribute master (64 cols)
  - **LSMD_CONT_LDREG**            continuous cadastral SHP (PNU)
  - **TL_SPRD_MANAGE**             road-name admin road sections
  - **TN_RODWAY_NODE / TN_RODWAY_LINK**  national base-map road network
  - **TI_SPOT_***                  KAIS object-address shelter/transit/park

Emits canonical node/relationship dicts compatible with the rule engine
and Cypher builder. Region-agnostic: pass ``region='yuseong'`` or
``region='sejong'`` to the entry point.

Usage:
    from backend.data.seed_data import generate_scene_data
    payload = generate_scene_data(region="yuseong")
    payload = generate_scene_data(region="sejong")
"""
from __future__ import annotations

import json
import math
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from backend.data.parcel_loader import load_parcels
from backend.data.building_loader import load_buildings
from backend.data.entrance_loader import attach_entrances_to_buildings
from backend.data.road_network_loader import load_intersections_tn, load_links_tn
from backend.data.safety_facility_loader import (
    load_sig_polygon,
    load_shelters,
    load_transit,
    load_parks,
    load_monitor_facilities,
)
from backend.geokg.gsid import (
    generate_gsid_for_entity,
    resolve_subtype,
    reset_seq_counters,
)


# ──────────────────────────────────────────────────────────────────────────
# Region configurations
# ──────────────────────────────────────────────────────────────────────────

REGION_CONFIGS: dict[str, dict] = {
    "yuseong": {
        "label": "Yuseong-gu",
        "country_code": "KR",
        "sample_center_lon": 127.341,
        "sample_center_lat": 36.369,
        "area_km2": 176.5,
        "population_estimate": 360_000,
        "parcel_shp":     "data/yuseong/cadastral/LSMD_CONT_LDREG_5174_30200_202604",
        "parcel_filter":  "30200",            # COL_ADM_SE
        "parcel_src_crs": "EPSG:5186",
        "building_shp":   "data/yuseong/buildings/TL_SGCO_RNADR_MST",
        "building_filter": "30200",           # SIG_CD
        "summary_xlsx":   "data/yuseong/road_address/building_register_summary.xlsx",
        "entrance_shp":   "data/yuseong/buildings/TL_SPBD_ENTRC",
        "entrance_filter": "30200",           # SIG_CD
        "road_shp":       "data/yuseong/road_network/TL_SPRD_MANAGE",
        "road_filter":    "30200",            # SIG_CD
        "tn_node_shp":    "data/road_network_national/TN_RODWAY_NODE.shp",
        "tn_link_shp":    "data/road_network_national/TN_RODWAY_LINK.shp",
        "tn_leglcd":      "30200",
        "sig_polygon_shp": "data/yuseong/admin/TL_SCCO_SIG",
        "sig_cd":         "30200",
        "extracted_dir":  "data/yuseong",
        "core_zone_bounds": (127.310, 36.345, 127.375, 36.395),
        "peri_zone_bounds": (127.046, 36.264, 127.421, 36.440),
    },
    "sejong": {
        "label": "Sejong-si",
        "country_code": "KR",
        "sample_center_lon": 127.289,
        "sample_center_lat": 36.480,
        "area_km2": 465.0,
        "population_estimate": 390_000,
        "parcel_shp":     "data/sejong/cadastral/LSMD_CONT_LDREG_36_202604",
        "parcel_filter":  None,
        "parcel_src_crs": "EPSG:5186",
        "building_shp":   "data/sejong/buildings/TL_SGCO_RNADR_MST",
        "building_filter": None,
        "summary_xlsx":   "data/sejong/road_address/building_register_summary.xlsx",
        "entrance_shp":   "data/sejong/buildings/TL_SPBD_ENTRC",
        "entrance_filter": None,
        "road_shp":       "data/sejong/road_network/36110/TL_SPRD_MANAGE",
        "road_filter":    None,
        "tn_node_shp":    "data/road_network_national/TN_RODWAY_NODE.shp",
        "tn_link_shp":    "data/road_network_national/TN_RODWAY_LINK.shp",
        "tn_leglcd":      None,
        "sig_polygon_shp": None,
        "sig_cd":         None,
        "extracted_dir":  "data/sejong",
        "core_zone_bounds": (127.230, 36.420, 127.340, 36.530),
        "peri_zone_bounds": (127.000, 36.300, 127.500, 36.700),
    },
}


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _ll(lon, lat):
    return [round(lon, 6), round(lat, 6)]


def _assign_gsid(label, entity):
    entity["subtype"] = resolve_subtype(label, entity)
    entity["gsid"] = generate_gsid_for_entity(label, entity)


def _filter_entrances_by_sig_cd(shp_path: str, sig_cd: str) -> list[dict]:
    """Read TL_SPBD_ENTRC entries restricted to a specific SIG_CD, projecting to WGS84."""
    import shapefile
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)

    sf = shapefile.Reader(shp_path, encoding="euc-kr")
    fnames = [f[0] for f in sf.fields[1:]]
    i_sig = fnames.index("SIG_CD") if "SIG_CD" in fnames else None
    i_ent = fnames.index("ENT_MAN_NO") if "ENT_MAN_NO" in fnames else None
    i_eqb = fnames.index("EQB_MAN_SN") if "EQB_MAN_SN" in fnames else None
    i_es  = fnames.index("ENTRC_SE") if "ENTRC_SE" in fnames else None

    out: list[dict] = []
    for sr in sf.iterShapeRecords():
        if sig_cd and i_sig is not None and sr.record[i_sig] != sig_cd:
            continue
        pts = sr.shape.points
        if not pts:
            continue
        x, y = pts[0]
        lon, lat = transformer.transform(x, y)
        out.append({
            "ent_man_no": int(sr.record[i_ent]) if i_ent is not None and sr.record[i_ent] not in (None, "") else 0,
            "eqb_man_sn": str(sr.record[i_eqb]) if i_eqb is not None and sr.record[i_eqb] is not None else "",
            "entrc_se":   str(sr.record[i_es]) if i_es is not None and sr.record[i_es] is not None else "",
            "longitude":  round(lon, 6),
            "latitude":   round(lat, 6),
            "raw_x":      x,
            "raw_y":      y,
        })
    sf.close()
    return out


def _load_roads_from_shp(shp_path: str, sig_cd_filter: str | None) -> list[dict]:
    """Read TL_SPRD_MANAGE polylines into the canonical road dict shape."""
    import shapefile
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)

    sf = shapefile.Reader(shp_path, encoding="euc-kr")
    fnames = [f[0] for f in sf.fields[1:]]
    i_sig  = fnames.index("SIG_CD") if "SIG_CD" in fnames else None
    i_rdcd = fnames.index("RN_CD") if "RN_CD" in fnames else None
    i_rdnm = fnames.index("RN_KOR") if "RN_KOR" in fnames else (
        fnames.index("ROAD_NM") if "ROAD_NM" in fnames else None
    )
    i_wcnt = fnames.index("WUL_MAN_NO") if "WUL_MAN_NO" in fnames else None

    out: list[dict] = []
    for i, sr in enumerate(sf.iterShapeRecords()):
        if sig_cd_filter and i_sig is not None and sr.record[i_sig] != sig_cd_filter:
            continue
        pts = sr.shape.points
        if not pts:
            continue
        coords_wgs = [transformer.transform(x, y) for x, y in pts]
        coords_wgs = [(round(lon, 7), round(lat, 7)) for lon, lat in coords_wgs]
        rn_cd = str(sr.record[i_rdcd]) if i_rdcd is not None and sr.record[i_rdcd] is not None else f"road-{i:06d}"
        rn_nm = str(sr.record[i_rdnm]) if i_rdnm is not None and sr.record[i_rdnm] is not None else ""
        # midpoint as anchor
        mx, my = pts[len(pts) // 2]
        alon, alat = transformer.transform(mx, my)
        out.append({
            "uid":         f"road-{rn_cd}-{i:06d}",
            "name":        rn_nm or rn_cd,
            "road_name":   rn_nm,
            "rn_cd":       rn_cd,
            "longitude":   round(alon, 7),
            "latitude":    round(alat, 7),
            "coordinates": json.dumps([list(c) for c in coords_wgs]),
        })
    sf.close()
    return out


def _zone_for(lon: float, lat: float, core_b, peri_b):
    if core_b[0] <= lon <= core_b[2] and core_b[1] <= lat <= core_b[3]:
        return "zone-core"
    return "zone-peripheral"


def _haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


# ──────────────────────────────────────────────────────────────────────────
# Main entry
# ──────────────────────────────────────────────────────────────────────────

def generate_scene_data(region: str = "yuseong") -> dict:
    if region not in REGION_CONFIGS:
        raise ValueError(f"Unknown region: {region}. Choose from {list(REGION_CONFIGS)}")
    cfg = REGION_CONFIGS[region]
    print(f"\n[Seed v2] ═══ Region: {cfg['label']} ═══")

    reset_seq_counters()
    timing: dict[str, float] = {}
    t_total_start = time.time()
    cx, cy = cfg["sample_center_lon"], cfg["sample_center_lat"]
    core_b = cfg["core_zone_bounds"]
    peri_b = cfg["peri_zone_bounds"]

    # 1. Parcels
    print("[Seed v2] Loading parcels (LSMD_CONT_LDREG)...")
    t = time.time()
    parcels = load_parcels(
        cfg["parcel_shp"],
        include_all=True,
        col_adm_se_filter=cfg["parcel_filter"],
        src_crs=cfg["parcel_src_crs"],
    )
    timing["1_parcels"] = time.time() - t

    # 2. Buildings (with PNU spatial join + 총괄표제부 enrichment)
    print("[Seed v2] Loading buildings (TL_SGCO_RNADR_MST + 총괄표제부)...")
    t = time.time()
    summary_path = cfg["summary_xlsx"] if Path(cfg["summary_xlsx"]).exists() else None
    buildings = load_buildings(
        cfg["building_shp"],
        sig_cd_filter=cfg["building_filter"],
        summary_xlsx=summary_path,
        parcels=parcels,
        parcel_src_crs=cfg["parcel_src_crs"],
    )
    timing["2_buildings"] = time.time() - t

    # 3. Entrance attachment
    print("[Seed v2] Loading entrances (TL_SPBD_ENTRC) + spatial attach...")
    t = time.time()
    if cfg["entrance_filter"]:
        entrances = _filter_entrances_by_sig_cd(cfg["entrance_shp"], cfg["entrance_filter"])
    else:
        from backend.data.entrance_loader import load_entrances
        entrances = load_entrances(cfg["entrance_shp"], src_crs="EPSG:5179")
    n_matched = attach_entrances_to_buildings(buildings, entrances) if entrances else 0
    timing["3_entrances"] = time.time() - t

    # 4. Roads (TL_SPRD_MANAGE)
    print("[Seed v2] Loading roads (TL_SPRD_MANAGE)...")
    t = time.time()
    roads = _load_roads_from_shp(cfg["road_shp"], cfg["road_filter"])
    timing["4_roads"] = time.time() - t
    print(f"  [Roads] loaded {len(roads):,}")

    # 5. Intersections + auto-road links (TN_RODWAY)
    print("[Seed v2] Loading intersections + auto-road links (TN_RODWAY_NODE/LINK)...")
    t = time.time()
    intersections = load_intersections_tn(
        cfg["tn_node_shp"],
        leglcd_prefix=cfg["tn_leglcd"],
        intersection_only=True,
    )
    auto_links = load_links_tn(cfg["tn_link_shp"], leglcd_prefix=cfg["tn_leglcd"])
    timing["5_road_network"] = time.time() - t

    # 6. Safety facilities (TI_SPOT_* via spatial filter where applicable)
    print("[Seed v2] Loading safety facilities (TI_SPOT_*)...")
    t = time.time()
    ring = None
    if cfg["sig_polygon_shp"] and cfg["sig_cd"]:
        ring = load_sig_polygon(cfg["sig_polygon_shp"], cfg["sig_cd"])
    shelters = load_shelters(cfg["extracted_dir"], filter_ring_5179=ring)
    transit_pts = load_transit(cfg["extracted_dir"], filter_ring_5179=ring)
    park_pts = load_parks(cfg["extracted_dir"], filter_ring_5179=ring)
    monitor_pts = load_monitor_facilities(cfg["extracted_dir"], filter_ring_5179=ring)
    timing["6_safety"] = time.time() - t

    # 7. Zones
    zones = [
        {
            "uid": "zone-core",
            "name": f"{cfg['label']} 중심부",
            "zone_type": "core",
            "boundary": json.dumps([
                [core_b[0], core_b[1]], [core_b[2], core_b[1]],
                [core_b[2], core_b[3]], [core_b[0], core_b[3]],
                [core_b[0], core_b[1]],
            ]),
        },
        {
            "uid": "zone-peripheral",
            "name": f"{cfg['label']} 외곽",
            "zone_type": "peripheral",
            "boundary": json.dumps([
                [peri_b[0], peri_b[1]], [peri_b[2], peri_b[1]],
                [peri_b[2], peri_b[3]], [peri_b[0], peri_b[3]],
                [peri_b[0], peri_b[1]],
            ]),
        },
    ]

    # 8. ThingsAddr (safety facility) → canonical
    iot_addresses: list[dict] = []
    for s in shelters:
        iot_addresses.append({
            "uid":         f"thingsaddr-shelter-{s['uid']}",
            "iot_type":    "SHELTER",
            "shelter_kind": s["object_kind"],
            "name":        s.get("name") or s["object_kind"],
            "longitude":   s["longitude"],
            "latitude":    s["latitude"],
            "category":    "shelter",
        })
    for t_ in transit_pts:
        iot_addresses.append({
            "uid":         f"thingsaddr-{t_['object_kind']}-{t_['uid']}",
            "iot_type":    t_["object_kind"].upper(),
            "name":        t_.get("name") or t_["object_kind"],
            "longitude":   t_["longitude"],
            "latitude":    t_["latitude"],
            "category":    "transit",
        })
    for p_ in park_pts:
        iot_addresses.append({
            "uid":         f"thingsaddr-park-{p_['uid']}",
            "iot_type":    "PARK",
            "park_kind":   p_["object_kind"],
            "name":        p_.get("name") or p_["object_kind"],
            "longitude":   p_["longitude"],
            "latitude":    p_["latitude"],
            "category":    "park",
        })
    for m_ in monitor_pts:
        iot_addresses.append({
            "uid":         f"thingsaddr-monitor-{m_['uid']}",
            "iot_type":    m_["object_kind"].upper(),
            "name":        m_.get("name") or m_["object_kind"],
            "longitude":   m_["longitude"],
            "latitude":    m_["latitude"],
            "category":    "monitor",
        })

    # 9. Simulation data (vehicles, sensors, cameras, trees, facilities, parking)
    print("[Seed v2] Generating simulation data...")
    t = time.time()
    random.seed(42)
    plates_prefix = ["대전", "세종", "충남", "충북"]
    v_types = ["car", "car", "car", "car", "bus", "truck"]
    vehicles = []
    if roads:
        for i in range(50):
            road = random.choice(roads)
            coords = json.loads(road["coordinates"]) if isinstance(road["coordinates"], str) else road["coordinates"]
            if len(coords) < 2:
                continue
            idx = random.randint(0, len(coords) - 2)
            tt = random.random()
            lon = coords[idx][0] + tt * (coords[idx+1][0] - coords[idx][0]) + random.uniform(-0.001, 0.001)
            lat = coords[idx][1] + tt * (coords[idx+1][1] - coords[idx][1]) + random.uniform(-0.001, 0.001)
            vehicles.append({
                "uid": f"veh-{i+1:03d}",
                "plate": f"{random.choice(plates_prefix)} {random.randint(10,99)}가 {random.randint(1000,9999)}",
                "vehicle_type": random.choice(v_types),
                "longitude": round(lon, 6),
                "latitude": round(lat, 6),
                "heading": random.uniform(0, 360),
                "speed": random.uniform(0, 80),
            })

    now = datetime.utcnow().isoformat() + "Z"
    sensor_configs = [
        ("temperature", "°C", 5, 35),
        ("humidity", "%", 30, 90),
        ("aqi", "AQI", 20, 120),
        ("noise", "dB", 40, 85),
        ("pm25", "μg/m³", 10, 80),
    ]
    sensors = []
    for i, (stype, unit, vmin, vmax) in enumerate(sensor_configs):
        for j in range(4):
            sensors.append({
                "uid": f"sen-{stype}-{j+1}",
                "sensor_type": stype,
                "longitude": round(cx + random.uniform(-0.03, 0.03), 6),
                "latitude":  round(cy + random.uniform(-0.03, 0.03), 6),
                "value": round(random.uniform(vmin, vmax), 1),
                "unit": unit,
                "last_updated": now,
            })

    cameras = []
    for i in range(15):
        cameras.append({
            "uid": f"cam-{i+1:03d}",
            "camera_type": random.choice(["traffic", "security", "cctv"]),
            "longitude": round(cx + random.uniform(-0.025, 0.025), 6),
            "latitude":  round(cy + random.uniform(-0.025, 0.025), 6),
            "status": "active",
            "resolution": random.choice(["1080p", "4K"]),
        })

    tree_species = ["소나무", "은행나무", "벚나무", "느티나무", "메타세쿼이아",
                    "단풍나무", "잣나무", "참나무"]
    trees = []
    for i in range(50):
        trees.append({
            "uid": f"tree-{i+1:03d}",
            "tree_species": random.choice(tree_species),
            "height_m": round(random.uniform(3, 20), 1),
            "longitude": round(cx + random.uniform(-0.03, 0.03), 6),
            "latitude":  round(cy + random.uniform(-0.03, 0.03), 6),
        })

    facility_types = ["lamp", "bench", "sign", "gate", "hydrant", "bus_stop", "trash_bin"]
    facilities = []
    for i in range(25):
        facilities.append({
            "uid": f"fac-{i+1:03d}",
            "facility_type": random.choice(facility_types),
            "longitude": round(cx + random.uniform(-0.025, 0.025), 6),
            "latitude":  round(cy + random.uniform(-0.025, 0.025), 6),
            "status": "active",
        })

    parking_lots = [
        {"uid": "park-A", "name": f"{cfg['label']} 주차장 A", "capacity": 200,
         "occupied": random.randint(50, 180),
         "longitude": cx, "latitude": cy},
        {"uid": "park-B", "name": f"{cfg['label']} 주차장 B", "capacity": 500,
         "occupied": random.randint(100, 450),
         "longitude": cx + 0.015, "latitude": cy + 0.012},
        {"uid": "park-C", "name": f"{cfg['label']} 주차장 C", "capacity": 300,
         "occupied": random.randint(80, 280),
         "longitude": cx - 0.012, "latitude": cy - 0.008},
    ]
    parking_spaces = []
    for pl in parking_lots:
        for s in range(min(10, pl["capacity"])):
            parking_spaces.append({
                "uid": f"{pl['uid']}-s{s+1:02d}",
                "space_number": s+1,
                "is_occupied": s < pl["occupied"] // (pl["capacity"] // 10),
                "longitude": round(pl["longitude"] + random.uniform(-0.001, 0.001), 6),
                "latitude":  round(pl["latitude"] + random.uniform(-0.001, 0.001), 6),
            })

    states = [
        {"uid": "ts-main", "_label": "TrafficState", "road_uid": roads[0]["uid"] if roads else "road-default",
         "congestion_level": random.uniform(0.3, 0.8),
         "avg_speed_kmh": random.uniform(20, 60),
         "vehicle_count": random.randint(30, 120), "timestamp": now},
        {"uid": "es-1", "_label": "EnvironmentState",
         "temperature_c": round(random.uniform(5, 30), 1),
         "humidity_pct": round(random.uniform(40, 80), 1),
         "aqi": random.randint(30, 80), "timestamp": now},
    ]
    for pl in parking_lots:
        states.append({"uid": f"os-{pl['uid']}", "_label": "OccupancyState",
                       "occupied": pl["occupied"], "capacity": pl["capacity"],
                       "rate": round(pl["occupied"] / pl["capacity"], 2),
                       "timestamp": now})
    states.append({"uid": "ws-1", "_label": "WeatherState",
                   "condition": "맑음", "temperature_c": 22.5, "wind_speed_ms": 3.2,
                   "humidity_pct": 55, "timestamp": now})

    timing["9_simulation"] = time.time() - t

    # 10. Relationships
    print("[Seed v2] Building relationships...")
    t = time.time()
    relationships: list[dict] = []

    def _rel(from_uid, from_label, to_uid, to_label, rel_type):
        return {"from": from_uid, "to": to_uid, "type": rel_type,
                "fl": from_label, "tl": to_label}

    # Zone hierarchy
    relationships.append(_rel("zone-peripheral", "Zone", "zone-core", "Zone", "CONTAINS"))

    # Road states
    if roads:
        relationships.append(_rel(roads[0]["uid"], "Road", "ts-main", "TrafficState", "HAS_STATE"))

    # Parking states
    for pl in parking_lots:
        relationships.append(_rel(pl["uid"], "ParkingLot", f"os-{pl['uid']}", "OccupancyState", "HAS_STATE"))

    # Nearest-road helper for entities
    def _road_midpoint(rd):
        coords = rd.get("coordinates", "[]")
        if isinstance(coords, str):
            coords = json.loads(coords)
        if not coords or not isinstance(coords, list):
            return None, None
        mid = len(coords) // 2
        try:
            return coords[mid][0], coords[mid][1]
        except (IndexError, TypeError):
            return None, None

    road_anchors = [(rd["uid"], *(_road_midpoint(rd))) for rd in roads]
    road_anchors = [(u, lon, lat) for (u, lon, lat) in road_anchors if lon is not None]

    def _nearest_road(lon, lat):
        if not road_anchors:
            return None
        best, best_d = None, float("inf")
        for uid, rlon, rlat in road_anchors:
            dx = (rlon - lon) * 111320 * math.cos(math.radians(lat))
            dy = (rlat - lat) * 110540
            d = dx*dx + dy*dy
            if d < best_d:
                best_d, best = d, uid
        return best

    for v in vehicles:
        relationships.append(_rel("zone-core", "Zone", v["uid"], "Vehicle", "CONTAINS"))
        nr = _nearest_road(v["longitude"], v["latitude"])
        if nr:
            relationships.append(_rel(v["uid"], "Vehicle", nr, "Road", "ON_ROAD"))

    for s in sensors:
        relationships.append(_rel("zone-core", "Zone", s["uid"], "Sensor", "CONTAINS"))
        nr = _nearest_road(s["longitude"], s["latitude"])
        if nr:
            relationships.append(_rel(s["uid"], "Sensor", nr, "Road", "ALONG"))

    for c in cameras:
        relationships.append(_rel("zone-core", "Zone", c["uid"], "Camera", "CONTAINS"))
        nr = _nearest_road(c["longitude"], c["latitude"])
        if nr:
            relationships.append(_rel(c["uid"], "Camera", nr, "Road", "ALONG"))

    for tr in trees:
        relationships.append(_rel("zone-core", "Zone", tr["uid"], "Tree", "CONTAINS"))

    for f in facilities:
        relationships.append(_rel("zone-core", "Zone", f["uid"], "Facility", "CONTAINS"))
        nr = _nearest_road(f["longitude"], f["latitude"])
        if nr:
            relationships.append(_rel(f["uid"], "Facility", nr, "Road", "ALONG"))

    for pl in parking_lots:
        relationships.append(_rel("zone-core", "Zone", pl["uid"], "ParkingLot", "CONTAINS"))

    for ps in parking_spaces:
        pl_uid = ps["uid"].rsplit("-s", 1)[0]
        relationships.append(_rel(pl_uid, "ParkingLot", ps["uid"], "ParkingSpace", "CONTAINS"))

    # Buildings → zones
    for b in buildings:
        zone_uid = _zone_for(b["longitude"], b["latitude"], core_b, peri_b)
        relationships.append(_rel(zone_uid, "Zone", b["uid"], "Building", "CONTAINS"))

    # IoT → zones
    for iot in iot_addresses:
        zone_uid = _zone_for(iot["longitude"], iot["latitude"], core_b, peri_b)
        relationships.append(_rel(zone_uid, "Zone", iot["uid"], "ThingsAddr", "CONTAINS"))

    # Transit IoT → nearest road
    for iot in iot_addresses:
        if iot.get("category") == "transit":
            nr = _nearest_road(iot["longitude"], iot["latitude"])
            if nr:
                relationships.append(_rel(iot["uid"], "ThingsAddr", nr, "Road", "ON_ROAD"))

    timing["10_seed_relationships"] = time.time() - t

    # 11. GSID assignment
    print("[Seed v2] Assigning GSIDs...")
    t = time.time()
    for z in zones:
        _assign_gsid("Zone", z)
    for rd in roads:
        _assign_gsid("Road", rd)
    for v in vehicles:
        _assign_gsid("Vehicle", v)
    for s in sensors:
        _assign_gsid("Sensor", s)
    for c in cameras:
        _assign_gsid("Camera", c)
    for tr in trees:
        _assign_gsid("Tree", tr)
    for f in facilities:
        _assign_gsid("Facility", f)
    for pl in parking_lots:
        _assign_gsid("ParkingLot", pl)
    for ps in parking_spaces:
        _assign_gsid("ParkingSpace", ps)
    for inter in intersections:
        _assign_gsid("RoadIntersection", inter)
    for lk in auto_links:
        _assign_gsid("AutoRoadLink", lk)
    for i, b in enumerate(buildings):
        _assign_gsid("Building", b)
        if (i + 1) % 10000 == 0:
            print(f"  [GSID] {i+1:,} / {len(buildings):,} buildings...")
    for i, p in enumerate(parcels):
        _assign_gsid("Parcel", p)
        if (i + 1) % 50000 == 0:
            print(f"  [GSID] {i+1:,} / {len(parcels):,} parcels...")
    for iot in iot_addresses:
        _assign_gsid("ThingsAddr", iot)

    timing["11_gsid"] = time.time() - t

    timing["total"] = time.time() - t_total_start
    print(f"\n[Seed v2] ═══ Timing Report ({cfg['label']}) ═══")
    for k, v in sorted(timing.items()):
        print(f"  {k:<30s} {v:>10.2f}s")
    print(f"  {'─' * 42}")
    print(f"  TOTAL: {timing['total']:.2f}s\n")

    summary_matched = sum(1 for b in buildings if b.get("gross_floor_area", 0) > 0)
    summary_match_rate = round(summary_matched / max(len(buildings), 1) * 100, 1)

    return {
        "region": region,
        "region_label": cfg["label"],
        "area_km2": cfg["area_km2"],
        "population_estimate": cfg["population_estimate"],
        "zones": zones,
        "buildings": buildings,
        "parcels": parcels,
        "roads": roads,
        "intersections": intersections,
        "auto_road_links": auto_links,
        "parking_lots": parking_lots,
        "parking_spaces": parking_spaces,
        "vehicles": vehicles,
        "sensors": sensors,
        "cameras": cameras,
        "trees": trees,
        "facilities": facilities,
        "iot_addresses": iot_addresses,
        "states": states,
        "relationships": relationships,
        "_timing": timing,
        "_metrics": {
            "n_buildings": len(buildings),
            "n_parcels": len(parcels),
            "n_roads": len(roads),
            "n_intersections": len(intersections),
            "n_auto_road_links": len(auto_links),
            "n_shelters": sum(1 for i in iot_addresses if i.get("category") == "shelter"),
            "n_transit": sum(1 for i in iot_addresses if i.get("category") == "transit"),
            "n_parks": sum(1 for i in iot_addresses if i.get("category") == "park"),
            "n_monitor": sum(1 for i in iot_addresses if i.get("category") == "monitor"),
            "entrance_match_count": n_matched,
            "entrance_match_rate_pct": round(n_matched / max(len(buildings), 1) * 100, 1),
            "summary_attribute_match_count": summary_matched,
            "summary_attribute_match_rate_pct": summary_match_rate,
        },
    }


if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "yuseong"
    payload = generate_scene_data(region=region)
    print(json.dumps(payload["_metrics"], indent=2, ensure_ascii=False))
