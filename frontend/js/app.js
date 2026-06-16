/**
 * App - Main application entry point.
 * Initializes all modules and wires them together.
 */

// ===== Region bootstrap (must run before any /api/ fetch) =====
// Reads ?region= from URL (default: yuseong), exposes window.REGION,
// and monkey-patches window.fetch so every /api/* request carries
// the region as a query parameter — enabling per-region Neo4j routing.
const REGION_DEFAULTS = {
    yuseong: { lon: 127.341, lat: 36.369, alt: 4000, label: '유성구 (Yuseong)' },
    sejong:  { lon: 127.288, lat: 36.480, alt: 4000, label: '세종시 (Sejong)'  },
};
const REGION = (new URLSearchParams(window.location.search).get('region') || 'yuseong').toLowerCase();
window.REGION = REGION;
window.REGION_CENTER = REGION_DEFAULTS[REGION] || REGION_DEFAULTS.yuseong;

(function _patchFetch() {
    const _origFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
        try {
            let url = (typeof input === 'string') ? input : (input && input.url) || '';
            if (url && (url.startsWith('/api/') || url.startsWith('api/'))) {
                // Don't double-add region if it's already present
                if (!/[?&]region=/.test(url)) {
                    const sep = url.includes('?') ? '&' : '?';
                    url = url + sep + 'region=' + encodeURIComponent(REGION);
                }
                if (typeof input === 'string') {
                    return _origFetch(url, init);
                } else {
                    return _origFetch(new Request(url, input), init);
                }
            }
        } catch (e) { /* fall through */ }
        return _origFetch(input, init);
    };
})();

// Sync the region selector dropdown with the active region
document.addEventListener('DOMContentLoaded', () => {
    const sel = document.getElementById('region-select');
    if (!sel) return;
    sel.value = REGION;
    sel.addEventListener('change', () => {
        const next = sel.value;
        if (next && next !== REGION) {
            const url = new URL(window.location.href);
            url.searchParams.set('region', next);
            window.location.href = url.toString();
        }
    });
});

