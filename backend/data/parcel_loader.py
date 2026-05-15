"""
Parcel (지적도) & Road Address (도로명주소) Loader.

1. Reads cadastral SHP → creates Parcel nodes with polygon boundaries.
   CRS is auto-detected from the accompanying .prj file (supports EPSG:5174 and
   EPSG:5186; older releases use 5174, newer releases switched to 5186).
2. Reads road-address Excel → enriches Building nodes with additional address info.
3. Links Buildings to Parcels via PNU matching (ON_PARCEL relationship).

Optimization: Only loads building-related parcels (대지, 공장, 학교, 종교, 주차장, 창고 등)
to keep memory and DB size manageable (~25K instead of 71K).
"""

import json
import os
import shapefile
from pyproj import Transformer

# Cached transformers per source CRS (avoid repeatedly building expensive objects)
_TRANSFORMER_CACHE = {}

# Default fallback when no .prj file is present (legacy behaviour preserved)
_DEFAULT_SRC_CRS = "EPSG:5174"


def _get_transformer(src_crs):
    """Return cached pyproj transformer src_crs -> EPSG:4326 (always_xy=True)."""
    src_crs = src_crs or _DEFAULT_SRC_CRS
    t = _TRANSFORMER_CACHE.get(src_crs)
    if t is None:
        t = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        _TRANSFORMER_CACHE[src_crs] = t
    return t


def _detect_crs_from_prj(shp_path):
    """Detect EPSG code from accompanying .prj file. Returns 'EPSG:NNNN' or default."""
    prj_path = shp_path + ".prj" if not shp_path.endswith(".prj") else shp_path
    if not os.path.exists(prj_path):
        return _DEFAULT_SRC_CRS
    try:
        with open(prj_path, "r", encoding="utf-8", errors="ignore") as f:
            wkt = f.read()
    except Exception:
        return _DEFAULT_SRC_CRS
    # Heuristic match — Korean PRJ files reliably embed AUTHORITY["EPSG", "NNNN"]
    # for the projected CS (Korea 2000 5186) and old ESRI WKT for 5174.
    if 'AUTHORITY["EPSG","5186"]' in wkt or "AUTHORITY[\"EPSG\",\"5186\"]" in wkt:
        return "EPSG:5186"
    if 'AUTHORITY["EPSG","5179"]' in wkt:
        return "EPSG:5179"
    if 'AUTHORITY["EPSG","5174"]' in wkt:
        return "EPSG:5174"
    # ESRI WKT for 5174: "Korean_1985_Modified_Korea_Central_Belt"
    if "Korean_1985" in wkt or "Korean Datum 1985" in wkt or "Bessel_1841" in wkt:
        return "EPSG:5174"
    if "Korea 2000" in wkt or "Korea_2000" in wkt:
        return "EPSG:5186"
    return _DEFAULT_SRC_CRS

# Land category code mapping (지목 코드).
# Korean cadastral SHPs encode 지목 (land-use category) in the trailing
# character of the JIBUN attribute. Most Yuseong releases use the canonical
# 1-character abbreviation table below, but the Sejong 2026-Q2 release also
# emits the *trailing character of the full word* — e.g. "하천" → '천', not
# '하' — for several categories. We therefore include both encodings to
# avoid spurious "unknown" classification. Without these aliases roughly
# 5% of Sejong parcels (10K) end up as 'unknown' and the cadastral overlay
# shows large grey polygons on top of correctly-categorised neighbours.
LAND_CATEGORY = {
    "대": "building_site",    # 대지
    "답": "paddy",            # 답 (논)
    "전": "field",            # 전 (밭)
    "임": "forest",           # 임야
    "도": "road",             # 도로
    "하": "river",            # 하천
    "천": "river",            # 하천 (full-word last char)
    "구": "ditch",            # 구거
    "제": "embankment",       # 제방
    "잡": "miscellaneous",    # 잡종지
    "공": "factory",          # 공장용지
    "학": "school",           # 학교용지
    "주": "parking",          # 주차장
    "장": "parking",          # 주차장 (full-word last char)
    "차": "parking",          # 주차장 alt
    "종": "religious",        # 종교용지
    "체": "sports",           # 체육용지
    "유": "recreation",       # 유원지
    "목": "pasture",          # 목장용지
    "과": "orchard",          # 과수원
    "묘": "cemetery",         # 묘지
    "광": "mineral",          # 광천지
    "염": "salt",             # 염전
    "양": "aquaculture",      # 양어장
    "수": "waterway",         # 수도용지
    "창": "warehouse",        # 창고용지
    "원": "park",             # 공원
    "사": "historic",         # 사적지
    "철": "rail",             # 철도용지
    "가": "gas_station",      # 주유소용지 / 가스충전소
}

# Categories to include (building-related only for performance)
INCLUDE_CATEGORIES = {
    "대", "공", "학", "종", "주", "창", "체", "유", "잡",
}


