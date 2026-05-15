"""Safety facility loader (TI_SPOT_* — 사물주소도형).

Replaces the previously synthesized Shelter / Transit / Park / Playground
nodes with authoritative point/line/area data from the KAIS object-address
(사물주소) catalogue. All inputs ship as EPSG:5179 (UTM-K, GRS80).

Shelter coverage is built by union over four KAIS object classes plus a
fifth fallback dataset:

  - **CivilDefense** — 민방위 대피시설
  - **EQOUT**        — 지진 옥외 대피장소
  - **EWRC**         — 재난 대피시설 (Emergency Welfare/Refuge)
  - **CoolingCen**   — 무더위 쉼터
  - **EMERWAT**      — 비상 급수시설 (fallback / amenity)

Each shelter entry carries an ``object_kind`` distinguishing the five
categories so the safety pipeline can weight them separately if needed.

Transit coverage combines BUSST + TAXIST point shapes. The Park category
combines TL_SPOT_PARK (large parks) with TI_SPOT_SCPARK / ChPlayground /
RIVERPK (small parks and playgrounds).

For Daejeon a Yuseong-gu polygon (TL_SCCO_SIG SIG_CD=30200) is used as a
spatial filter, because the TI_SPOT_* point shapes carry no
administrative-code field. Sejong is a single-sigungu city so no spatial
filter is needed.
"""
from __future__ import annotations

import os
import shapefile
from pyproj import Transformer


_TRANSFORMER = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)


# ──────────────────────────────────────────────────────────────────────────
# Common geometry helpers
# ──────────────────────────────────────────────────────────────────────────

def _polygon_from_shape(shape) -> list:
    """Outer ring as (x, y) list in source CRS."""
    parts = list(shape.parts) if shape.parts else [0]
    end = parts[1] if len(parts) > 1 else len(shape.points)
    return [(p[0], p[1]) for p in shape.points[parts[0]:end]]


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ──────────────────────────────────────────────────────────────────────────
# Yuseong polygon helper
# ──────────────────────────────────────────────────────────────────────────

def load_sig_polygon(sig_shp_path: str, sig_cd: str) -> list | None:
    """Return outer ring (in EPSG:5179 coordinates) for the given SIG_CD."""
    if not os.path.exists(sig_shp_path + ".shp"):
        return None
    sf = shapefile.Reader(sig_shp_path, encoding="euc-kr")
    fnames = [f[0] for f in sf.fields[1:]]
    i_sig = fnames.index("SIG_CD")
    for rec, sh in zip(sf.records(), sf.shapes()):
        if rec[i_sig] == sig_cd:
            ring = _polygon_from_shape(sh)
            sf.close()
            return ring
    sf.close()
    return None


# ──────────────────────────────────────────────────────────────────────────
# Generic POINT-shape loader with spatial filter
# ──────────────────────────────────────────────────────────────────────────

def load_points(shp_path: str,
                category: str,
                kind_label: str,
                filter_ring_5179: list | None = None,
                limit: int | None = None) -> list[dict]:
    """Read a TI_SPOT_*_POINT or TL_SPOT_* point/polygon SHP.

    For polygon inputs (e.g. TL_SPOT_PARK is a polygon shape), the
    centroid is emitted as the point coordinate.

    Args:
        shp_path:         Path to .shp file (extension optional)
        category:         Output category bucket (e.g. 'shelter', 'transit',
                          'park', 'playground').
        kind_label:       Specific sub-type label (e.g. 'civil_defense',
                          'cooling_center', 'bus_stop').
        filter_ring_5179: Polygon ring in source CRS for spatial filter.
                          None disables filtering.
        limit:            Optional max number of items to load.

    Returns:
        List of facility dicts.
    """
    if shp_path.endswith(".shp"):
        shp_path = shp_path[:-4]
    if not os.path.exists(shp_path + ".shp"):
        return []
    enc = "euc-kr"
    cpg = shp_path + ".cpg"
    if os.path.exists(cpg):
        try:
            with open(cpg, "r", encoding="ascii", errors="ignore") as f:
                v = f.read().strip()
                if v:
                    enc = {"EUC-KR": "euc-kr", "UTF-8": "utf-8"}.get(v.upper(), v.lower())
        except Exception:
            pass

    sf = shapefile.Reader(shp_path, encoding=enc)
    fnames = [f[0] for f in sf.fields[1:]]
    has_obj_mng = "OBJ_MNG_NO" in fnames
    has_sig_cd  = "SIG_CD" in fnames
    has_park_nm = "KOR_PAR_NM" in fnames

    out: list[dict] = []
    for i, sr in enumerate(sf.iterShapeRecords()):
        rec = sr.record
        pts = sr.shape.points
        if not pts:
            continue
        # Polygon shape: use centroid
        if len(pts) > 1:
            sx = sum(p[0] for p in pts) / len(pts)
            sy = sum(p[1] for p in pts) / len(pts)
        else:
            sx, sy = pts[0]

        if filter_ring_5179 and not _point_in_ring(sx, sy, filter_ring_5179):
            continue

        lon, lat = _TRANSFORMER.transform(sx, sy)
        obj_mng = str(rec[fnames.index("OBJ_MNG_NO")]) if has_obj_mng else ""
        name = str(rec[fnames.index("KOR_PAR_NM")]) if has_park_nm else ""

        out.append({
            "uid": obj_mng or f"{kind_label}-{i:06d}",
            "category": category,
            "object_kind": kind_label,
            "name": name,
            "longitude": round(lon, 7),
            "latitude":  round(lat, 7),
            "sig_cd": str(rec[fnames.index("SIG_CD")]) if has_sig_cd else "",
        })
        if limit and len(out) >= limit:
            break
    sf.close()
    return out