(async function () {
    // ===== Initialize Cesium =====
    const cm = new CesiumManager('cesium-container');
    cm.init(null);

    // Set initial camera position from region defaults
    const _C = window.REGION_CENTER;
    cm.viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(_C.lon, _C.lat, _C.alt),
        orientation: {
            heading: Cesium.Math.toRadians(0),
            pitch: Cesium.Math.toRadians(-45),
            roll: 0,
        },
    });

    // ===== Initialize Scene Loader =====
    const sceneLoader = new SceneLoader(cm);

    // ===== Initialize KG Panel =====
    const kgPanel = new GeoKGPanel(cm);
    kgPanel.init();

    // ===== Initialize Dashboard =====
    const dashboard = new DashboardPanel();
    dashboard.loadDashboard();

    // ===== Initialize KG Graph View =====
    const kgGraph = new KGGraphView('cesium-container');
    kgGraph.init();
    kgGraph.onNodeClick = (uid) => {
        cm.highlightEntity(uid);
    };

    // ===== Initialize KG Analysis =====
    kgAnalysis = new KGAnalysis(cm);
    kgAnalysis.init();

    // ===== Load 3D Scene =====
    setTimeout(async () => {
        const count = await sceneLoader.loadScene();
        console.log(`[App] Scene loaded with ${count} models`);

        // Load graph for zone position registration (no boundary polygon rendering)
        try {
            const resp = await fetch('/api/geokg/graph?limit=500');
            const graphData = await resp.json();

            // Register zone centroid positions only (no polygon overlay)
            for (const node of graphData.nodes) {
                if (node.label === 'Zone' && node.boundary) {
                    try {
                        const coords = JSON.parse(node.boundary);
                        let sumLon = 0, sumLat = 0;
                        for (const c of coords) { sumLon += c[0]; sumLat += c[1]; }
                        const cLon = sumLon / coords.length;
                        const cLat = sumLat / coords.length;
                        cm.entityPositions[node.id] = Cesium.Cartesian3.fromDegrees(cLon, cLat, 10);
                    } catch (e) {}
                }
            }
        } catch (e) {
            console.error('[App] Failed to load graph:', e);
        }

        // Spatial relationship lines removed from 3D map (available in KG Graph view)

        // Fly to the active region's center
        setTimeout(() => cm.flyToCenter(_C.lon, _C.lat, _C.alt), 500);
    }, 1500);

    // ===== Initialize WebSocket =====
    const ws = new WSClient();
    ws.onUpdate = (event) => {
        sceneLoader.applyDynamicUpdate(event);
        dashboard.updateFromEvent(event);
    };
    ws.connect();

    // ===== Entity click handler =====
    cm.onEntityClick = (uid, label) => {
        // Check if KG Analysis pick mode is active (From/To selector)
        if (kgAnalysis.onEntityPick(uid, label)) return;

        const items = document.querySelectorAll('.entity-item');
        items.forEach(item => {
            item.classList.toggle('selected', item.dataset.uid === uid);
        });

        // Show connected entities on 3D map
        cm.showConnections(uid);

        // Load entity info in panel
        kgPanel._loadEntityInfo(uid);
    };

    // ===== 3D Tile click handler - find nearest building in KG =====
    cm.onTileClick = async (lon, lat) => {
        try {
            const resp = await fetch(`/api/kg/nearest_building?lon=${lon}&lat=${lat}&radius_m=100`);
            const data = await resp.json();
            if (data.found) {
                cm.highlightBuildingAt(
                    data.properties.longitude,
                    data.properties.latitude,
                    data.properties.height,
                    data.properties
                );
                console.log(`[App] Building found: ${data.name} (${data.dist_m}m away)`);
            }
        } catch (e) {
            console.error('[App] Failed to lookup building:', e);
        }
    };

    // ===== Toolbar handlers =====
    const _resetBtn = document.getElementById('btn-reset-view');
    if (_resetBtn) {
        _resetBtn.addEventListener('click', () => {
            cm.flyToCenter(_C.lon, _C.lat, _C.alt);
        });
    }

    // Layer toggle buttons
    const layerButtons = {
        'btn-toggle-buildings': 'Building',
        'btn-toggle-roads': 'Road',
        'btn-toggle-vehicles': 'Vehicle',
        'btn-toggle-sensors': 'Sensor',
        'btn-toggle-cameras': 'Camera',
        'btn-toggle-trees': 'Tree',
    };

    for (const [btnId, label] of Object.entries(layerButtons)) {
        document.getElementById(btnId).addEventListener('click', (e) => {
            const btn = e.target;
            btn.classList.toggle('active');
            const visible = btn.classList.contains('active');
            // Buildings use KG-driven color overlays only
            if (label === 'Building') {
                cm.toggleBuildingOverlays(visible);
            } else {
                cm.setLayerVisibility(label, visible);
            }
        });
    }

    // ThingsAddr (사물주소) master toggle
    document.getElementById('btn-toggle-iot').addEventListener('click', (e) => {
        const btn = e.target;
        btn.classList.toggle('active');
        cm.setLayerVisibility('ThingsAddr', btn.classList.contains('active'));
    });

    // ThingsAddr sub-type toggle buttons (사물유형별 필터)
    document.querySelectorAll('.btn-iot-sub').forEach(btn => {
        btn.addEventListener('click', () => {
            btn.classList.toggle('active');
            const iotType = btn.dataset.iotType;
            const visible = btn.classList.contains('active');
            cm.setIoTSubTypeVisibility(iotType, visible);
        });
    });

    // Parcels toggle: shows/hides cadastral parcel boundaries on the 3D map
    document.getElementById('btn-toggle-parcels').addEventListener('click', (e) => {
        const btn = e.target;
        btn.classList.toggle('active');
        cm.toggleParcelOverlays(btn.classList.contains('active'));
    });

    // Relations toggle removed (spatial lines no longer rendered on 3D map)

    // KG Graph overlay toggle
    document.getElementById('btn-kg-graph').addEventListener('click', (e) => {
        const btn = e.target;
        btn.classList.toggle('active');
        kgGraph.toggle();
    });

    // Reseed button
    document.getElementById('btn-reseed').addEventListener('click', async () => {
        if (!confirm('This will delete all data and reseed. Continue?')) return;
        try {
            const resp = await fetch('/api/reseed');
            const result = await resp.json();
            alert(`Reseeded: ${result.node_count} nodes, ${result.relationship_count} relationships`);
            window.location.reload();
        } catch (err) {
            alert('Reseed failed: ' + err.message);
        }
    });

    // Use Cases modal
    const ucModal = document.getElementById('use-cases-modal');
    document.getElementById('btn-use-cases').addEventListener('click', () => {
        document.getElementById('kg-analysis-modal').classList.add('hidden');
        ucModal.classList.remove('hidden');
    });
    document.getElementById('use-cases-close').addEventListener('click', () => {
        ucModal.classList.add('hidden');
    });
    ucModal.addEventListener('click', (e) => {
        if (e.target === ucModal) ucModal.classList.add('hidden');
    });
    ucModal.querySelectorAll('.uc-accordion-header').forEach(header => {
        header.addEventListener('click', () => {
            header.parentElement.classList.toggle('expanded');
        });
    });

    // Panel toggle buttons
    document.getElementById('btn-toggle-kg').addEventListener('click', () => {
        document.getElementById('kg-panel').classList.toggle('collapsed');
    });

    document.getElementById('btn-toggle-dash').addEventListener('click', () => {
        document.getElementById('dashboard-panel').classList.toggle('collapsed');
    });

    // Refresh dashboard periodically
    setInterval(() => {
        dashboard.loadDashboard();
        kgPanel.loadStats();
    }, 10000);

    console.log('[App] GeoKG Digital Twin System initialized');
})();
