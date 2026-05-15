"""Building Entrance Loader (TL_SPBD_ENTRC).

KAIS publishes one or more `entrance` points per registered road-name building
in the TL_SPBD_ENTRC SHP. The point is the actual access door of the building
on the publicly-numbered road, which is closer to the road polyline than the
building's footprint centroid (the latter being a polygon centroid of the
entire building, often offset from the street side by half the building
depth).

This loader returns a dict keyed by the building's PNU-equivalent identifier
(EQB_MAN_SN — equity/management serial that the upstream KAIS dataset shares
with TL_SGCO_RNADR_MST, see KAIS schema). When EQB_MAN_SN is unavailable we
fall back to nearest-spatial assignment in the seed phase.

Output entries:
    {
        "ent_man_no":  int,    # entrance manage number (unique per row)
        "eqb_man_sn":  str,    # building-level link key
        "entrc_se":    str,    # entrance type (RM = main, etc.)
        "longitude":   float,  # WGS84
        "latitude":    float,
        "raw_x":       float,  # source x in projected CRS
        "raw_y":       float,
    }
"""

import os
import shapefile
from pyproj import Transformer

# Cached transformers
_TRANSFORMER_CACHE = {}


def _get_transformer(src_crs):
    t = _TRANSFORMER_CACHE.get(src_crs)
    if t is None:
        t = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        _TRANSFORMER_CACHE[src_crs] = t
    return t


def _detect_crs_from_prj(shp_path):
    """Detect EPSG from accompanying .prj — defaults to 5186."""
    prj = shp_path + ".prj" if not shp_path.endswith(".prj") else shp_path
    if not os.path.exists(prj):
        return "EPSG:5186"
    try:
        with open(prj, "r", encoding="utf-8", errors="ignore") as f:
            wkt = f.read()
    except Exception:
        return "EPSG:5186"
    if 'AUTHORITY["EPSG","5186"]' in wkt or '"5186"' in wkt:
        return "EPSG:5186"
    if 'AUTHORITY["EPSG","5179"]' in wkt or "UTM-K" in wkt:
        return "EPSG:5179"
    if 'AUTHORITY["EPSG","5174"]' in wkt or "Korean_1985" in wkt or "Bessel_1841" in wkt:
        return "EPSG:5174"
    if "Korea_2000" in wkt or "Korea 2000" in wkt:
        return "EPSG:5186"
    return "EPSG:5186"


def load_entrances(shp_path: str, src_crs: str | None = None) -> list[dict]:
    """Load all building entrance points from a TL_SPBD_ENTRC SHP."""
    if src_crs is None:
        src_crs = _detect_crs_from_prj(shp_path)
    transformer = _get_transformer(src_crs)
    print(f"  [Entrance] CRS: {src_crs} -> EPSG:4326")

    sf = shapefile.Reader(shp_path, encoding="cp949")
    entrances = []
    for sr in sf.iterShapeRecords():
        rec = sr.record
        pts = sr.shape.points
        if not pts:
            continue
        x, y = pts[0]
        lon, lat = transformer.transform(x, y)
        entrances.append({
            "ent_man_no": int(rec["ENT_MAN_NO"]) if rec["ENT_MAN_NO"] not in (None, "") else 0,
            "eqb_man_sn": str(rec["EQB_MAN_SN"]) if rec["EQB_MAN_SN"] is not None else "",
            "entrc_se":   str(rec["ENTRC_SE"]) if rec["ENTRC_SE"] is not None else "",
            "longitude":  round(lon, 6),
            "latitude":   round(lat, 6),
            "raw_x":      x,
            "raw_y":      y,
        })

    print(f"  [Entrance] Loaded {len(entrances):,} entrance points")
    return entrances


def attach_entrances_to_buildings(buildings: list[dict], entrances: list[dict],
                                   max_dist_m: float = 60.0):
    """Attach the nearest entrance to each building (via spatial join).

    Adds two attributes to each matched building:
        ``entrance_lon``, ``entrance_lat`` — the entrance point coordinates,
        which downstream FRONTS_ROAD computation can use in place of the
        building centroid for a more accurate building-to-road distance.

    Buildings with no entrance within ``max_dist_m`` keep their centroid as the
    effective FRONTS_ROAD source.

    Returns the count of buildings successfully matched.
    """
    if not buildings or not entrances:
        return 0

    # Project to a flat plane for nearest-neighbour search (rough equirectangular)
    # at the mean latitude of the building set.
    import math

    mean_lat = sum(b.get("latitude", 0.0) for b in buildings) / len(buildings)
    cos_lat = math.cos(math.radians(mean_lat))
    M_LON = 111320.0 * cos_lat
    M_LAT = 110540.0

    # Bin entrances into a coarse grid for O(1)-ish lookup
    GRID_M = 100.0
    grid: dict[tuple[int, int], list[dict]] = {}
    for e in entrances:
        gx = int(e["longitude"] * M_LON / GRID_M)
        gy = int(e["latitude"] * M_LAT / GRID_M)
        grid.setdefault((gx, gy), []).append(e)

    matched = 0
    max_d2 = max_dist_m * max_dist_m
    for b in buildings:
        blon, blat = b.get("longitude"), b.get("latitude")
        if blon is None or blat is None:
            continue
        bx = blon * M_LON
        by = blat * M_LAT
        gx0 = int(bx / GRID_M)
        gy0 = int(by / GRID_M)
        best_d2 = float("inf")
        best_e = None
        for dgx in (-1, 0, 1):
            for dgy in (-1, 0, 1):
                cell = grid.get((gx0 + dgx, gy0 + dgy))
                if not cell:
                    continue
                for e in cell:
                    ex = e["longitude"] * M_LON
                    ey = e["latitude"] * M_LAT
                    d2 = (bx - ex) ** 2 + (by - ey) ** 2
                    if d2 < best_d2:
                        best_d2 = d2
                        best_e = e
        if best_e is not None and best_d2 <= max_d2:
            b["entrance_lon"] = best_e["longitude"]
            b["entrance_lat"] = best_e["latitude"]
            b["entrance_se"] = best_e["entrc_se"]
            matched += 1

    print(f"  [Entrance] Attached entrance to {matched:,} / {len(buildings):,} "
          f"buildings ({matched/len(buildings)*100:.1f}%, threshold {max_dist_m:.0f} m)")
    return matched