def _safe_str(val, default=""):
    if val is None:
        return default
    return str(val).strip()


def _polygon_to_wgs84(shape, transformer, max_points=50):
    """Extract outer ring from polygon shape (projected CRS) → WGS84 [lon, lat] pairs.
    Simplify by sampling if too many points."""
    points = shape.points
    parts = list(shape.parts) if shape.parts else [0]
    end = parts[1] if len(parts) > 1 else len(points)
    outer_ring = points[parts[0]:end]

    # Simplify: if polygon has too many points, subsample
    n = len(outer_ring)
    if n > max_points:
        step = n / max_points
        indices = [int(i * step) for i in range(max_points)]
        outer_ring = [outer_ring[idx] for idx in indices]

    coords = []
    for x, y in outer_ring:
        lon, lat = transformer.transform(x, y)
        coords.append([round(lon, 6), round(lat, 6)])
    # Remove duplicate closing point
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def _centroid_wgs84(points, transformer):
    """Compute centroid from projected coordinates, return (lon, lat) in WGS84."""
    if not points:
        return None, None
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    n = len(points)
    lon, lat = transformer.transform(sx / n, sy / n)
    return round(lon, 6), round(lat, 6)


def _compute_area_sq_m(points):
    """Shoelace formula for area in projected meter coordinates."""
    n = len(points)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return abs(area) / 2.0


def load_parcels(shp_path: str, include_all: bool = False,
                 col_adm_se_filter: str = None, src_crs: str = None):
    """
    Load cadastral parcels from SHP.

    Args:
        shp_path: Path to .shp file (without extension)
        include_all: If True, load all parcels. If False, only building-related.
        col_adm_se_filter: If set (e.g. "30200"), only include parcels whose
            COL_ADM_SE attribute equals this value. Useful when reading the
            province-wide SHP that covers all 5 Daejeon districts.
        src_crs: Override source CRS (e.g. "EPSG:5186"). When None, the CRS is
            auto-detected from the accompanying .prj file (defaults to 5174).

    Returns:
        List of parcel dicts ready for Neo4j insertion.
    """
    if src_crs is None:
        src_crs = _detect_crs_from_prj(shp_path)
    transformer = _get_transformer(src_crs)
    print(f"  [Parcel] CRS: {src_crs} -> EPSG:4326")

    sf = shapefile.Reader(shp_path, encoding="cp949")
    field_names = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    has_col_adm_se = "COL_ADM_SE" in field_names
    parcels = []
    skipped = 0
    skipped_other_district = 0

    for i, sr in enumerate(sf.iterShapeRecords()):
        rec = sr.record
        shape = sr.shape

        pnu = _safe_str(rec["PNU"])
        if not pnu:
            continue

        # Filter by district code if requested
        if col_adm_se_filter and has_col_adm_se:
            adm = _safe_str(rec["COL_ADM_SE"])
            if adm != col_adm_se_filter:
                skipped_other_district += 1
                continue

        jibun = _safe_str(rec["JIBUN"])
        bchk = _safe_str(rec["BCHK"])

        # Extract land category from JIBUN suffix (e.g. "197-26대" → "대")
        land_cat_code = ""
        if jibun:
            last_char = jibun[-1] if jibun else ""
            if last_char in LAND_CATEGORY:
                land_cat_code = last_char
                jibun_num = jibun[:-1]
            else:
                jibun_num = jibun
        else:
            jibun_num = ""

        # Filter: only include building-related categories for performance
        if not include_all and land_cat_code not in INCLUDE_CATEGORIES:
            skipped += 1
            continue

        land_category = LAND_CATEGORY.get(land_cat_code, "unknown")

        # Parse PNU components — 19-digit Korean parcel ID layout:
        #   [0:10]  BJD code (법정동 10-digit)
        #   [10]    mountain flag ('1' = regular, '2' = mountain/산)
        #   [11:15] lot main number (지번 본번)
        #   [15:19] lot sub  number (지번 부번)
        bjd_code = pnu[:10] if len(pnu) >= 10 else ""
        mt_flag  = pnu[10] if len(pnu) >= 11 else "1"
        lot_main = int(pnu[11:15]) if len(pnu) >= 15 else 0
        lot_sub = int(pnu[15:19]) if len(pnu) >= 19 else 0

        # Geometry - centroid only uses first few points for speed
        points_2d = [(p[0], p[1]) for p in shape.points]
        lon, lat = _centroid_wgs84(points_2d, transformer)
        if lon is None:
            continue

        boundary_coords = _polygon_to_wgs84(shape, transformer, max_points=30)
        area_sq_m = _compute_area_sq_m(points_2d)

        parcel = {
            "uid": f"parcel-{pnu}",
            "pnu": pnu,
            "jibun": jibun_num,
            "bjd_code": bjd_code,
            "lot_main": lot_main,
            "lot_sub": lot_sub,
            "land_category": land_category,
            "land_cat_code": land_cat_code,
            # ``is_mountain`` is encoded in PNU position 11 (1=regular, 2=mountain).
            # The cadastral SHP's BCHK field is an approval-status code per the
            # LSMD_CONT_LDREG schema (0=unapproved, 1=approved, 2=attribute-only,
            # 3=rejected, 4=map-approved, 5=existing-approved, 8/9=variants),
            # NOT a mountain flag — earlier releases of this loader conflated
            # the two and emitted incorrect ``is_mountain`` values.
            "is_mountain": mt_flag == "2",
            "approval_code": bchk,
            "longitude": lon,
            "latitude": lat,
            "area_sq_m": round(area_sq_m, 1),
            "boundary": json.dumps(boundary_coords),
        }
        parcels.append(parcel)

        if (i + 1) % 10000 == 0:
            print(f"  [Parcel] Processed {i + 1} / {len(sf)} records, loaded {len(parcels)}...")

    msg = f"  [Parcel] Total parcels loaded: {len(parcels)} (skipped {skipped} non-building parcels"
    if col_adm_se_filter:
        msg += f", skipped {skipped_other_district} parcels outside COL_ADM_SE={col_adm_se_filter}"
    msg += ")"
    print(msg)
    return parcels


