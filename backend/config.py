"""Configuration for GeoKG Digital Twin System.

Supports multi-region runs (yuseong, sejong, ...) by selecting REGION
via the ``GEOKG_REGION`` environment variable (default: ``yuseong``).
Original paths are preserved as the ``yuseong`` configuration.
"""

import os

REGION = os.environ.get("GEOKG_REGION", "yuseong").lower()

# Neo4j connection — override via environment variables in production.
# The default credentials match Neo4j Desktop's first-run defaults; the
# released code never embeds production secrets.
NEO4J_URI      = os.environ.get("GEOKG_NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.environ.get("GEOKG_NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.environ.get("GEOKG_NEO4J_PASSWORD", "neo4j")
NEO4J_DATABASE = os.environ.get("GEOKG_NEO4J_DB",
                                f"geokg" if REGION == "yuseong" else f"geokg-{REGION}")

# Cesium Ion access token — supply your own free-tier token via env var.
# https://ion.cesium.com/tokens
CESIUM_ION_TOKEN = os.environ.get("CESIUM_ION_TOKEN", "YOUR_CESIUM_ION_TOKEN")

# WebSocket settings
WS_HEARTBEAT_INTERVAL = 30  # seconds
DYNAMIC_UPDATE_INTERVAL = 2  # seconds between simulated sensor updates


# ───────────────────────────────────────────────────────────────────
# Per-region paths (multi-city portability)
# ───────────────────────────────────────────────────────────────────

REGION_CONFIG = {
    "yuseong": {
        "center_lon": 127.341,
        "center_lat": 36.369,
        "center_alt": 60.0,
        "shp_path":          "data/yuseong/buildings/buildings",            # building polygons
        # Yuseong cadastral: 2026-Q2 release (LSMD_CONT_LDREG_5174_30200_202604,
        # EPSG:5186) pre-filtered to COL_ADM_SE=30200. CRS auto-detected from
        # the accompanying .prj.
        "parcel_shp_path":   "data/yuseong/cadastral/LSMD_CONT_LDREG_5174_30200_202604",
        "parcel_col_adm_se_filter": None,
        "parcel_src_crs":    None,                                            # auto-detect from .prj
        "road_addr_path":    "data/yuseong/road_address/road_address_yuseong.csv",
        "iot_address_dir":   "data/yuseong/iot_address",
        "road_shp_path":     "data/yuseong/road_network/TL_SPRD_MANAGE",
        "intersection_shp_path": "data/yuseong/road_network/TL_SPRD_CRSRD",
        "tileset_dir":       "data/yuseong/tiles_3d",
        "buildings_cache":   "data/yuseong/buildings/buildings_cache.json",
    },
    "sejong": {
        "center_lon": 127.288,
        "center_lat": 36.480,
        "center_alt": 60.0,
        "shp_path":          "data/sejong/buildings/buildings",             # building polygons
        # Sejong cadastral is the 2026-Q2 release in EPSG:5186 (auto-detected).
        "parcel_shp_path":   "data/sejong/cadastral/LSMD_CONT_LDREG_36_202604",
        "parcel_col_adm_se_filter": None,
        "parcel_src_crs":    None,                                            # auto-detect from .prj
        "road_addr_path":    "data/sejong/road_address/road_address_sejong.csv",
        "iot_address_dir":   "data/sejong/iot_address",
        "road_shp_path":     "data/sejong/road_network/36110/TL_SPRD_MANAGE",
        "intersection_shp_path": None,                                       # No intersection SHP for Sejong
        "tileset_dir":       "data/sejong/tiles_3d",
        "buildings_cache":   "data/sejong/buildings/buildings_cache.json",
    },
}

# Backward-compatibility hook: extend this map if a deployment needs to support
# alternative directory layouts (e.g., a legacy snapshot). The release default
# leaves it empty so that all paths resolve through ``REGION_CONFIG`` above.
REGION_CONFIG_LEGACY = {}


def _resolve_path(path):
    """Return the path if it exists (or its .shp version), else None."""
    if path is None:
        return None
    # Direct existence
    if os.path.exists(path):
        return path
    # Auto-detect .shp basename completion
    if os.path.exists(path + ".shp"):
        return path
    # Excel/CSV
    for ext in (".csv", ".xlsx", ".json"):
        if os.path.exists(path + ext):
            return path
    return None


# Keys that are NOT filesystem paths and should pass through verbatim
_NON_PATH_KEYS = {"parcel_col_adm_se_filter", "parcel_src_crs"}


def _get_region_paths(region):
    """Return dict of resolved paths for the given region with legacy fallback."""
    cfg = dict(REGION_CONFIG.get(region, {}))
    legacy = REGION_CONFIG_LEGACY.get(region, {})

    resolved = {}
    for key, primary in cfg.items():
        if key in _NON_PATH_KEYS:
            resolved[key] = primary
            continue
        if isinstance(primary, (int, float)):
            resolved[key] = primary
            continue
        if primary is None:
            resolved[key] = None
            continue
        path = _resolve_path(primary)
        if path is None and key in legacy:
            path = _resolve_path(legacy[key])
        resolved[key] = path
    return resolved


_resolved = _get_region_paths(REGION)

# Public exports (legacy variable names preserved)
SAMPLE_CENTER_LON = _resolved.get("center_lon", 127.341)
SAMPLE_CENTER_LAT = _resolved.get("center_lat", 36.369)
SAMPLE_CENTER_ALT = _resolved.get("center_alt", 60.0)

TILESET_DIR              = _resolved.get("tileset_dir")              or "data/yuseong/tiles_3d"
SHP_PATH                 = _resolved.get("shp_path")                 or "data/yuseong/buildings/buildings"
PARCEL_SHP_PATH          = _resolved.get("parcel_shp_path")
PARCEL_COL_ADM_SE_FILTER = _resolved.get("parcel_col_adm_se_filter")
PARCEL_SRC_CRS           = _resolved.get("parcel_src_crs")
ROAD_ADDR_XLSX_PATH      = _resolved.get("road_addr_path")           or "data/yuseong/road_address/road_address_yuseong.csv"
IOT_ADDRESS_DATA_DIR     = _resolved.get("iot_address_dir")          or "data/yuseong/iot_address"
ROAD_SHP_PATH            = _resolved.get("road_shp_path")            or "data/yuseong/road_network/TL_SPRD_MANAGE"
INTERSECTION_SHP_PATH    = _resolved.get("intersection_shp_path")
BUILDINGS_CACHE_PATH     = _resolved.get("buildings_cache")

print(f"[config] REGION={REGION}, NEO4J_DB={NEO4J_DATABASE}, "
      f"SHP_PATH={SHP_PATH}, INTERSECTION={INTERSECTION_SHP_PATH or '(none)'}, "
      f"PARCEL={PARCEL_SHP_PATH or '(none)'}")
