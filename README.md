# k-GeoKG — A Declarative GeoKG Framework for District-Scale Urban Infrastructure Analysis

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](requirements.txt)
[![Neo4j 2026.04](https://img.shields.io/badge/Neo4j-2026.04-008cc1.svg)](https://neo4j.com)

Reference implementation for the IEEE Access paper:

> **A Declarative Geographic Knowledge Graph Framework for District-Scale Urban Infrastructure Analysis**
> Chae-Seok Lee, Dae-seung Park, Jae-min Choi, Ho-jong Chang. *IEEE Access*, 2026.

This framework redirects the GeoKG focus from 3D-visualisation to district-scale analytical workloads. A declarative rule engine of 7 matching strategies and 18 rules automatically generates 936,739 spatial relationships from 118,231 Yuseong-gu (Daejeon) nodes and 1,402,275 relationships from 320,863 Sejong-si nodes on the unified KAIS 2026/03/01 release.

---

## Headline numbers

| Metric | Yuseong-gu | Sejong-si | Ratio |
|---|---:|---:|---:|
| Buildings | 18,106 | 27,773 | 1.53× |
| Total nodes | 118,231 | 320,863 | 2.71× |
| Total relationships | **936,739** | **1,402,275** | 1.50× |
| Rule-engine wall-time (n=10 mean) | 207.2 s | 644.8 s | 3.11× |
| End-to-end wall-time (n=10 ± 95% CI) | 382.6 ± 2.2 s | 1,350.7 ± 6.4 s | 3.53× |
| End-to-end build (n=10 ± 95% CI) | 382.6 ± 2.2 s | 1,350.7 ± 6.4 s | 3.53× |
| Pair operations | 40.0 M | 116.7 M | 2.92× |

The same engine, the same 18 rule definitions, and the same Cypher templates run unchanged on both regions through a thin region-specific data adapter.

---

## Repository layout

```
geokg-public/
├── backend/                       Main Python framework
│   ├── geokg/                     Rule engine, ontology, builder
│   │   ├── relationship_engine.py
│   │   ├── relationship_rules.py
│   │   ├── ontology.py
│   │   └── builder.py
│   ├── data/                      Per-source loaders (KAIS, LSMD, etc.)
│   │   ├── building_loader.py
│   │   ├── entrance_loader.py
│   │   ├── parcel_loader.py
│   │   ├── road_network_loader.py
│   │   ├── safety_facility_loader.py
│   │   └── relationship_enrichment.py
│   ├── api/                       FastAPI REST endpoints
│   ├── db/                        Neo4j adapter
│   ├── fusion/                    CRS / spatial helpers
│   ├── experiments/               Validation + benchmark scripts
│   │   ├── *.py
│   │   └── results/               Canonical experiment JSONs (single source of truth)
│   ├── config.py
│   ├── main.py
│   └── requirements.txt
│
├── frontend/                      Cesium.js 3D visualisation
│
├── scripts/                       Top-level reproducibility scripts
│   ├── reseed_neo4j.py            End-to-end build on Neo4j
│   ├── bench_wallclock_n10.py     n = 10 wall-time variance benchmark
│   ├── measure_polyline_perpendicular.py
│   ├── measure_fronts_road_distance.py
│   ├── compute_dong_scores_neo4j.py
│   ├── compare_regions.py
│   └── ...
│
├── docs/                          Code-side documentation
│   └── DATA_DOWNLOAD.md           Where to obtain the KAIS raw data
│
├── data/                          Raw input data (download separately)
│   └── README.md
│
├── start_neo4j.bat                Neo4j launcher (Windows)
├── requirements.txt               Pinned Python dependencies
├── CITATION.cff                   Citation metadata
└── LICENSE
```

---

## Quick start

### 1. Prerequisites

- **Python 3.10+** (tested on 3.10.x and 3.12.x)
- **Neo4j 2026.04** (Community Edition is sufficient; the paper's wall-clock numbers are reproducible at the Community-Edition 716.8 MiB per-transaction pool default)
- ~16 GB RAM, 10 GB free disk
- Tested on Windows 11 (CPU: Intel Core i9-14900KF). Linux / macOS supported via the same Python stack.

### 2. Install Python dependencies

```bash
python -m venv .venv
.venv/Scripts/activate         # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

### 3. Get the input data

Two options:

#### Option A (recommended, ~41 MiB) — **pre-processed dumps**

The repository ships `data/processed/yuseong.json.gz` (~11 MiB) and `data/processed/sejong.json.gz` (~30 MiB), which are the output of the SHP/XLSX loaders for both regions. The reseed script auto-detects and uses these dumps. No raw data download is needed.

```bash
ls data/processed/
# yuseong.json.gz   sejong.json.gz   README.md
```

See [data/processed/README.md](data/processed/README.md) for schema details.

#### Option B (~1.4 GB) — **raw KAIS / LSMD / TN_RODWAY shapefiles**

Needed only if you want to refresh the dumps to a newer monthly KAIS release or study a different Korean municipality. See **[docs/DATA_DOWNLOAD.md](docs/DATA_DOWNLOAD.md)** for the source URLs and expected layout:

```
data/
├── 대전광역시 도로명주소/           # KAIS TL_SGCO_RNADR_MST + TL_SPBD_ENTRC
├── 세종특별자치시 도로명주소/
├── 노드_링크(대전,세종)/            # TN_RODWAY_NODE/LINK
├── LSMD_CONT_LDREG_대전/           # Cadastral SHP
├── LSMD_CONT_LDREG_세종/
├── 02. 총괄표제부_대전광역시_유성구.xlsx
└── 02. 총괄표제부_세종특별자치시.xlsx
```

After populating, regenerate the dumps with:

```bash
python scripts/dump_processed_data.py
```

### 4. Start Neo4j

```bash
./start_neo4j.bat              # Windows
# Or: neo4j console            # Linux / macOS
```

The first start requires setting an initial password; the default credentials in `backend/config.py` are `neo4j / NX2010nx`. Edit `config.py` to match your local password.

### 5. Reproduce the end-to-end build

```bash
python scripts/reseed_neo4j.py yuseong          # auto-detect: prefers processed dump
python scripts/reseed_neo4j.py sejong
python scripts/reseed_neo4j.py both             # both regions in sequence

# Or be explicit about the seed source:
python scripts/reseed_neo4j.py yuseong --from processed   # data/processed/*.json.gz
python scripts/reseed_neo4j.py yuseong --from raw         # raw SHP/XLSX
```

Each invocation logs timing and per-rule counts and writes a JSON snapshot under `backend/experiments/results/reseed_neo4j_<region>_<timestamp>.json`. Expected wall-times on the reference workstation (Intel Core i9-14900KF), reported as n=10 means with 95% Student-t CIs (run `scripts/bench_wallclock_n10.py` to reproduce):

| Source | Region | Data load | Neo4j build | Rule engine | End-to-end |
|---|---|---:|---:|---:|---:|
| Raw SHP    | Yuseong | 129.3 s | 253.3 s | 207.2 s | 382.6 ± 2.2 s |
| Raw SHP    | Sejong  | 358.2 s | 992.5 s  | 644.8 s | 1,350.7 ± 6.4 s |
| Processed  | Yuseong | ~5 s    | 253.3 s | 207.2 s | ~258 s |
| Processed  | Sejong  | ~12 s   | 992.5 s  | 644.8 s | ~1,005 s |

(The processed-dump path skips the SHP parsing time but keeps the Neo4j ingestion and rule-engine timing intact, so the rule-engine and per-rule edge counts remain identical to the paper.)

### 6. Reproduce the analysis case studies

```bash
python scripts/compute_dong_scores_neo4j.py --region yuseong
python scripts/compute_dong_scores_neo4j.py --region sejong
python backend/experiments/sejong_portability.py
python backend/experiments/auto_validate_enhanced.py
python backend/experiments/backend_benchmark.py
```

Outputs are written to `backend/experiments/results/`.

### 7. Run the analysis REST API

```bash
cd backend
uvicorn main:app --reload --port 8000
```

API endpoints under `/api/kg/` expose the 16 analysis functions (path finding, dead-zone, road-closure impact, safety profile, district comparison, etc.). The OpenAPI spec is auto-generated at `http://localhost:8000/docs`.

### 8. Open the 3D visualisation

```bash
cd frontend
python -m http.server 8001
# Open http://localhost:8001 in your browser
```

---

## Experiment-result snapshots

The `backend/experiments/results/` directory carries the JSON snapshots produced by the reproducibility scripts under `scripts/`:

| File | Produced by |
|---|---|
| `reseed_neo4j_{region}_<timestamp>.json` | `scripts/reseed_neo4j.py` (per-run timing + edge counts) |
| `measured_wallclock_n10.json`            | `scripts/bench_wallclock_n10.py` (n=10 wall-time CIs) |
| `measured_polyline_gain.json`            | `scripts/measure_polyline_perpendicular.py` |
| `dong_scores_{region}_<timestamp>.json`  | `scripts/compute_dong_scores_neo4j.py` |
| `ahp_external_<timestamp>.json`          | `scripts/ahp_safety_weights.py` |

Every script writes incrementally into `backend/experiments/results/`, so re-running any script produces a fresh timestamped snapshot side-by-side with the canonical ones.

---

## GSID identifier service

Every node carries a 36-character canonical identifier under the KAIST CRC **GSID v7.0** specification:

```
{DOMAIN:2}-{SPATIAL:8}-{ELEV:2}-{GEN:2}-{SEQ:6}-{CHK:1}
e.g.  BL-89C259AB-G0-00-000001-7
```

The derivation algorithm (S2 cell tokenisation, Luhn mod-36 checksum, per-domain sequence allocation) is provided by an external service and is **not redistributed** with this release. The repository instead ships an **interface-compatible stub** at `backend/geokg/gsid.py` that returns deterministic UUID5-based synthetic identifiers of the same canonical character length.

The framework's rule engine, analysis API, and all reported case-study results work end-to-end with the stub because no analysis function inspects GSID internals — identifiers are used purely as opaque entity handles for graph indexing and cross-table joins. To use the canonical KAIST CRC format in production, replace `backend/geokg/gsid.py` with the upstream implementation; the public function surface (`generate_gsid_for_entity`, `resolve_subtype`, `reset_seq_counters`, etc.) is preserved.

---

## Citation

If you use this software, please cite:

```bibtex
@article{lee2026geokg,
  title={A Declarative Geographic Knowledge Graph Framework for District-Scale Urban Infrastructure Analysis},
  author={Lee, Chae-Seok and Park, Dae-seung and Choi, Jae-min and Chang, Ho-jong},
  journal={IEEE Access},
  year={2026},
  doi={10.1109/ACCESS.2026.XXXXXXX}
}
```

See [CITATION.cff](CITATION.cff) for structured metadata.

---

## License

MIT (see [LICENSE](LICENSE)). The Korean government open datasets referenced in this work follow their respective public-data terms; see [docs/DATA_DOWNLOAD.md](docs/DATA_DOWNLOAD.md).

---

## Acknowledgement

This work was supported by the Institute of Information & Communications Technology Planning & Evaluation (IITP) grant funded by the Korea government (MSIT) (No. RS-2024-00459703, Development of next-generation AI integrated mobility simulation and prediction/application technologies for metropolitan cities).