# ──────────────────────────────────────────────────────────────────────────
# Pre-packaged loaders for each safety category
# ──────────────────────────────────────────────────────────────────────────

def load_shelters(extracted_dir: str,
                  filter_ring_5179: list | None = None) -> list[dict]:
    """Union of five shelter-related TI_SPOT_* point shapes."""
    sources = [
        ("TI_SPOT_CivilDefense_POINT", "civil_defense"),
        ("TI_SPOT_EQOUT_POINT",        "earthquake_outdoor"),
        ("TI_SPOT_EWRC_POINT",         "disaster_refuge"),
        ("TI_SPOT_CoolingCen_POINT",   "cooling_center"),
        ("TI_SPOT_EMERWAT_POINT",      "emergency_water"),
    ]
    out: list[dict] = []
    for fn, kind in sources:
        path = os.path.join(extracted_dir, fn)
        if not os.path.exists(path + ".shp"):
            print(f"  [Shelter] missing {fn}, skipped")
            continue
        chunk = load_points(path, "shelter", kind, filter_ring_5179)
        out.extend(chunk)
        print(f"  [Shelter] {kind:20s} → {len(chunk):5,} (spatial filter applied)")
    print(f"  [Shelter] TOTAL: {len(out):,}")
    return out


def load_transit(extracted_dir: str,
                 filter_ring_5179: list | None = None) -> list[dict]:
    """Bus stops + taxi stands."""
    sources = [
        ("TI_SPOT_BUSST_POINT",  "bus_stop"),
        ("TI_SPOT_TAXIST_POINT", "taxi_stand"),
    ]
    out: list[dict] = []
    for fn, kind in sources:
        path = os.path.join(extracted_dir, fn)
        chunk = load_points(path, "transit", kind, filter_ring_5179) if os.path.exists(path + ".shp") else []
        out.extend(chunk)
        print(f"  [Transit] {kind:12s} → {len(chunk):5,}")
    print(f"  [Transit] TOTAL: {len(out):,}")
    return out


def load_parks(extracted_dir: str,
               filter_ring_5179: list | None = None) -> list[dict]:
    """Large parks + small parks + playgrounds + riverside parks."""
    sources = [
        ("TL_SPOT_PARK",                "tl_park"),       # polygon → centroid
        ("TI_SPOT_SCPARK_POINT",        "small_park"),
        ("TI_SPOT_ChPlayground_POINT",  "playground"),
        ("TI_SPOT_RIVERPK_POINT",       "riverside_park"),
    ]
    out: list[dict] = []
    for fn, kind in sources:
        path = os.path.join(extracted_dir, fn)
        if not os.path.exists(path + ".shp"):
            continue
        chunk = load_points(path, "park", kind, filter_ring_5179)
        out.extend(chunk)
        print(f"  [Park] {kind:15s} → {len(chunk):5,}")
    print(f"  [Park] TOTAL: {len(out):,}")
    return out


def load_monitor_facilities(extracted_dir: str,
                            filter_ring_5179: list | None = None) -> list[dict]:
    """Fire hydrants + emergency water + lifesaving stations.

    Note: actual CCTV/sensor data is not in this dataset. This loader
    returns physical 'monitoring/emergency response' assets that are
    proxies for monitor-style safety coverage.
    """
    sources = [
        ("TI_SPOT_FireHydr_POINT",   "fire_hydrant"),
        ("TI_SPOT_LIFESAV_POINT",    "lifesaving"),
        ("TI_SPOT_PublicTel_POINT",  "public_phone"),
    ]
    out: list[dict] = []
    for fn, kind in sources:
        path = os.path.join(extracted_dir, fn)
        if not os.path.exists(path + ".shp"):
            continue
        chunk = load_points(path, "monitor", kind, filter_ring_5179)
        out.extend(chunk)
        print(f"  [Monitor] {kind:13s} → {len(chunk):5,}")
    print(f"  [Monitor] TOTAL: {len(out):,}")
    return out
