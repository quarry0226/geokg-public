"""
GeoKG-Guided Digital Twin System - Main FastAPI Application.

Implements the prototype system from:
"Geographic knowledge graph-guided twin modeling method for complex city scene"
"""

import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.api.geokg_routes import router as geokg_router
from backend.api.scene_routes import router as scene_router
from backend.api.ws_routes import router as ws_router
from backend.api.kg_analysis_routes import router as kg_analysis_router
from backend.db.neo4j_client import db, set_region, get_region, _db_name_for
from backend.geokg.builder import GeoKGBuilder
from backend.geokg.dynamic_update import dynamic_engine
from backend.data.seed_data import generate_scene_data
from backend.config import REGION_CONFIG

_update_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB, seed data, start dynamic updates."""
    global _update_task

    # 1. Ensure database exists and initialize schema
    print("[GeoKG] Initializing database...")
    try:
        db.ensure_database()
    except Exception as e:
        print(f"[GeoKG] Warning: Could not create database (may need Enterprise edition): {e}")
        print("[GeoKG] Continuing with default database...")

    builder = GeoKGBuilder()
    builder.initialize()

    # 2. Check if data exists, seed if empty
    stats = db.stats()
    if stats["node_count"] == 0:
        print("[GeoKG] Seeding sample data...")
        scene_data = generate_scene_data(region=os.environ.get("GEOKG_REGION", "yuseong"))
        builder.build_from_data(scene_data)
        stats = db.stats()
        print(f"[GeoKG] Seeded {stats['node_count']} nodes, {stats['relationship_count']} relationships")
    else:
        print(f"[GeoKG] Database already has {stats['node_count']} nodes")

    # 3. Start dynamic update engine
    print("[GeoKG] Starting dynamic update engine...")
    _update_task = asyncio.create_task(dynamic_engine.start())

    yield

    # Shutdown
    dynamic_engine.stop()
    if _update_task:
        _update_task.cancel()
    print("[GeoKG] Shutdown complete.")


app = FastAPI(
    title="GeoKG Digital Twin System",
    description="Geographic Knowledge Graph-guided twin modeling for complex city scenes",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Region selection: each request may carry ?region=... or X-Region header.
# The middleware writes the value into a ContextVar that the Neo4j client reads
# to route queries to the per-region database (geokg / geokg-sejong / ...).
@app.middleware("http")
async def region_middleware(request: Request, call_next):
    region = (
        request.query_params.get("region")
        or request.headers.get("X-Region")
        or ""
    ).lower().strip()
    if region:
        set_region(region)
    response = await call_next(request)
    response.headers["X-Region"] = get_region()
    return response


# API routes
app.include_router(geokg_router)
app.include_router(scene_router)
app.include_router(ws_router)
app.include_router(kg_analysis_router)

# Serve frontend static files
app.mount("/css", StaticFiles(directory="frontend/css"), name="css")
app.mount("/js", StaticFiles(directory="frontend/js"), name="js")

# Serve 3D Tiles per region. Legacy /tiles points at the original Yuseong path
# for backward compatibility; new mounts expose each region under
# /tiles_<region>/tileset.json.
def _safe_mount_tiles(url_path: str, fs_path: str, name: str):
    if fs_path and os.path.isdir(fs_path):
        app.mount(url_path, StaticFiles(directory=fs_path), name=name)
        print(f"[main] Mounted {url_path} -> {fs_path}")
    else:
        print(f"[main] Skipped {url_path}: directory not found ({fs_path})")


for _rname, _rcfg in REGION_CONFIG.items():
    _safe_mount_tiles(f"/tiles_{_rname}", _rcfg.get("tileset_dir", ""), f"tiles_{_rname}")


# ── Multi-region info endpoints ───────────────────────────────────────
@app.get("/api/regions")
def list_regions():
    """List available regions with viewer config for the frontend selector."""
    out = []
    label_map = {"yuseong": "유성구 (Yuseong-gu, Daejeon)", "sejong": "세종시 (Sejong)"}
    for rname, rcfg in REGION_CONFIG.items():
        tileset_dir = rcfg.get("tileset_dir") or ""
        tileset_url = (
            f"/tiles_{rname}/tileset.json"
            if tileset_dir and os.path.isdir(tileset_dir)
            else None
        )
        out.append({
            "id": rname,
            "label": label_map.get(rname, rname.title()),
            "center": {
                "lon": rcfg.get("center_lon"),
                "lat": rcfg.get("center_lat"),
                "alt": rcfg.get("center_alt", 4000),
            },
            "tileset_url": tileset_url,
            "neo4j_db": _db_name_for(rname),
        })
    return {"regions": out, "active": get_region()}


@app.get("/api/regions/active")
def active_region():
    """Return active region (resolved from ?region= or middleware default)."""
    return {"region": get_region(), "neo4j_db": _db_name_for(get_region())}


@app.get("/")
async def serve_index():
    return FileResponse("frontend/index.html")


@app.get("/visual_validation")
async def serve_visual_validation():
    return FileResponse("frontend/visual_validation.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "database": db.stats()}


# CLI entry point for seeding
@app.get("/api/reseed")
def reseed():
    """Force re-seed the database (clears existing data). Returns timing report.

    Region-aware: ``generate_scene_data(region=...)`` dispatches to the
    same builder pipeline for either Yuseong (default) or Sejong, using
    the unified region-agnostic seeder.
    """
    import time as _time
    import json as _json
    import os as _os

    region = get_region() or "yuseong"

    t_start = _time.time()
    builder = GeoKGBuilder()
    builder.clear()
    builder.initialize()
    scene_data = generate_scene_data(region=region)
    build_timing = builder.build_from_data(scene_data)
    total_elapsed = _time.time() - t_start

    # Save timing report to JSON file for experiments
    report_dir = _os.path.join(_os.path.dirname(__file__), "experiments")
    _os.makedirs(report_dir, exist_ok=True)
    report = {
        "region": region,
        "total_elapsed": round(total_elapsed, 3),
        "stats": db.stats(),
        "timing": build_timing,
    }
    report_path = _os.path.join(report_dir, f"last_reseed_timing_{region}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        _json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"[GeoKG] Timing report saved to {report_path}")

    return {
        "status": "reseeded",
        "region": region,
        "total_elapsed": round(total_elapsed, 2),
        **db.stats(),
    }