def load_road_addresses(path: str):
    """
    Load road address data from a KAIS road-address dump (CSV or XLSX).

    The KAIS public release ships as a UTF-8 CSV; older private releases
    were redistributed internally as XLSX. The column layout (positions
    1, 6, 7, 8, 10, 12, 13, 14, 15, 16, 21, 22) is identical between the
    two formats, so this loader picks the appropriate reader from the
    file extension and yields one ``addr_map`` keyed by 19-digit PNU.

    Returns:
        Dict mapping PNU → road address info dict.
    """
    if not path:
        return {}

    import os
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        rows_iter = _iter_csv_rows(path)
    elif ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        rows_iter = _iter_xlsx_rows(path)
    else:
        raise ValueError(f"Unsupported road-address file extension: {ext} ({path})")

    addr_map = {}
    rows = rows_iter

    count = 0
    for row in rows:
        if not row or len(row) < 18:
            continue

        bjd_code = str(row[1] or "").strip()
        sido_name = str(row[2] or "").strip()      # 시도명, e.g. 대전광역시
        sigungu_name = str(row[3] or "").strip()   # 시군구명, e.g. 유성구
        is_mountain = str(row[6] or "0").strip()
        lot_main = str(row[7] or "0").strip()
        lot_sub = str(row[8] or "0").strip()
        road_name = str(row[10] or "").strip()
        is_underground = str(row[11] or "0").strip()
        bldg_main = str(row[12] or "0").strip()
        bldg_sub = str(row[13] or "0").strip()
        admin_dong_code = str(row[14] or "").strip()
        admin_dong_name = str(row[15] or "").strip()
        zipcode = str(row[16] or "").strip()
        building_name = str(row[21] or "").strip()
        sigungu_bldg_name = str(row[22] or "").strip()

        # Construct PNU
        mountain_flag = "2" if is_mountain == "1" else "1"
        pnu = f"{bjd_code}{mountain_flag}{int(lot_main):04d}{int(lot_sub):04d}"

        # Construct full road address — derive the city/district prefix from
        # the row itself rather than hard-coding it, so the same loader
        # works for any KAIS region (e.g., 유성구, 동구, 중구).
        prefix = (sido_name + " " + sigungu_name).strip()
        bldg_num = bldg_main
        if bldg_sub and bldg_sub != "0":
            bldg_num += f"-{bldg_sub}"
        full_addr = (f"{prefix} {road_name} {bldg_num}".strip()
                     if prefix else f"{road_name} {bldg_num}".strip())

        addr_map[pnu] = {
            "road_name": road_name,
            "building_main": int(bldg_main) if bldg_main else 0,
            "building_sub": int(bldg_sub) if bldg_sub else 0,
            "admin_dong_code": admin_dong_code,
            "admin_dong_name": admin_dong_name,
            "zipcode": zipcode,
            "full_road_address": full_addr,
            "building_name": building_name or sigungu_bldg_name,
            "is_underground": is_underground == "1",
        }
        count += 1

    print(f"  [RoadAddr] Loaded {count} road address records, {len(addr_map)} unique PNUs")
    return addr_map


def _iter_xlsx_rows(path):
    """Yield data rows (skipping header) from an XLSX road-address dump."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    try:
        for row in ws.iter_rows(min_row=2, values_only=True):
            yield row
    finally:
        wb.close()


def _iter_csv_rows(path):
    """Yield data rows (skipping header) from a CSV road-address dump."""
    import csv
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # skip header
        for row in reader:
            yield row
