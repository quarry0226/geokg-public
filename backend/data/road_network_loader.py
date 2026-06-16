"""Road-network Loader for TN_RODWAY (국가기본도 차도 노드/링크).

Produces two node types:

  - **RoadIntersection**: TN_RODWAY_NODE filtered to real intersections only
    (RDNODE_SE = RWN003 '3+ way' + RWN005 'grade-separated').
    The other codes (RWN001 endpoint, RWN002 pseudo-midpoint,
    RWN004 admin-boundary pseudo, RWN006 sheet-boundary dangle) are
    representational nodes of the topology rather than physical
    intersections and would inflate the count by ~3x with no benefit
    to safety analysis.

  - **AutoRoad** (road network link): TN_RODWAY_LINK with begin/end node
    NF_IDs preserved so the upstream graph engine can construct
    BEGINS_AT / ENDS_AT relationships into RoadIntersection.

Both inputs ship in EPSG:5179 (UTM-K, GRS80) — the same CRS as all KAIS
road-name address shapes, so coordinate handling is consistent.

CPG encoding varies across files (some UTF-8, some EUC-KR); the loader
reads the .cpg and selects the right codec.
"""
from __future__ import annotations

import json
import os
import shapefile
from pyproj import Transformer

_TRANSFORMER = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)


# RDNODE_SE codes
_INTERSECTION_CODES = {"RWN003", "RWN005"}  # 실 교차로 (3+way, grade-separated)
_NODE_LABELS = {
    "RWN001": "endpoint",
    "RWN002": "pseudo_mid",
    "RWN003": "intersection_3plus",
    "RWN004": "admin_boundary",
    "RWN005": "grade_separated",
    "RWN006": "sheet_boundary",
}

# ROAD_SE class codes
_ROAD_SE_LABEL = {
    "RDC001": "highway",          # 고속국도
    "RDC002": "national",         # 일반국도
    "RDC003": "special",          # 특별/광역시도
    "RDC005": "city",             # 시도
    "RDC006": "county",           # 군도
    "RDC007": "district",         # 구도
    "RDC008": "rural",            # 면도
    "RDC010": "rural_road",       # 농어촌도로
    "RDC011": "estate",           # 단지내도로
    "RDC014": "other",            # 기타
}

# USGSTT_SE usage codes
_USE_SE_LABEL = {
    "RUS001": "open",
    "RUS002": "construction",
    "RUS003": "open",
    "RUS004": "closed",
}


def _read_cpg(shp_path: str) -> str:
    """Read .cpg side-car; default to euc-kr when absent."""
    base = shp_path[:-4] if shp_path.endswith(".shp") else shp_path
    cpg_path = base + ".cpg"
    if not os.path.exists(cpg_path):
        return "euc-kr"
    try:
        with open(cpg_path, "r", encoding="ascii", errors="ignore") as f:
            enc = f.read().strip()
        return {"EUC-KR": "euc-kr", "UTF-8": "utf-8", "CP949": "cp949"}.get(
            enc.upper(), enc.lower() or "euc-kr"
        )
    except Exception:
        return "euc-kr"


def _safe_str(v, default: str = "") -> str:
    if v is None:
        return default
    return str(v).strip()


