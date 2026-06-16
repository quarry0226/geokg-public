"""Dump the loader output (``generate_scene_data``) to compact gzipped JSON
so users can reproduce the experiments without obtaining the 1.4 GB raw KAIS
SHP / XLSX datasets.

The pipeline normally is:

    raw KAIS SHP/XLSX (~1.4 GB)
        → backend/data/seed_data.generate_scene_data(region)
            → in-memory dict {buildings, parcels, roads, ...}
                → Builder pushes to Neo4j
                    → rule engine generates relationships

This script captures the in-memory dict and writes one ``.json.gz`` per
region. Downstream users can later call ``load_processed(region)`` (defined
at the bottom of this file) to obtain the same dict without reading any
SHP.

Output layout::

    data/processed/
        yuseong.json.gz         (≈ 11 MiB)
        sejong.json.gz          (≈ 30 MiB)
        README.md

Usage::

    python scripts/dump_processed_data.py            # dump both regions
    python scripts/dump_processed_data.py yuseong    # dump one region
"""
from __future__ import annotations

import gzip
import io
import json
import sys
import time
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def dump_region(region: str) -> Path:
    from backend.data.seed_data import generate_scene_data

    print(f"\n[dump] ═══ {region} — running loaders ═══")
    t0 = time.time()
    payload = generate_scene_data(region=region)
    elapsed = time.time() - t0
    print(f"[dump] loaders finished in {elapsed:.1f}s")

    payload.pop("_timing", None)

    out_path = OUT_DIR / f"{region}.json.gz"
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    raw_bytes = serialized.encode("utf-8")
    with gzip.open(out_path, "wb", compresslevel=9) as f:
        f.write(raw_bytes)

    raw_mb = len(raw_bytes) / 1024 / 1024
    gz_mb = out_path.stat().st_size / 1024 / 1024
    print(f"[dump] wrote {out_path}")
    print(f"[dump]   plain : {raw_mb:.1f} MiB")
    print(f"[dump]   gzip  : {gz_mb:.1f} MiB ({gz_mb/raw_mb*100:.1f}% of plain)")
    return out_path


def load_processed(region: str) -> dict:
    """Reload a previously dumped region payload.

    Drop-in replacement for ``backend.data.seed_data.generate_scene_data``
    when the user has only the processed dump and not the raw SHP/XLSX.
    """
    path = OUT_DIR / f"{region}.json.gz"
    if not path.exists():
        raise FileNotFoundError(
            f"Processed dump not found: {path}\n"
            f"Either:\n"
            f"  1. Run `python scripts/dump_processed_data.py {region}` "
            f"     (requires raw KAIS data per docs/DATA_DOWNLOAD.md), or\n"
            f"  2. Download the dump from the GitHub Releases page."
        )
    with gzip.open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["yuseong", "sejong"]
    paths = []
    for r in targets:
        if r not in ("yuseong", "sejong"):
            raise SystemExit(f"Unknown region: {r}")
        paths.append(dump_region(r))

    print("\n[dump] ═══ Summary ═══")
    total_mb = 0
    for p in paths:
        mb = p.stat().st_size / 1024 / 1024
        total_mb += mb
        print(f"  {mb:>7.2f} MiB  {p.relative_to(ROOT)}")
    print(f"  ──────────────")
    print(f"  {total_mb:>7.2f} MiB  TOTAL")


if __name__ == "__main__":
    main()
