"""Compute per-법정동 safety scores from live Neo4j data.

Reads the seeded Neo4j databases (geokg, geokg-sejong), groups Building
nodes by legal_dong_code (10-digit BJD prefix of PNU), and emits per-dong
shelter/transit/park/road coverage and an overall safety score under the
paper-heuristic weights as well as the AHP consensus weights.

Output: backend/experiments/results/dong_scores_<region>_<timestamp>.json
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.db.neo4j_client import db, set_region  # type: ignore
from backend.data.building_loader import load_summary_titles  # type: ignore

PAPER_WEIGHTS = {"shelter": 0.30, "monitor": 0.20, "transit": 0.20,
                 "park": 0.15, "road": 0.15}
AHP_WEIGHTS   = {"shelter": 0.087, "monitor": 0.284, "transit": 0.320,
                 "park": 0.124, "road": 0.185}

# Minimum building count per dong to treat the dong as statistically meaningful
# (the long tail of N<30 dongs is dominated by isolated cadastral parcels that
# the official KAIS 행정동 aggregation rolls up into the parent admin dong).
MIN_BUILDINGS_PER_DONG = 30

# Path to 총괄표제부 xlsx for BJD→dong-name lookup
SUMMARY_XLSX = {
    "yuseong": "data/02. 총괄표제부_대전광역시_유성구.xlsx",
    "sejong":  "data/02. 총괄표제부_세종특별자치시.xlsx",
}


def per_dong_metrics(region: str) -> dict:
    set_region(region)
    print(f"\n=== {region}: per-법정동 safety metrics ===")

    # Pre-load authoritative BJD→dong-name lookup from 총괄표제부 xlsx
    summary = load_summary_titles(SUMMARY_XLSX[region])
    bjd_to_name = summary.pop("__bjd_to_dong_name__", {})

    # Group buildings by legal-dong code (first 10 chars of PNU)
    q = """
    MATCH (b:Building)
    WHERE b.pnu IS NOT NULL AND b.pnu <> ''
    RETURN substring(b.pnu, 0, 10) AS bjd_code,
           coalesce(b.legal_dong_name, '') AS dong_name,
           count(*) AS n_buildings,
           sum(CASE WHEN exists((b)-[:NEAREST_SHELTER]->(:ThingsAddr)) THEN 1 ELSE 0 END) AS n_shelter,
           sum(CASE WHEN exists((b)-[:ACCESSIBLE_BY_TRANSIT]->(:ThingsAddr)) THEN 1 ELSE 0 END) AS n_transit,
           sum(CASE WHEN exists((b)-[:NEAR_PARK]->(:ThingsAddr)) THEN 1 ELSE 0 END) AS n_park,
           sum(CASE WHEN exists((b)-[:FRONTS_ROAD]->(:Road)) THEN 1 ELSE 0 END) AS n_road
    ORDER BY bjd_code
    """
    rows = db.graph.run(q).data()
    dong_scores: dict[str, dict] = {}
    for r in rows:
        bjd = r["bjd_code"]
        n = r["n_buildings"]
        if n == 0:
            continue
        shel = r["n_shelter"] / n
        trans = r["n_transit"] / n
        park = r["n_park"] / n
        road = r["n_road"] / n
        # Monitor coverage: we do not have a NEAR_MONITOR edge yet; treat as 0 for paper-weight baseline
        # so the safety score reflects only the four directly-measured indicators.
        monitor = 0.0

        score_paper = (PAPER_WEIGHTS["shelter"] * shel +
                       PAPER_WEIGHTS["transit"] * trans +
                       PAPER_WEIGHTS["park"] * park +
                       PAPER_WEIGHTS["road"] * road) * 100
        score_ahp = (AHP_WEIGHTS["shelter"] * shel +
                     AHP_WEIGHTS["transit"] * trans +
                     AHP_WEIGHTS["park"] * park +
                     AHP_WEIGHTS["road"] * road) * 100

        # Prefer xlsx-derived dong_name; fallback to Neo4j's stored value.
        dong_name = bjd_to_name.get(bjd, "") or r["dong_name"]
        dong_scores[bjd] = {
            "bjd_code": bjd,
            "legal_dong_name": dong_name,
            "n_buildings": n,
            "shelter_pct": round(shel * 100, 1),
            "transit_pct": round(trans * 100, 1),
            "park_pct": round(park * 100, 1),
            "road_pct": round(road * 100, 1),
            "safety_score_paper": round(score_paper, 2),
            "safety_score_ahp": round(score_ahp, 2),
        }

    # Filter to dongs with statistically meaningful N
    stable = {bjd: d for bjd, d in dong_scores.items()
              if d["n_buildings"] >= MIN_BUILDINGS_PER_DONG}
    # Sort by paper score desc
    ranked = sorted(stable.items(), key=lambda x: -x[1]["safety_score_paper"])
    print(f"  Total 법정동: {len(dong_scores)} ({len(stable)} with N >= {MIN_BUILDINGS_PER_DONG})")
    print(f"\n  Top-5 (paper weights, N >= {MIN_BUILDINGS_PER_DONG}):")
    for bjd, d in ranked[:5]:
        name = d["legal_dong_name"] or bjd[5:]
        print(f"    {name:8s} (bjd={bjd}, n={d['n_buildings']:>5}): "
              f"score={d['safety_score_paper']:>6.2f}  "
              f"shelter={d['shelter_pct']:>5.1f}%  "
              f"transit={d['transit_pct']:>5.1f}%  "
              f"park={d['park_pct']:>5.1f}%")
    print(f"\n  Bottom-5 (paper weights, N >= {MIN_BUILDINGS_PER_DONG}):")
    for bjd, d in ranked[-5:]:
        name = d["legal_dong_name"] or bjd[5:]
        print(f"    {name:8s} (bjd={bjd}, n={d['n_buildings']:>5}): "
              f"score={d['safety_score_paper']:>6.2f}  "
              f"shelter={d['shelter_pct']:>5.1f}%  "
              f"transit={d['transit_pct']:>5.1f}%  "
              f"park={d['park_pct']:>5.1f}%")

    # Persist
    out_dir = ROOT / "backend" / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"dong_scores_{region}_{stamp}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "region": region,
            "n_legal_dongs": len(ranked),
            "weights_paper": PAPER_WEIGHTS,
            "weights_ahp": AHP_WEIGHTS,
            "dongs": dong_scores,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  Saved → {out}")
    return dong_scores


if __name__ == "__main__":
    for region in ["yuseong", "sejong"]:
        per_dong_metrics(region)