def _safe_int(v, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _safe_float(v, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ──────────────────────────────────────────────────────────────────────────
# Intersection loader
# ──────────────────────────────────────────────────────────────────────────

def load_intersections_tn(shp_path: str,
                          leglcd_prefix: str | None = None,
                          intersection_only: bool = True) -> list[dict]:
    """Load TN_RODWAY_NODE intersection nodes (point shape).

    Args:
        shp_path:         Path to TN_RODWAY_NODE_*.shp (extension optional).
        leglcd_prefix:    e.g., "30200" for Yuseong-gu. When set, only nodes
                          whose LEGLCD_SE1 starts with this prefix are kept.
        intersection_only: When True (default), keep only RWN003+RWN005
                          (real intersections). When False, also keep the
                          pseudo/endpoint nodes so the caller can build a
                          full topology.

    Returns:
        List of intersection dicts.
    """
    if shp_path.endswith(".shp"):
        shp_path = shp_path[:-4]
    enc = _read_cpg(shp_path + ".shp")

    sf = shapefile.Reader(shp_path, encoding=enc)
    fnames = [f[0] for f in sf.fields[1:]]
    idx = {n: i for i, n in enumerate(fnames)}

    out: list[dict] = []
    for i, sr in enumerate(sf.iterShapeRecords()):
        rec = sr.record
        pts = sr.shape.points
        if not pts:
            continue
        nf_id = _safe_str(rec[idx["NF_ID"]])
        node_se = _safe_str(rec[idx["RDNODE_SE"]])
        leglcd = _safe_str(rec[idx["LEGLCD_SE1"]])

        if leglcd_prefix and not leglcd.startswith(leglcd_prefix):
            continue
        if intersection_only and node_se not in _INTERSECTION_CODES:
            continue

        x, y = pts[0]
        lon, lat = _TRANSFORMER.transform(x, y)
        out.append({
            "uid": nf_id or f"int-{i:06d}",
            "nf_id": nf_id,
            "node_kind": _NODE_LABELS.get(node_se, node_se),
            "node_se": node_se,
            "leglcd": leglcd,
            "name": _safe_str(rec[idx.get("RDNODE_NM", -1)]) if "RDNODE_NM" in idx else "",
            "clink_count": _safe_int(rec[idx["CLINK_CO"]]) if "CLINK_CO" in idx else 0,
            "longitude": round(lon, 7),
            "latitude":  round(lat, 7),
        })
    sf.close()

    print(f"  [TN_RODWAY_NODE] loaded {len(out):,} intersections "
          f"(filter={leglcd_prefix or 'all'}, intersection_only={intersection_only})")
    return out


# ──────────────────────────────────────────────────────────────────────────
# AutoRoad (link) loader
# ──────────────────────────────────────────────────────────────────────────

def load_links_tn(shp_path: str,
                  leglcd_prefix: str | None = None,
                  exclude_classes: set[str] | None = None) -> list[dict]:
    """Load TN_RODWAY_LINK road segments (polyline shape).

    Args:
        shp_path:         Path to TN_RODWAY_LINK_*.shp
        leglcd_prefix:    e.g., "30200". When set, only links whose
                          LEGLCD_SE starts with this prefix.
        exclude_classes:  e.g., {'RDC011'} to drop "estate" segments.

    Returns:
        List of road-link dicts (one per polyline). Each dict carries
        ``bnode_nfid`` and ``enode_nfid`` so the orchestration step can
        wire BEGINS_AT / ENDS_AT into the RoadIntersection layer.
    """
    if shp_path.endswith(".shp"):
        shp_path = shp_path[:-4]
    enc = _read_cpg(shp_path + ".shp")

    sf = shapefile.Reader(shp_path, encoding=enc)
    fnames = [f[0] for f in sf.fields[1:]]
    idx = {n: i for i, n in enumerate(fnames)}

    out: list[dict] = []
    for i, sr in enumerate(sf.iterShapeRecords()):
        rec = sr.record
        pts = sr.shape.points
        if not pts:
            continue
        leglcd = _safe_str(rec[idx.get("LEGLCD_SE", -1)]) if "LEGLCD_SE" in idx else ""
        if leglcd_prefix and not leglcd.startswith(leglcd_prefix):
            continue

        road_se = _safe_str(rec[idx["ROAD_SE"]]) if "ROAD_SE" in idx else ""
        if exclude_classes and road_se in exclude_classes:
            continue

        # Convert polyline vertices to WGS84
        coords_wgs = []
        for x, y in pts:
            lon, lat = _TRANSFORMER.transform(x, y)
            coords_wgs.append([round(lon, 7), round(lat, 7)])

        # Use the link's midpoint as the representative anchor coordinate
        mid = pts[len(pts) // 2]
        anchor_lon, anchor_lat = _TRANSFORMER.transform(mid[0], mid[1])

        # Length (approximate, in projected 5179 metres)
        length_m = 0.0
        for j in range(1, len(pts)):
            dx = pts[j][0] - pts[j - 1][0]
            dy = pts[j][1] - pts[j - 1][1]
            length_m += (dx * dx + dy * dy) ** 0.5

        nf_id = _safe_str(rec[idx["NF_ID"]])
        out.append({
            "uid": nf_id or f"link-{i:06d}",
            "nf_id": nf_id,
            "bnode_nfid": _safe_str(rec[idx["BNODE_NFID"]]) if "BNODE_NFID" in idx else "",
            "enode_nfid": _safe_str(rec[idx["ENODE_NFID"]]) if "ENODE_NFID" in idx else "",
            "leglcd": leglcd,
            "road_no": _safe_str(rec[idx["ROAD_NO"]]) if "ROAD_NO" in idx else "",
            "road_name": _safe_str(rec[idx["ROAD_NM"]]) if "ROAD_NM" in idx else "",
            "road_class": _ROAD_SE_LABEL.get(road_se, road_se),
            "road_se":  road_se,
            "usage": _USE_SE_LABEL.get(
                _safe_str(rec[idx["USGSTT_SE"]]) if "USGSTT_SE" in idx else "", "open"
            ),
            "lane_count": _safe_int(rec[idx["CARTRK_CO"]]) if "CARTRK_CO" in idx else 0,
            "width_m":   _safe_float(rec[idx["ROAD_BT"]]) if "ROAD_BT" in idx else 0.0,
            "length_m":  round(length_m, 1),
            "longitude": round(anchor_lon, 7),
            "latitude":  round(anchor_lat, 7),
            "geometry":  json.dumps(coords_wgs),
        })
    sf.close()

    print(f"  [TN_RODWAY_LINK] loaded {len(out):,} links "
          f"(filter={leglcd_prefix or 'all'})")
    return out
