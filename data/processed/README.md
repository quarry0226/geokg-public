# `data/processed/` — Pre-processed loader output

This directory ships **pre-processed Yuseong-gu and Sejong-si scene dumps** so users can reproduce the experiments without downloading the full 1.4 GB raw KAIS / LSMD / TN_RODWAY shapefile bundle.

| File | Size (gzip) | Plain JSON | Contents |
|---|---:|---:|---|
| `yuseong.json.gz` | ~11 MiB | ~73 MiB | All 118,231 Yuseong nodes (buildings, parcels, roads, intersections, AutoRoadLinks, ThingsAddr, simulation entities) + seed relationships |
| `sejong.json.gz`  | ~30 MiB | ~213 MiB | All 320,863 Sejong nodes + seed relationships |
| **Total** | **~41 MiB** | ~286 MiB | — |

The compression ratio is 14% because the polygon `boundary` / polyline `coordinates` fields are dense decimal-coordinate strings that compress very well.

## How the dumps were produced

```bash
python scripts/dump_processed_data.py            # both regions
python scripts/dump_processed_data.py yuseong    # one region
```

The script calls `backend.data.seed_data.generate_scene_data(region)` (the same function that the reseed pipeline calls) and serialises the returned dict to gzipped JSON. The dict layout is documented at the top of `backend/data/seed_data.py`.

## How to use the dumps

`scripts/reseed_neo4j.py` auto-detects the dumps and prefers them over raw SHP. If `data/processed/<region>.json.gz` exists, you do **not** need any raw data:

```bash
python scripts/reseed_neo4j.py yuseong            # auto-detect (will use processed dump)
python scripts/reseed_neo4j.py yuseong --from processed   # force processed
python scripts/reseed_neo4j.py yuseong --from raw         # force raw SHP/XLSX
```

Programmatic access:

```python
from scripts.dump_processed_data import load_processed
scene = load_processed("yuseong")            # same dict that generate_scene_data() returns
print(scene["_metrics"]["n_buildings"])      # 18106
```

## Schema

Each dump is a JSON object with the following top-level keys (same as the in-memory output of `generate_scene_data`):

```jsonc
{
  "region": "yuseong",
  "region_label": "Yuseong-gu",
  "area_km2": 176.5,
  "population_estimate": 360000,
  "buildings":        [ ... 18,106 dicts ... ],
  "parcels":          [ ... 71,580 dicts ... ],
  "roads":            [ ... 2,210 dicts ... ],
  "intersections":    [ ... 8,325 dicts ... ],
  "auto_road_links":  [ ... 16,504 dicts ... ],
  "iot_addresses":    [ ... 1,562 dicts (shelter/transit/park/monitor) ... ],
  "zones":            [ 2 dicts ],
  "parking_lots":     [ 3 dicts ],
  "parking_spaces":   [ 30 dicts ],
  "vehicles":         [ 50 dicts ],
  "sensors":          [ 20 dicts ],
  "cameras":          [ 15 dicts ],
  "trees":            [ 50 dicts ],
  "facilities":       [ 25 dicts ],
  "states":           [ ... time-varying state nodes ... ],
  "relationships":    [ ... seed-time CONTAINS / HAS_STATE / ON_ROAD edges ... ],
  "_metrics":         { "n_buildings": 18106, "n_parcels": 71580, ... }
}
```

## When to use raw SHP instead

You need the raw data only if:

1. You want to update the dump to a newer KAIS release (e.g. the next monthly publication).
2. You want to verify the SHP-loading code on your own machine.
3. You are studying a different Korean municipality not covered by the two dumps.

Otherwise the processed dumps are byte-identical inputs to the rule engine and reproduce the paper's headline numbers exactly. See `../docs/DATA_DOWNLOAD.md` for raw-data download instructions.

## Licence

The processed dumps are derivative outputs of the public Korean government open datasets listed in `../docs/DATA_DOWNLOAD.md`. They are redistributed here under the [Korea Open Government Licence (Type 1)](https://www.kogl.or.kr/info/license.do) terms — free use, commercial or non-commercial, with attribution. Attribution is captured in the paper bibliography.
