# Raw-data download guide

The raw input datasets (~1.4 GB) are **not** included in this repository because of size and licence constraints. This guide lists the exact source URLs, file layout, and any post-processing required to match the loaders in `backend/data/`.

All sources are Korean government open data under the [Korea Open Government Licence (Type 1)](https://www.kogl.or.kr/info/license.do) or equivalent public-data terms. Attribution is provided in the paper bibliography (refs [36], [37]).

---

## Required datasets

After download, place each region's files under `data/<region>/` matching the layout below. The exact paths are declared in [`backend/config.py`](../backend/config.py) under `REGION_CONFIG`; if you change the layout, update `config.py` accordingly. Original Korean filenames from the KAIS portal are noted in parentheses for reference.

```
data/
├── yuseong/                                       (Daejeon Yuseong-gu, 대전광역시 유성구)
│   ├── buildings/
│   │   ├── buildings.shp                          (TL_SGCO_RNADR_MST, road-name addressed buildings)
│   │   └── buildings.{dbf,shx,prj,cpg}
│   ├── cadastral/
│   │   └── LSMD_CONT_LDREG_5174_30200_202604.shp  (continuous cadastral, 2026-Q2)
│   ├── road_address/
│   │   └── road_address_yuseong.csv               (Building-Register Summary, 총괄표제부 XLSX exported to CSV)
│   ├── road_network/
│   │   ├── TL_SPRD_MANAGE.shp                     (road-name-address road sections)
│   │   └── TL_SPRD_CRSRD.shp                      (intersections, legacy; replaced by TN_RODWAY_NODE)
│   └── iot_address/                               (KAIS object-address, 사물주소)
│       ├── shelter/                               (Civil-defense + EQOUT + EWRC + CoolingCen)
│       ├── transit/                               (Bus stops + taxi stands)
│       └── park/                                  (Park + children-park + sports-park)
│
├── sejong/                                        (Sejong Special Self-Governing City, 세종특별자치시)
│   ├── buildings/
│   │   └── buildings.shp                          (TL_SGCO_RNADR_MST, Sejong subset)
│   ├── cadastral/
│   │   └── LSMD_CONT_LDREG_36_202604.shp          (continuous cadastral, 2026-Q2)
│   ├── road_address/
│   │   └── road_address_sejong.csv                (Building-Register Summary, Sejong subset)
│   ├── road_network/
│   │   └── 36110/TL_SPRD_MANAGE.shp               (Sejong road sections, SIG_CD-prefixed)
│   └── iot_address/                               (KAIS object-address, Sejong subset)
│       ├── shelter/                               (+ EMERWAT, only present in Sejong)
│       ├── transit/
│       └── park/
│
└── road_network_national/                         (TN_RODWAY_NODE/LINK national base-map, used by both regions)
    ├── TN_RODWAY_NODE.shp                         (real intersections; filter RDNODE_SE ∈ {RWN003, RWN005})
    └── TN_RODWAY_LINK.shp                         (carriageway links)
```

Filter the national `TN_RODWAY_*` SHPs by `LEGLCD_SE` prefix (`30200` for Yuseong, `36110` for Sejong) inside the loader; the SHPs themselves can stay in the shared `road_network_national/` directory.

---

## Source 1 — Road-name address master (TL_SGCO_RNADR_MST + TL_SPBD_ENTRC)

- **Provider:** Korea Address Information System (KAIS), Ministry of the Interior and Safety
- **Portal:** <https://business.juso.go.kr/addrlink/index.do>
- **Release used in the paper:** 2026/03/01 monthly publication
- **What to download:**
  - Daejeon (대전광역시) — `TL_SGCO_RNADR_MST.shp` (building footprint with road-name address) and `TL_SPBD_ENTRC.shp` (building entrance points)
  - Sejong (세종특별자치시) — same two layers

The KAIS publication contract guarantees that every road-name-addressed building's `ADR_MNG_NO` embeds an `RN_CD` (road-name code) that resolves to a `TL_SPRD_MANAGE` segment of the same code; the framework relies on this contract for the 100 % `ON_STREET` coverage claim.

## Source 2 — TN_RODWAY_NODE / TN_RODWAY_LINK (national base-map road network)

- **Provider:** National Geographic Information Institute (NGII), Ministry of Land, Infrastructure and Transport
- **Portal:** <https://www.ngii.go.kr/kor/contents/contentsView.do?rbsIdx=152>
- **Layers:** `TN_RODWAY_NODE` (real intersections, filter `RDNODE_SE ∈ {RWN003, RWN005}` for physical nodes only) and `TN_RODWAY_LINK` (carriageway segments)
- The paper's Yuseong-only counts (filter `LEGLCD_SE` prefix `30200`) are 8,325 real intersections and 16,504 links; Sejong is 25,639 and 53,808 respectively.

## Source 3 — LSMD_CONT_LDREG (continuous cadastral map)

- **Provider:** Ministry of Land, Infrastructure and Transport / V-World portal
- **Portal:** <https://www.vworld.kr/dtmk/dtmk_ntads_s002.do>
- **Layer:** Continuous cadastral parcels (Bessel 1841 or GRS80, 2026-Q2 release)
- **EPSG:** 5186 (TM on GRS80)

The framework's PNU spatial-join attaches Building-Register Summary attributes to building nodes; the 2026/03/01 release achieves 99.9 % match in Yuseong and 99.6 % in Sejong.

## Source 4 — Building-Register Summary XLSX (KAIS supplementary master)

- **Provider:** KAIS
- **Portal:** <https://business.juso.go.kr/addrlink/openApi/searchApi.do> → supplementary download
- **Files:**
  - `02. 총괄표제부_대전광역시_유성구.xlsx`
  - `02. 총괄표제부_세종특별자치시.xlsx`

The XLSX enriches buildings with permit / approval-date, gross-floor-area, structural usage, and parking-capacity attributes via PNU spatial join (1,253 / 18,106 Yuseong buildings = 6.9 % match; 3,886 / 27,773 Sejong = 14.0 % match).

## Source 5 — Object-address (JUSUAN) facility catalogues

- **Provider:** Ministry of the Interior and Safety, object-address (사물주소) catalogue
- **Portal:** <https://business.juso.go.kr/addrlink/openApi/searchObjListAjax.do>
- **Sub-catalogues used:**
  - Civil-defense shelters (`CivilDefense`): 113 (Yuseong), 166 (Sejong)
  - Earthquake outdoor shelters (`EQOUT`): 57 / 206
  - Heat-wave cooling centres (`CoolingCen`): 243 / 448
  - Disaster-refuge centres (`EWRC` / `EMERWAT`): 0 + 28 / 28 + 26
  - Bus / taxi-stand (`BUSST`, `TAXIST`): 666 / 1,081
  - Park / playground / sports park (`PARK`, `SCPARK`, `Children`): 265 / 276

---

## Coordinate reference systems

All sources are transformed to WGS84 (EPSG:4326) via `pyproj`'s `Transformer.from_crs(source_epsg, 4326, always_xy=True)` chain. Per-source CRS:

| Source | EPSG | Datum / projection |
|---|---|---|
| KAIS road-name address | 5179 | UTM-K (GRS80) |
| TN_RODWAY_NODE/LINK | 5179 | UTM-K (GRS80) |
| LSMD cadastral | 5186 | TM (GRS80) |
| Legacy KAIS releases | 5174 | TM (Bessel 1841) |

Worst-case stacked-transformation error is bounded below 1 m end-to-end (paper §IV.B.1), two orders of magnitude smaller than the 50 m minimum spatial threshold used by any rule in the engine.

---

## Licence

All five sources are public-domain Korean government open data. Redistribution within this repository is intentionally avoided to respect the originator's release channels; download each from the official portal listed above. The Korea Open Government Licence (Type 1) permits commercial and non-commercial use with attribution; attribution is captured in the paper bibliography.
